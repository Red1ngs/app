"""
telegram_service/proxy_utils.py — валідація/нормалізація проксі на вході,
у боті, ще ДО мережевого виклику до core-service → account-service.

Це навмисна копія account_service/proxy_utils.py (account-service лишається
єдиним джерелом правди й останньою лінією захисту — там та сама перевірка
знову спрацює для будь-якого іншого клієнта account-service, не тільки
цього бота). Дублювання тут — не помилка архітектури, а свідомий вибір:
telegram-service і account-service живуть у різних образах без спільної
залежності (див. Dockerfile), а мати миттєвий, зрозумілий фідбек прямо в
чаті — без зайвого round-trip по мережі заради помилки формату — вартує
цього невеликого дублювання. Якщо колись логіка проксі ускладниться,
винести обидві копії в окремий пакет ``proxy-format`` буде правильним
наступним кроком.

Формати на вході:
  "1.2.3.4:8080"                → http за замовчуванням
  "user:pass@1.2.3.4:8080"      → http за замовчуванням
  "http(s)/socks4/socks5(h)://1.2.3.4:8080"
  "http(s)/socks4/socks5(h)://user:pass@1.2.3.4:8080"
  "1.2.3.4:8080:user:pass"      → формат багатьох проксі-провайдерів

normalize_proxy("") -> "" (проксі не задано — це ок, акаунт без проксі).
normalize_proxy(<криво>) -> ValueError з людяним текстом, можна показати
користувачу прямо в повідомленні бота.
"""
from __future__ import annotations

_SCHEMES = {"http", "https", "socks4", "socks5", "socks5h"}


def normalize_proxy(raw: str | None) -> str:
    if raw is None:
        return ""
    raw = raw.strip()
    if not raw:
        return ""

    if raw.count("://") > 1:
        raise ValueError(
            "Схема проксі вказана двічі (напр. http://...https://...). "
            "Залиш тільки одну схему, напр. http://user:pass@1.2.3.4:8080"
        )

    scheme = "http"
    rest = raw
    if "://" in raw:
        scheme, rest = raw.split("://", 1)
        scheme = scheme.lower()
        if scheme not in _SCHEMES:
            raise ValueError(
                f"Невідома схема проксі '{scheme}'. Підтримуються: {', '.join(sorted(_SCHEMES))}"
            )

    if not rest:
        raise ValueError("Не вказано хост і порт проксі")

    auth = ""
    hostport = rest
    if "@" in rest:
        auth, hostport = rest.rsplit("@", 1)

    # Формат багатьох проксі-провайдерів: host:port:user:pass (без "@")
    if not auth and hostport.count(":") == 3:
        host, port, user, pwd = hostport.split(":")
        auth = f"{user}:{pwd}"
        hostport = f"{host}:{port}"

    if "://" in hostport or "@" in hostport:
        raise ValueError(
            "Не вдалось розібрати проксі — перевір формат: http://user:pass@host:port"
        )

    if ":" not in hostport:
        raise ValueError("Не вказано порт. Формат: host:port або user:pass@host:port")

    host, _, port = hostport.rpartition(":")
    if not host:
        raise ValueError("Не вказано хост проксі")
    if not port.isdigit() or not (1 <= int(port) <= 65535):
        raise ValueError(f"Некоректний порт '{port}' — має бути числом від 1 до 65535")

    result = f"{scheme}://"
    if auth:
        if auth.count(":") != 1:
            raise ValueError("Некоректні дані авторизації проксі — очікується user:pass")
        result += f"{auth}@"
    result += f"{host}:{port}"
    return result