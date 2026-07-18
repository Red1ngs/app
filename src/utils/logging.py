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
from typing import Any
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

# ── curl_cffi logging helpers ─────────────────────────────────────────────────
# curl_cffi не підтримує event hooks як httpx, тому логування робиться
# вручну в BotTransport._request() через ці функції.
#
# Порядок логів для кожного запиту:
#   1. log_payload_curl  — що передано (params/data/json) ДО відправки
#   2. log_request_curl  — сам запит (METHOD URL)
#   3. log_response_curl — що прийшло (STATUS ms body) ДО будь-якої валідації


def _format_payload(data: Any) -> str:
    """Серіалізує payload у читабельний рядок, маскуючи чутливі поля."""
    if not data:
        return ""
    if isinstance(data, bytes):
        return _body_hint(data, "application/x-www-form-urlencoded")
    if isinstance(data, dict):
        masked = {
            k: "***" if str(k) in _SENSITIVE else v
            for k, v in data.items()
        }
        # Компактний формат: key=value  key2=value2
        parts = [f"{k}={v}" for k, v in masked.items()]
        return "  ".join(parts)
    return str(data)[:200]


def log_payload_curl(
    method: str,
    url: str,
    *,
    params: Any = None,
    data: Any = None,
    json_body: Any = None,
) -> None:
    """
    Крок 1: логує що передається в запит ДО його відправки.

    Виклик:
        log_payload_curl("POST", url, data=body_dict)
        log_payload_curl("GET",  url, params={"page": 1})
    """
    parts: list[str] = []
    if params:
        parts.append(f"params={_format_payload(params)}")
    if data:
        parts.append(f"data={_format_payload(data)}")
    if json_body:
        parts.append(f"json={_format_payload(json_body)}")

    if not parts:
        return  # нема тіла — не засмічуємо лог

    _log().debug(f"  ↑  {method:<5} {url}  {' | '.join(parts)}")


def log_request_curl(method: str, url: str, headers: dict[str, str], body: Any = None) -> float:
    """
    Крок 2: логує сам HTTP-запит (METHOD URL).
    Повертає monotonic timestamp для розрахунку elapsed у log_response_curl.

    Примітка: body тут ігнорується — payload вже залогований у log_payload_curl.
    Параметр залишено для зворотної сумісності.

    headers типізовано як dict[str, str] (було голе `dict`, тобто
    dict[Unknown, Unknown]) — саме це й було джерелом reportUnknownVariableType
    на виклику в http_client.py: тип "t" тягнув Unknown із сигнатури,
    хоча сама функція завжди повертає float.
    """
    t = time.monotonic()
    _log().debug(f"  →  {method:<5} {url}")
    return t


def log_response_curl(status: int, url: str, body: bytes, content_type: str, started: float) -> None:
    """
    Крок 3: логує сиру відповідь одразу після отримання, ДО будь-якої валідації.
    """
    elapsed_ms = int((time.monotonic() - started) * 1000)
    hint   = _body_hint(body, content_type)
    level  = logging.DEBUG if status < 400 else logging.WARNING
    suffix = f"  {hint}" if hint else ""
    _log().log(level, f"  ←  {status}  {elapsed_ms}ms  {url}{suffix}")