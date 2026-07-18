from logging import Logger
from typing import Any, Optional

from src.core.core_account import Account
from src.core.logging.loggers import get_account_logger
from src.core.runtime.profession import BaseProfession, RequestResult
from src.core.runtime.request_router import RequestContext
from src.core.runtime.scheduler import EventDrivenScheduler
from src.mangabuff.mining.inventory import MiningInventory
from src.mangabuff.session.http_result import FailReason


class MiningProfession(BaseProfession):
    """
    Profession «Шахта».

    Чистий виконавець: отримує ask → HTTP → inventory → emit.
    Не знає про таймінги, не планує наступний запуск, не скидає лічильник.
    """

    def __init__(self) -> None:
        self._account_id: str                              = ""
        self._inv:        Optional[MiningInventory]        = None
        self._scheduler:  Optional["EventDrivenScheduler"] = None

    @property
    def profession_id(self) -> str:
        return "mining"

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def setup(self, scheduler: "EventDrivenScheduler", account_id: str) -> None:
        self._account_id = account_id
        self._scheduler  = scheduler

    async def restore_state(self, bot: "Account") -> None:
        inv: MiningInventory = bot.inventory.mining
        self._inv = inv

        get_account_logger(self._account_id).info(
            f"MiningProfession відновлено: "
            f"mining_complete={inv.mining_complete!r} "
        )

    async def teardown(self, scheduler: "EventDrivenScheduler", account_id: str) -> None:
        self._scheduler = None

    def check_guard(self, bot: "Account") -> bool:
        return not bool(bot.inventory.personal.is_banned)

    # ── handle_request ────────────────────────────────────────────────────────

    async def handle_request(
        self,
        intent: str,
        data:   dict[str, Any],
        ctx:    "RequestContext",
    ) -> RequestResult:
        log = get_account_logger(self._account_id)
        try:
            # 1. Валідація системних об'єктів
            if self._scheduler is None:
                raise ValueError("Scheduler не доступний")
            
            bot = self._scheduler.get_bot(ctx.account_id)
            if bot is None:
                raise ValueError(f"Бот для акаунта {ctx.account_id} не знайдений")
            
            if ctx.account_id != self._account_id:
                raise ValueError(
                    f"account_id {ctx.account_id} не збігається з {self._account_id}"
                )
            
            if intent == "start_mining":
                return await self._handle_start_mining(bot, log)
            elif intent == "mining_hit":
                return await self._handle_mining_hit(bot, log)
            elif intent == "upgrade_pickaxe":
                return await self._handle_upgrade_pickaxe(bot, log)
            elif intent == "mining_buy_strong_hit":
                return await self._handle_buy_strong_hit(bot, log)
            elif intent == "mining_exchange":
                return await self._handle_mining_exchange(data, bot, log)

            return RequestResult.deny(f"unknown intent: {intent!r}")
        
        except Exception as exc:
            log.exception(f"{intent}: критична помилка")
            return RequestResult.deny(f"Помилка {intent} : {exc}")

    async def _handle_start_mining(self, bot: "Account", log: "Logger") -> RequestResult:
        cfg = bot.app_config.mining
            
        # 2. Виконання запиту
        log.info("📝 Mining: надсилаємо запит на початок копання…")
        result = await bot.safe_session.mine(self._account_id,cfg)

        if not result.ok:
            log.warning(f"⚠️ /mine провалився: {result.reason}")
            return RequestResult.deny(str(result.reason))

        data = result.data
        if data is None:
            raise ValueError("API повернуло порожні дані (data is None)")

        # 3. Перевірка обов'язкових полів
        # Використовуємо set для перевірки наявності ключів
        required_keys = {"hits_left", "ore", "max_hits", "upgrade_cost", "upgrade_level", "upgrade_max", "power_cost", "power_bought", "exchange_diamond_cost"}
        missing = [k for k in required_keys if k not in data]
        if missing:
            raise ValueError(f"API не повернуло обов'язкові поля: {', '.join(missing)}")
            
        log.info(f"⛏️ Майнінг статус: Ударів: {data["hits_left"]}, Руди: {data["ore"]}")
                
        return RequestResult.approve(data=data)
        
    async def _handle_mining_hit(self, bot: "Account", log: "Logger") -> RequestResult:
        # 1. Виконання запиту
        cfg = bot.app_config.mining
        result = await bot.safe_session.mine_hit(cfg)

        if not result.ok:
            if result.reason == FailReason.LIMIT_EXHAUSTED:
                # локальний лічильник ударів розійшовся з реальним станом на сайті (403 «Лимит
                # ударов на сегодня исчерпан»). Це не помилка виконання —
                # примусово фіксуємо hits_left=0 в інвентарі, щоб
                # MiningMonitor коректно зупинив цикл до daily.claimed.
                log.warning(
                    "⚠️ Розбіжність лічильника ударів із сайтом — "
                    "ліміт на сьогодні вже вичерпано, hits_left=0"
                )
                inv: MiningInventory = bot.inventory.mining
                inv.hits_left = 0
                inv.mining_complete = True
                return RequestResult.deny(
                    "Ліміт ударів вичерпано (розбіжність з сайтом)",
                    data={"hits_left": 0},
                )
            log.warning(f"⚠️ /mining провалився: {result.reason}")
            return RequestResult.deny(str(result.reason))

        data = result.data
        if data is None:
            raise ValueError("Отримано порожні дані від сервера (data is None)")

        # 3. Валідація отриманих даних
        # Складаємо список ключів, які ми очікуємо отримати
        required_fields = ["hits_left", "hits_used", "ore", "added"]
        missing_fields = [field for field in required_fields if data.get(field) is None]
            
        if missing_fields:
            raise ValueError(f"Відсутні обов'язкові поля в API: {', '.join(missing_fields)}, data={data}")

        return RequestResult.approve(data=data)
        
    async def _handle_upgrade_pickaxe(self, bot: "Account", log: "Logger") -> RequestResult:  
        cfg = bot.app_config.mining
            
        # 2. Виконання запиту
        log.info("📝 Mining: надсилаємо запит на покращення кайлу…")
        result = await bot.safe_session.upgrade_pickaxe(cfg)

        if not result.ok:
            log.warning(f"⚠️ /mine/upgrade провалився: {result.reason}")
            raise ValueError(f"Помилка покращення кайлу: {result.reason}")
            
        data = result.data
        if data is None:
            raise ValueError("API повернуло порожні дані (data is None)")
            
        # 3. Перевірка обов'язкових полів
        # Використовуємо set для перевірки наявності ключів
        required_keys = {"ore", "pickaxe_level", "message"}
        missing = [k for k in required_keys if k not in data]
        if missing:
            raise ValueError(f"API не повернуло обов'язкові поля: {', '.join(missing)}, data={data}")
         
        return RequestResult.approve(data=data)
        
    async def _handle_buy_strong_hit(self, bot: "Account", log: "Logger") -> RequestResult:
        cfg = bot.app_config.mining
            
        # 2. Виконання запиту
        log.info("📝 Mining: надсилаємо запит на покупку сильного удару…")
        result = await bot.safe_session.buy_strong_hit(cfg)

        if not result.ok:
            log.warning(f"⚠️ /mine/buy-strong-hit провалився: {result.reason}")
            raise ValueError(f"Помилка покупки сильного удару: {result.reason}")
            
        data = result.data
        if data is None:
            raise ValueError("API повернуло порожні дані (data is None)")
            
        # 3. Перевірка обов'язкових полів
        # Використовуємо set для перевірки наявності ключів
        required_keys = {"ore", "pickaxe_level", "message"}
        missing = [k for k in required_keys if k not in data]
        if missing:
            raise ValueError(f"API не повернуло обов'язкові поля: {', '.join(missing)}, data={data}")
         
        return RequestResult.approve(data=data)
        
    async def _handle_mining_exchange(self, data: dict[str, Any], bot: "Account", log: "Logger") -> RequestResult:
        diamonds = data.get("diamonds")
        if diamonds is None:
            raise ValueError("Не вказано кількість діамантів для обміну")
            
        cfg = bot.app_config.mining
            
        # 2. Виконання запиту
        log.info("📝 Mining: надсилаємо запит на обмін руди на діаманти…")
        result = await bot.safe_session.exchange_ore(cfg, diamonds=diamonds)

        if not result.ok:
            log.warning(f"⚠️ /mine/exchange провалився: {result.reason}")
            raise ValueError(f"Помилка обміну руди на діаманти: {result.reason}")
                
        result_data = result.data
        if result_data is None:
            raise ValueError("API повернуло порожні дані (data is None)")
            
        # 3. Перевірка обов'язкових полів
        # Використовуємо set для перевірки наявності ключів
        required_keys = {"ore", "diamonds", "message"}
        missing = [k for k in required_keys if k not in result_data]
        if missing:
            raise ValueError(f"API не повернуло обов'язкові поля: {', '.join(missing)}, data={result_data}")
         
        return RequestResult.approve(data=result_data)