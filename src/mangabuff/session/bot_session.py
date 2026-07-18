"""
src/mangabuff/session/bot_session.py

Публічний фасад — єдина точка входу для бізнес-логіки. Однакова з
попередньою версією за контрактом (ті самі методи: mine, quiz_start,
claim_daily, submit_add_history, ...), АЛЕ більше не тримає власного
HTTP/socket-клієнта — HTTP/auth/cookies/socket повністю живуть у
account-service. Кожен бізнес-метод просить account-service виконати
конкретний запит через account_client.request(...) ("порт" — сказати
акаунт-сервісу що саме зробити).

Це і є межа рефакторингу на мікросервіси: mining.py, quiz.py, daily/*,
reader/* далі викликають bot.safe_session.mine(...) / .quiz_start(...)
так само, як і раніше — жодних змін у них не потрібно.
"""
from __future__ import annotations

from typing import Any, Optional

from src.core.account_client import AccountServiceClient, RemoteResponse
from src.core.config.app import AppConfig, DailyCfg, MiningCfg, PersonalCfg, QuizCfg, ReaderAppCfg
from src.mangabuff.daily.parser import get_claimable_day
from src.mangabuff.parser import parse_main_page, parse_mining_page
from src.mangabuff.session.http_result import HttpResult, FailReason, http_call, http_success, http_fail, http_success_none
from src.utils.logging import get_logger as log


