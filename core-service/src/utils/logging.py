"""
utils/logging.py — HTTP request/response logging.

Request:  →  METHOD /path  [body hint]
Response: ←  STATUS  Nms  /path  [body hint]

Кожен HTTP-лог автоматично потрапляє у файл того акаунта,
чий worker-потік зараз виконується.

Як це працює:
    Python threading ізолює ContextVar по потоках.
    BotWorker._loop() викликає set_http_logger(self._task_log)
    один раз на старті потоку — і всі наступні httpx event-hooks
    пишуть у правильний файл без будь-яких змін у BotSession.

Fallback: якщо контекст не встановлено (тести, інші потоки),
    логи йдуть у стандартний logger 'http'.
"""
from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar
from urllib.parse import unquote

import httpx

# ── Контекстний логер ─────────────────────────────────────────────────────────
# Встановлюється один раз на початку кожного worker-потоку.
_http_logger: ContextVar[logging.Logger] = ContextVar(
    "http_logger",
    default=logging.getLogger("http"),
)

_timings: dict[int, float] = {}
_SENSITIVE = {"password", "_token", "token"}


def set_http_logger(logger: logging.Logger) -> None:
    """
    Прив'язує логер до поточного потоку.
    Викликати один раз на початку BotWorker._loop().
    """
    _http_logger.set(logger)


def get_logger() -> logging.Logger:
    """Повертає поточний логер для HTTP-трафіку поточного потоку."""
    return _http_logger.get()


# Зворотна сумісність — не видаляти до повного рефакторингу
_log = get_logger


# ── Хелпери ───────────────────────────────────────────────────────────────────

def _short_path(url: httpx.URL) -> str:
    path = url.path or "/"
    if url.params:
        path += f"?{url.params}"
    return path


def _body_hint(content: bytes, content_type: str) -> str:
    if not content:
        return ""
    if "application/json" in content_type:
        try:
            data = json.loads(content)
            masked = {
                k: "***" if k in _SENSITIVE else v
                for k, v in (data.items() if isinstance(data, dict) else {})
            }
            return json.dumps(masked, ensure_ascii=False)
        except Exception:
            pass
    if "application/x-www-form-urlencoded" in content_type:
        parts = []
        for pair in unquote(content.decode("utf-8", errors="replace")).split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                parts.append(f"{k}=***" if k in _SENSITIVE else f"{k}={v}")
            else:
                parts.append(pair)
        return "  ".join(parts)
    if "text/html" in content_type:
        return "[html]"
    text = content.decode("utf-8", errors="replace")
    return text[:120].replace("\n", " ") if text.strip() else ""


# ── httpx event hooks ─────────────────────────────────────────────────────────

def log_request(request: httpx.Request) -> None:
    _timings[id(request)] = time.monotonic()
    body   = _body_hint(request.read(), request.headers.get("content-type", ""))
    suffix = f"  {body}" if body else ""
    _log().debug(f"  →  {request.method:<5} {_short_path(request.url)}{suffix}")


def log_response(response: httpx.Response) -> None:
    start = _timings.pop(id(response.request), None)
    elapsed_ms = int((time.monotonic() - start) * 1000) if start is not None else 0

    content = response.read()
    body    = _body_hint(content, response.headers.get("content-type", ""))
    status  = response.status_code
    level   = logging.DEBUG if status < 400 else logging.WARNING
    suffix  = f"  {body}" if body else ""

    _log().log(
        level,
        f"  ←  {status}  {elapsed_ms}ms  {_short_path(response.url)}{suffix}",
    )