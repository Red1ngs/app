"""
src/mangabuff/session/__init__.py

Публічний API пакету. Імпортуй звідси — не з окремих модулів.

BotSocket / MessageSocket / BotHttpClient / BotAuth більше НЕ живуть тут —
вони переїхали в account-service (account_service/transport/, .../session.py).
Бізнес-сервіс говорить з ними тільки через src.core.account_client (HTTP-порт)
і src.core.account_events (Redis socket-callback порт).
"""
from src.mangabuff.session.bot_session import BotSession
from src.mangabuff.session.http_result import (
    HttpResult,
    FailReason,
    http_success,
    http_success_none,
    http_fail,
    http_call,
)

__all__ = [
    "BotSession",
    "HttpResult", "FailReason", "http_success", "http_success_none", "http_fail", "http_call",
]