class BotSession:
    """
    Єдина точка входу для будь-якої бізнес-логіки.

    На відміну від попередньої версії — НЕ має .http/.auth/.socket/.msg.
    Замість цього тримає account_id + account_client (порт до
    account-service). user_id/user_name кешуються тут же після connect().
    """

    def __init__(
        self,
        account_id: str,
        account_client: AccountServiceClient,
        app_config: AppConfig,
        user_id: Optional[str] = None,
        user_name: Optional[str] = None,
    ) -> None:
        self.account_id = account_id
        self.account_client = account_client
        self.config = app_config
        self.user_id = user_id
        self.user_name = user_name

    async def close(self) -> None:
        """Закрити сесію на боці account-service."""
        await self.account_client.disconnect(self.account_id)
        log().info("[session] закрито")

    # ── HTTP-обгортки з автоматичним use_room ─────────────────────────────────
    #
    # priority — та сама шкала, що й раніше (core/runtime/proxy_queue.py,
    # тепер живе в account-service): AUTH / TIME_CRITICAL / CRITICAL /
    # NORMAL / BACKGROUND. AUTH зарезервовано для account-service
    # (логін/re-login) — бізнес-методи сюди НЕ передають.

    async def get(
        self, url: str, room: Optional[str] = None,
        priority: str = "NORMAL", **kw: Any,
    ) -> RemoteResponse:
        return await self.account_client.request(
            self.account_id, "GET", url, room=room, priority=priority, **kw,
        )

    async def post(
        self, url: str, room: Optional[str] = None,
        priority: str = "NORMAL", **kw: Any,
    ) -> RemoteResponse:
        return await self.account_client.request(
            self.account_id, "POST", url, room=room, priority=priority, **kw,
        )

    # ── Повідомлення ──────────────────────────────────────────────────────────

    async def open_dialog(self, user_id: int | str) -> Optional[str]:
        token = await self.account_client.open_dialog(self.account_id, str(user_id))
        if token:
            log().info(f"[session] діалог з {user_id} відкрито (token={token!r})")
        else:
            log().warning(f"[session] open_dialog({user_id}) не вдався")
        return token

    async def send_message(
        self,
        to_user_id: int | str,
        text: str,
        reply_id: Optional[int | str] = None,
        reply_text: Optional[str] = None,
    ) -> HttpResult[str]:
        r = await self.account_client.send_message(
            self.account_id, str(to_user_id), text,
            reply_id=str(reply_id) if reply_id is not None else None,
            reply_text=reply_text,
        )
        if r.status_code != 200 or not r.text.strip():
            return http_fail(FailReason.SERVER if r.status_code != 200 else FailReason.BAD_DATA)
        return http_success(r.text)

    async def mark_messages_read(
        self, dialog_token: str, last_msg_id: Optional[str] = None,
    ) -> None:
        await self.account_client.mark_messages_read(self.account_id, dialog_token, last_msg_id=last_msg_id)

    async def close_dialog(self) -> None:
        await self.account_client.close_dialog(self.account_id)
        log().info("[session] діалог закрито")

    # ── Daily ─────────────────────────────────────────────────────────────────

    @http_call
    async def fetch_daily_streak(self, daily: DailyCfg) -> HttpResult[Optional[int]]:
        url = daily.urls.balance
        r = await self.get(url, room=url, priority="CRITICAL", timeout=15)
        r.raise_for_status()
        day = get_claimable_day(
            r.text,
            item_selector=daily.item_selector,
            claim_text=daily.claim_text,
            day_attr=daily.day_attr,
        )
        if day is not None:
            log().info(f"  → день {day} доступний")
            return http_success(int(day))
        log().info("  → бонус недоступний сьогодні")
        return http_success_none()

    @http_call
    async def claim_calendar(self, day: int | str, daily: DailyCfg) -> HttpResult[dict[str, Any]]:
        url = daily.urls.api_calendar
        room = daily.urls.balance
        try:
            formatted_url = url.format(day=day)
        except (IndexError, KeyError, ValueError):
            formatted_url = url.format(day)

        r = await self.post(formatted_url, room=room, priority="CRITICAL", timeout=15)
        if r.status_code == 200:
            log().info("  → отримано")
            return http_success(r.json() or {})
        log().warning(f"  → claim_calendar: {r.status_code}")
        return http_fail(FailReason.DENIED)

    @http_call
    async def claim_daily(self, daily: DailyCfg, personal: PersonalCfg) -> HttpResult[dict[str, Any]]:
        url = daily.urls.ping
        room = personal.urls.user_page.format(user_id=self.user_id)
        r = await self.post(url, room=room, priority="CRITICAL", timeout=15)
        if r.status_code == 200:
            log().info("  → отримано")
            return http_success(r.json() or {})
        log().warning(f"  → claim_daily: {r.status_code}")
        return http_fail(FailReason.DENIED)

    # ── Reader ────────────────────────────────────────────────────────────────

    @http_call
    async def submit_add_history(self, items: list[dict[str, Any]], last_manga_read: str, reader: ReaderAppCfg) -> HttpResult[dict[str, Any]]:
        url = reader.urls.api_history
        room = reader.urls.manga_page.format(translit_name=last_manga_read)
        body = {
            f"items[{i}][{k}]": v
            for i, item in enumerate(items)
            for k, v in item.items()
        }
        r = await self.post(url, room=room, data=body, priority="BACKGROUND")
        if r.status_code == 200:
            return http_success(r.json() or {})
        log().warning(f"  → submit_add_history: {r.status_code}")
        return http_fail(FailReason.SERVER)

    @http_call
    async def claim_candy(self, token: str, last_manga_read: str, reader: ReaderAppCfg) -> HttpResult[dict[str, Any]]:
        url = reader.urls.api_candy
        room = reader.urls.manga_page.format(translit_name=last_manga_read)
        r = await self.post(url, room=room, data={"token": token}, priority="BACKGROUND", timeout=15)
        if r.status_code == 200:
            log().info("  → цукерка отримана")
            return http_success(r.json() or {})
        log().warning(f"  → claim_candy: {r.status_code}")
        return http_fail(FailReason.DENIED)

    @http_call
    async def fetch_manga_catalog(self, reader: ReaderAppCfg, page: int = 1) -> HttpResult[str]:
        url = reader.urls.catalog
        r = await self.get(url, room=url, params={"page": page}, priority="BACKGROUND", timeout=15)
        r.raise_for_status()
        return http_success(r.text)

    @http_call
    async def fetch_manga_chapters(self, reader: ReaderAppCfg, translit_name: str, manga_data_id: int) -> HttpResult[str]:
        page = await self.fetch_manga_page(reader, translit_name)
        if not page or page.data is None:
            return http_fail(FailReason.NOT_FOUND)
        more = await self._fetch_more_chapters(translit_name, manga_data_id, reader)
        return http_success(page.data + (more.data or ""))

    @http_call
    async def fetch_manga_page(self, reader: ReaderAppCfg, translit_name: str) -> HttpResult[str]:
        url = reader.urls.manga_page.format(translit_name=translit_name)
        r = await self.get(url, room=url, priority="BACKGROUND", timeout=15)
        r.raise_for_status()
        return http_success(r.text)

    @http_call
    async def _fetch_more_chapters(self, translit_name: str, manga_data_id: int, reader: ReaderAppCfg) -> HttpResult[str]:
        url = reader.urls.api_load
        room = reader.urls.manga_page.format(translit_name=translit_name)
        r = await self.post(url, room=room, data={"manga_id": manga_data_id}, priority="BACKGROUND", timeout=15)
        r.raise_for_status()
        return http_success((r.json() or {}).get("content", ""))

    # ── Quiz ──────────────────────────────────────────────────────────────────

    @http_call
    async def quiz_start(self, quiz: QuizCfg) -> HttpResult[dict[str, Any]]:
        r = await self.post(quiz.urls.start, room=quiz.urls.quiz_page, priority="TIME_CRITICAL", timeout=15)
        if r.status_code == 200:
            question = (r.json() or {}).get("question")
            if question is None:
                return http_fail(FailReason.BAD_DATA)
            return http_success(question)
        log().warning(f"  → quiz_start: {r.status_code}")
        return http_fail(FailReason.SERVER)

    @http_call
    async def quiz_answer(self, answer: str, quiz: QuizCfg) -> HttpResult[dict[str, Any]]:
        r = await self.post(quiz.urls.answer, room=quiz.urls.quiz_page, data={"answer": answer}, priority="TIME_CRITICAL", timeout=15)
        if r.status_code == 200:
            return http_success(r.json() or {})
        log().warning(f"  → quiz_answer: {r.status_code}")
        return http_fail(FailReason.SERVER)

    # ── Mining ──────────────────────────────────────────────────────────────────

    @http_call
    async def mine(self, account_id: str, mining: MiningCfg) -> HttpResult[dict[str, Optional[int]]]:
        url = mining.urls.mining_page
        r = await self.get(url, room=url, priority="NORMAL", timeout=15)
        if r.status_code == 200:
            data = parse_mining_page(r.text)
            missing = [k for k, v in data.items() if v is None]
            if missing:
                auth_data = parse_main_page(r.text)
                if not auth_data.get("user_id"):
                    log().warning("  → mine: unauthenticated page detected")
                    await self.account_client.invalidate_session(account_id)
                    return http_fail(FailReason.AUTH)
                log().warning(f"  → mine: missing required mining fields: {', '.join(missing)}")
                return http_fail(FailReason.BAD_DATA)
            return http_success(data)
        log().warning(f"  → mine: {r.status_code}")
        return http_fail(FailReason.SERVER)

    _HITS_LIMIT_EXHAUSTED_MESSAGE = "Лимит ударов на сегодня исчерпан"

    @http_call
    async def mine_hit(self, mining: MiningCfg) -> HttpResult[dict[str, int]]:
        r = await self.post(mining.urls.hit, room=mining.urls.mining_page, priority="NORMAL", timeout=15)
        if r.status_code == 200:
            return http_success(r.json() or {})

        if r.status_code == 403:
            body = r.json() or {}
            message = body.get("message", "") if isinstance(body, dict) else ""
            if message == self._HITS_LIMIT_EXHAUSTED_MESSAGE:
                log().warning(f"  → mine_hit: 403 {message!r} — розбіжність лічильника, hits_left=0")
                return http_fail(FailReason.LIMIT_EXHAUSTED, data={"hits_left": 0})
            log().warning(f"  → mine_hit: 403 {message!r}")
            return http_fail(FailReason.DENIED)

        log().warning(f"  → mine_hit: {r.status_code}")
        return http_fail(FailReason.SERVER)

    @http_call
    async def upgrade_pickaxe(self, mining: MiningCfg) -> HttpResult[dict[str, int]]:
        r = await self.post(mining.urls.upgrade, room=mining.urls.mining_page, priority="NORMAL", timeout=15)
        if r.status_code == 200:
            return http_success(r.json() or {})
        elif r.status_code == 400:
            log().warning(f"  → upgrade_pickaxe: {r.status_code} (403 Forbidden)")
            return http_fail(FailReason.DENIED)
        log().warning(f"  → upgrade_pickaxe: {r.status_code}")
        return http_fail(FailReason.SERVER)

    @http_call
    async def buy_strong_hit(self, mining: MiningCfg) -> HttpResult[dict[str, int]]:
        r = await self.post(mining.urls.buy_strong_hit, room=mining.urls.mining_page, priority="NORMAL", timeout=15)
        if r.status_code == 200:
            return http_success(r.json() or {})
        elif r.status_code == 400:
            log().warning(f"  → buy_strong_hit: {r.status_code} (403 Forbidden)")
            return http_fail(FailReason.DENIED)
        log().warning(f"  → buy_strong_hit: {r.status_code}")
        return http_fail(FailReason.SERVER)

    @http_call
    async def exchange_ore(self, mining: MiningCfg, diamonds: int) -> HttpResult[dict[str, int]]:
        r = await self.post(mining.urls.exchange, room=mining.urls.mining_page, data={"diamonds": diamonds}, priority="NORMAL", timeout=15)
        if r.status_code == 200:
            return http_success(r.json() or {})
        elif r.status_code == 400:
            log().warning(f"  → exchange_ore: {r.status_code} (403 Forbidden)")
            return http_fail(FailReason.DENIED)
        log().warning(f"  → exchange_ore: {r.status_code}")
        return http_fail(FailReason.SERVER)
