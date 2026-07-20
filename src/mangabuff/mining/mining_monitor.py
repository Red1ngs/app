from __future__ import annotations

from dataclasses import dataclass
from logging import Logger
from typing import TYPE_CHECKING, Any, Optional, cast

from src.core.monitoring.looping_monitor import LoopingMonitor
from src.core.logging.loggers import get_account_logger
from src.utils.time import is_today

if TYPE_CHECKING:
    from src.core.runtime.scheduler import EventDrivenScheduler
    from src.core.core_account import Account
    
    
# ─────────────────────────────────────────────────────────────────────────────
# MiningParams & PurchasePlan
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MiningParams:
    exchange: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "exchange": self.exchange,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MiningParams":
        return cls(
            exchange = bool(d.get("exchange", True))
        )


@dataclass
class PurchasePlan:
    """Описує заплановану покупку або обмін."""
    intent: str          # "upgrade_pickaxe", "buy_power" або "exchange_diamonds"
    name: str            # Зрозуміла назва для логування
    cost: int            # Загальна вартість у руді
    quantity: int = 1    # Кількість для купівлі
    has_enough: bool = False  # Чи вистачає руди на цю покупку


class MiningMonitor(LoopingMonitor):
    """
    Монітор Шахти. Веде повний цикл mine_hit для одного акаунта.

    Не містить IO — тільки планування ask().
    Стан (hits_count, )
    читається з MiningInventory при кожному циклі.
    """

    @property
    def monitor_id(self) -> str:
        return "mining"

    def __init__(self) -> None:
        super().__init__()
        self._need_update:        bool                             = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def attach(self, scheduler: "EventDrivenScheduler", account_id: str) -> None:
        self.account_id = account_id
        self.scheduler  = scheduler
        
        scheduler.subscribe("daily.claimed", self._on_daily_claimed)
        scheduler.subscribe("mining.mining_complete", self._on_mining_complete)

        # Гарантуємо порядок "спочатку денний бонус, потім інші процеси":
        # якщо на акаунті підключено профессію "daily" і бонус за сьогодні
        # ще не зібрано — НЕ стартуємо шахту одразу, а чекаємо подію
        # "daily.claimed" (підписку вище) — саме вона й викличе
        # _start_mining(). Якщо ж бонус уже зібрано (або "daily" на
        # акаунті взагалі немає) — стартуємо без затримки, як і раніше.
        if self._daily_bonus_ready():
            self.log.info("[MiningMonitor] Ініціалізація, початок")
            await self._start_mining()
            await self._schedule_next()  # Запускаємо цикл після підписки на події
        else:
            self.log.info(
                "[MiningMonitor] Денний бонус ще не зібрано → відкладаємо старт шахти "
                "до daily.claimed"
            )

    async def detach(self, scheduler: "EventDrivenScheduler", account_id: str) -> None:
        self._stop_loop()
        self._scheduler = None

    # ── Scheduling ────────────────────────────────────────────────────────────
    #
    # Власне планування (delay/скасування/try-except) винесено у
    # LoopingMonitor. Тут лишається тільки те, що специфічне для mining:
    # яка дія відбувається на пробудженні і яка затримка до нього.

    async def _run_cycle(self) -> None:
        await self._send_ask()

    async def _interval(self) -> float:
        bot = self.bot
        cfg = bot.app_config.mining

        if not await self._mining_complete():
            return cfg.delay

        return -1.0

    def _loop_logger(self) -> "Logger":
        return get_account_logger(self._account_id)

    # ── Daily guard ───────────────────────────────────────────────────────────

    def _daily_bonus_ready(self) -> bool:
        """
        True — можна (далі) працювати з шахтою: або на акаунті взагалі
        немає профессії "daily" (нема на що чекати), або денний бонус на
        сьогодні вже зібрано.
        False — на акаунті є "daily", але бонус за сьогодні ще не
        зібрано → чекаємо подію "daily.claimed" (див. _on_daily_claimed).

        (Раніше метод називався _waiting_for_daily і мав протилежну за
        змістом назву при тій самій поведінці для випадку "daily
        зібрано", але помилково повертав False для акаунтів БЕЗ
        профессії "daily" — через що _send_ask() і виклик у attach()
        блокували шахту назавжди на таких акаунтах. Тепер обидва місця
        викликають саме цей метод з правильною семантикою.)
        """
        scheduler = self.scheduler
        bot       = self.bot

        if not scheduler.has_profession(self._account_id, "daily"):
            return True

        daily = bot.inventory.daily
        return is_today(daily.last_daily_claimed)
        

    # ── Ask ───────────────────────────────────────────────────────────────────
    
    async def _start_mining(self) -> None:
        log = self.log
        scheduler = self.scheduler

        log.info("[MiningMonitor] → ask start_mining (ініціалізуємо шахту на новий день)")
        result = await scheduler.ask(
            account_id    = self._account_id,
            profession_id = "mining",
            intent        = "start_mining",
            caller        = "mining_monitor",
        )
        
        if not result.approved:
            log.warning(f"[MiningMonitor] start_mining відхилено: {result.reason}")
            return
        
        result_data = result.data
        self._update_inventory(result_data)
        
        # Плануємо наступний удар
        await self._schedule_next()

    def _get_purchase_plan(self) -> Optional[PurchasePlan]:
        """
        Аналізує інвентар та визначає першочергову потребу в покупці/обміні.
        Повертає PurchasePlan або None, якщо купувати нічого не потрібно.
        """
        bot = self.bot
        log = self.log
        inv = bot.inventory.mining
        
        ore = int(inv.ore)
        cost_of_one_diamond = inv.exchange_diamond_cost

        # 1. Покращення кирки
        if not inv.upgrade_max and inv.upgrade_cost is not None:
            return PurchasePlan(
                intent="upgrade_pickaxe",
                name="покращення кирки",
                cost=inv.upgrade_cost,
                quantity=1,
                has_enough=(ore >= inv.upgrade_cost)
            )

        # 2. Сильний удар
        if not inv.power_bought and inv.power_cost is not None:
            return PurchasePlan(
                intent="buy_power",
                name="сильний удар",
                cost=inv.power_cost,
                quantity=1,
                has_enough=(ore >= inv.power_cost)
            )

        # 3. Обмін на алмази
        if cost_of_one_diamond and ore >= cost_of_one_diamond:
            to_buy = ore // cost_of_one_diamond
            cost = to_buy * cost_of_one_diamond

            if to_buy > 0:
                log.info(f"[MiningMonitor] → ask exchange_diamonds: купуємо {to_buy} 💎, за {cost} руди, залишок - {ore - cost}")
                
                return PurchasePlan(
                    intent="exchange_diamonds",
                    name="обмін на алмази",
                    cost=cost,
                    quantity=to_buy,
                    has_enough=True
                )
        else:
            log.info(f"[MiningMonitor] → ask exchange_diamonds: не вистачає руди для обміну або ціна не визначена")
            return None

        return None
        
    async def _sale_ore(self) -> Optional[bool]:
        log = self.log
        try:
            bot = self.bot
            inv = bot.inventory.mining

            plan = self._get_purchase_plan()

            if plan is not None:
                if not plan.has_enough:
                    log.info(
                        f"[MiningMonitor] Не вистачає руди на {plan.name}. "
                        f"Потрібно мінімум: {plan.cost}, є: {inv.ore}"
                    )
                else:
                    await self._execute_purchase(plan)
            else:
                log.info("[MiningMonitor] Всі завдання виконано, вільних алмазів для обміну немає.")

            return getattr(inv, "needs_upgrade", False)
        except ValueError as ex:
           log.error(f"Помилка при обробці продажі руди: {ex}") 
           return None

    async def _execute_purchase(self, plan: PurchasePlan) -> None:
        """Викликає відповідний метод для здійснення покупки відповідно до плану."""
        if plan.intent == "upgrade_pickaxe":
            await self._upgrade_pickaxe()
        elif plan.intent == "buy_power":
            await self._buy_power()
        elif plan.intent == "exchange_diamonds":
            await self._exchange_diamonds(plan.quantity, plan.cost)
        
    async def _upgrade_pickaxe(self) -> None:
        scheduler = self._scheduler
        if scheduler is None:
            return
        
        log = self.log
        log.info("[MiningMonitor] → ask upgrade_pickaxe")
        
        result = await scheduler.ask(
            account_id    = self._account_id,
            profession_id = "mining",
            intent        = "upgrade_pickaxe",
            caller        = "mining_monitor",
        )
        
        if result.approved:
            log.info("[MiningMonitor] Кирку успішно покращено.")
            self._update_inventory(result.data)
        else:
            log.warning(f"[MiningMonitor] Відхилено покращення кирки: {result.reason}")
        
    async def _buy_power(self) -> None:
        scheduler = self._scheduler
        if scheduler is None:
            return
        
        log = self.log
        log.info("[MiningMonitor] → ask buy_power")
        
        result = await scheduler.ask(
            account_id    = self._account_id,
            profession_id = "mining",
            intent        = "buy_power",
            caller        = "mining_monitor",
        )
        
        if result.approved:
            log.info("[MiningMonitor] Сильний удар успішно куплено.")
            self._update_inventory(result.data)
        else:
            log.warning(f"[MiningMonitor] Відхилено купівлю сильного удару: {result.reason}")

    async def _exchange_diamonds(self, to_buy: int, cost: int) -> None:
        scheduler = self.scheduler
        
        log = self.log
        
        result = await scheduler.ask(
            account_id    = self._account_id,
            profession_id = "mining",
            intent        = "exchange_diamonds",
            caller        = "mining_monitor",
            data          = {"diamonds": to_buy}, 
        )
        
        if result.approved:
            log.info(f"[MiningMonitor] Успішно обміняно руду на {to_buy} 💎.")
            self._update_inventory(result.data)
        else:
            log.warning(f"[MiningMonitor] Відхилено обмін руди: {result.reason}")

    async def _send_ask(self) -> None:
        """
        Надсилає ask("mining", "mining_hit") для здійснення чергового удару в шахті.
        """
        scheduler = self.scheduler
        bot = self.bot
        log = self.log

        inv = bot.inventory.mining

        # 1. ЗАПОБІЖНИК: якщо кількість ударів не визначена (None), це вказує на те,
        # що шахту ще не розпочато на сьогодні (наприклад, після перезапуску бота).
        # Запускаємо _start_mining() для ініціалізації.
        if inv.hits_left is None:
            log.info("[MiningMonitor] hits_left не визначено (шахту не активовано) → запускаємо _start_mining")
            await self._start_mining()
            return

        await self._sale_ore()
        
        if not self._daily_bonus_ready():
            log.info("[MiningMonitor] очікуємо збору денного бонусу (daily.claimed)")
            return
        
        if await self._mining_complete():
            log.info("[MiningMonitor] Шахта видобута — чекаємо daily.claimed")
            return

        params = self._mining_params()

        log.info(
            f"[MiningMonitor] → ask mining_hit "
            f"exchange={params.exchange!r}"
        )
        
        result = await scheduler.ask(
            account_id    = self._account_id,
            profession_id = "mining",
            intent        = "mining_hit",
            caller        = "mining_monitor",
        )
        
        result_data = result.data
        self._update_inventory(result_data)

        if not result.approved:
            log.warning(f"[MiningMonitor] mining_hit відхилено: {result.reason}")
            if not await self._mining_complete():
                await self._schedule_next()
            return

        if not await self._mining_complete():
            await self._schedule_next()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _mining_params(self) -> MiningParams:
        try:
            bot = self.bot  
            inv = bot.inventory.mining
            raw = inv.mining_params
            return MiningParams.from_dict(raw) if raw else MiningParams()
        except ValueError:
            return MiningParams()

    async def _mining_complete(self) -> Optional[bool]:
        scheduler = self.scheduler
        bot = self.bot
        inv = bot.inventory.mining
        hits_left = inv.hits_left
        if hits_left is None:
            return None
        if hits_left > 0:
            inv.mining_complete = False
            return False
        elif hits_left == 0:
            if not inv.mining_complete:
                inv.mining_complete = True
                inv.hits_left = 0
                await scheduler.emit_event(
                    "mining.mining_complete",
                    {"account_id": self._account_id},
                    source=self._account_id,
                )
                await self._persist_inventory(bot)
            return True
        else:
            raise ValueError(f"hits_left не може бути {hits_left}")
        
    def _update_inventory(self, data: dict[str, Any]) -> None:
        """Оновлює локальний інвентар шахти на основі отриманих від ask() даних."""
        bot = self.bot
        if not data:
            return
        
        inv = bot.inventory.mining
        
        if "hits_left" in data:
            inv.hits_left = cast(int, data["hits_left"])
        if "max_hits" in data:
            inv.max_hits = cast(int, data["max_hits"])
        if "ore" in data:
            inv.ore = cast(int, data["ore"])
        if "upgrade_cost" in data:
            inv.upgrade_cost = cast(Optional[int], data["upgrade_cost"])
        if "upgrade_level" in data:
            inv.upgrade_level = cast(Optional[int], data["upgrade_level"])
        if "upgrade_max" in data:
            inv.upgrade_max = cast(bool, data["upgrade_max"])
        if "power_cost" in data:
            inv.power_cost = cast(Optional[int], data["power_cost"])
        if "power_bought" in data:
            inv.power_bought = cast(bool, data["power_bought"])
        if "exchange_diamond_cost" in data:
            inv.exchange_diamond_cost = cast(Optional[int], data["exchange_diamond_cost"])

        if inv.hits_left is not None:
            inv.mining_complete = (inv.hits_left == 0)
    
    
    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_daily_claimed(self, payload: dict[str, Any]) -> None:
        log = self.log
        if payload.get("account_id") != self._account_id:
            return    
        
        self._need_update = True
        log.info("[MiningMonitor] daily.claimed → ініціалізуємо нову шахту через _start_mining")
        
        # Замість простого планування hits_hit, ми безпосередньо викликаємо старт шахти
        await self._start_mining()
        
    async def _on_mining_complete(self, payload: dict[str, Any]) -> None:
        log = self.log
        if payload.get("account_id") != self._account_id:
            return
        
        bot = self.bot     
        inv = bot.inventory.mining
        
        log.info(
            f"[MiningMonitor] було успішно видобуто руди: {inv.max_hits} "
            f"→ emit mining.mining_complete"
        )