"""
src/core/account_client.py — клієнт до account-service (порт).

Бізнес-сервіс НЕ тримає curl_cffi/socketio клієнтів і НЕ бачить cookies —
він або (а) просить account-service виконати HTTP-запит через сесію
потрібного акаунта, або (б) підписується на socket-події через
AccountEventBus (src/core/account_events.py, Redis pub/sub).

Використання (з core_account.py, bot_session.py):

    from src.core.account_client import account_client

    await account_client.register(account_id, email, password, proxy)
    status = await account_client.connect(account_id)
    resp   = await account_client.request(account_id, "GET", "/mine", room="/mine")
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx


class AccountServiceError(Exception):
    """Загальна помилка account-service (мережа, 5xx, неочікуваний статус)."""


class AccountUnavailableError(AccountServiceError):
    """Акаунт не підключений на боці account-service (409 — виклич connect())."""


class AccountAuthError(AccountServiceError):
    """401 від account-service — сесія прострочена і re-login не вдався."""


@dataclass
class RemoteResponse:
    """Легкий аналог curl_cffi.Response — щоб бізнес-методи bot_session.py
    (mine, quiz_start, claim_daily, ...) майже не змінювались.
    """
    status_code: int
    text: str
    headers: dict[str, str] = field(default_factory=dict)
    _json: Optional[Any] = None

    def json(self) -> Any:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=None  # type: ignore[arg-type]
            )


@dataclass
class AccountStatus:
    id: str
    email: str
    status: str
    error: Optional[str]
    user_id: Optional[str]
    user_name: Optional[str]
    proxy: Optional[str] = None

    @property
    def is_connected(self) -> bool:
        return self.status == "connected"


class AccountServiceClient:
    def __init__(self, base_url: Optional[str] = None, timeout: float = 30.0) -> None:
        self._base_url = (base_url or os.getenv("ACCOUNT_SERVICE_URL", "http://account-service:8100")).rstrip("/")
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── Реєстрація / статус ──────────────────────────────────────────────────

    async def register(
        self, account_id: str, email: str, password: str, proxy: Optional[str] = None,
    ) -> AccountStatus:
        r = await self._client.post("/accounts", json={
            "account_id": account_id, "email": email, "password": password, "proxy": proxy,
        })
        self._raise_for_status(r)
        return self._to_status(r.json())

    async def get_status(self, account_id: str) -> Optional[AccountStatus]:
        r = await self._client.get(f"/accounts/{account_id}")
        if r.status_code == 404:
            return None
        self._raise_for_status(r)
        return self._to_status(r.json())

    async def connect(self, account_id: str) -> AccountStatus:
        r = await self._client.post(f"/accounts/{account_id}/connect")
        self._raise_for_status(r)
        return self._to_status(r.json())

    async def disconnect(self, account_id: str) -> None:
        await self._client.post(f"/accounts/{account_id}/disconnect")

    async def invalidate_session(self, account_id: str) -> None:
        await self._client.post(f"/accounts/{account_id}/session/invalidate")

    # ── Generic-порт: "зроби цей запит" ──────────────────────────────────────

    async def request(
        self,
        account_id: str,
        method: str,
        url: str,
        room: Optional[str] = None,
        priority: str = "NORMAL",
        params: Optional[dict[str, Any]] = None,
        data: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> RemoteResponse:
        body: dict[str, Any] = {"method": method, "url": url, "room": room, "priority": priority}
        if params is not None:
            body["params"] = params
        if data is not None:
            body["data"] = data
        if json is not None:
            body["json_body"] = json
        if headers is not None:
            body["headers"] = headers
        if timeout is not None:
            body["timeout"] = timeout

        r = await self._client.post(f"/accounts/{account_id}/request", json=body)
        if r.status_code == 409:
            raise AccountUnavailableError(r.text)
        if r.status_code == 401:
            raise AccountAuthError(r.text)
        self._raise_for_status(r)
        payload = r.json()
        return RemoteResponse(
            status_code=payload["status_code"],
            text=payload["text"],
            headers=payload.get("headers", {}),
            _json=payload.get("json"),
        )

    # ── Діалоги ───────────────────────────────────────────────────────────────

    async def open_dialog(self, account_id: str, user_id: str) -> Optional[str]:
        r = await self._client.post(f"/accounts/{account_id}/dialog/open", params={"user_id": str(user_id)})
        self._raise_for_status(r)
        return r.json().get("dialog_token")

    async def send_message(
        self, account_id: str, to_user_id: str, text: str,
        reply_id: Optional[str] = None, reply_text: Optional[str] = None,
    ) -> RemoteResponse:
        r = await self._client.post(f"/accounts/{account_id}/dialog/send", json={
            "to_user_id": str(to_user_id), "text": text, "reply_id": reply_id, "reply_text": reply_text,
        })
        self._raise_for_status(r)
        payload = r.json()
        return RemoteResponse(status_code=payload["status_code"], text=payload["text"])

    async def mark_messages_read(self, account_id: str, dialog_token: str, last_msg_id: Optional[str] = None) -> None:
        r = await self._client.post(f"/accounts/{account_id}/dialog/read", json={
            "dialog_token": dialog_token, "last_msg_id": last_msg_id,
        })
        self._raise_for_status(r)

    async def close_dialog(self, account_id: str) -> None:
        await self._client.post(f"/accounts/{account_id}/dialog/close")

    # ── Internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _raise_for_status(r: httpx.Response) -> None:
        if r.status_code >= 400:
            raise AccountServiceError(f"account-service {r.status_code}: {r.text}")

    @staticmethod
    def _to_status(payload: dict[str, Any]) -> AccountStatus:
        return AccountStatus(
            id=payload["id"], email=payload["email"], status=payload["status"],
            error=payload.get("error"), user_id=payload.get("user_id"), user_name=payload.get("user_name"),
        )


# Синглтон — за тим самим патерном, що proxy_queue_manager раніше.
account_client = AccountServiceClient()
