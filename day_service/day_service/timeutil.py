"""
day_service/timeutil.py — самодостатні часові хелпери.

Навмисно НЕ імпортує src.utils.time з бізнес-застосунку (day-service —
окремий образ/деплой, без спільного коду з app; та сама причина, чому
account_client.py копіюється, а не імпортується, у queue_service).
Тут лише той мінімум, що потрібен для обчислення "стабільного" (з
індивідуальним для account_id зсувом) моменту настання нового дня.
"""
from __future__ import annotations

import datetime
import hashlib

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment, misc]


def get_tz(name: str) -> datetime.tzinfo:
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return datetime.timezone.utc


def now(tz: datetime.tzinfo) -> datetime.datetime:
    return datetime.datetime.now(tz)


def parse_hh_mm(s: str) -> tuple[int, int]:
    h, m = s.strip().split(":")[:2]
    return int(h), int(m)


def stable_jitter_minutes(account_id: str, jitter_minutes: int) -> int:
    """
    Симетричний стабільний (по MD5 від account_id) зсув у хвилинах,
    у діапазоні [-jitter_minutes, +jitter_minutes]. Той самий алгоритм,
    що й раніше використовувався бізнес-застосунком у DailyMonitor —
    тому час, на який реально спрацьовував акаунт, не зміщується для
    вже працюючих акаунтів при переїзді цієї логіки в day-service.
    """
    if jitter_minutes <= 0:
        return 0
    hash_val = int(hashlib.md5(account_id.encode()).hexdigest(), 16)
    max_range = jitter_minutes * 2
    return (hash_val % (max_range + 1)) - jitter_minutes


def scheduled_time_today(
    account_id: str,
    base_time: str,
    jitter_minutes: int,
    tz: datetime.tzinfo,
) -> tuple[str, datetime.datetime]:
    """
    Повертає (HH:MM стабільного часу, datetime сьогодні о цьому часі).
    """
    h, m = parse_hh_mm(base_time)
    offset = stable_jitter_minutes(account_id, jitter_minutes)
    total_minutes = (h * 60 + m + offset) % 1440
    new_h, new_m = divmod(total_minutes, 60)

    n = now(tz)
    target = n.replace(hour=new_h, minute=new_m, second=0, microsecond=0)
    return f"{new_h:02d}:{new_m:02d}", target


def next_occurrence(
    account_id: str,
    base_time: str,
    jitter_minutes: int,
    tz: datetime.tzinfo,
) -> tuple[str, datetime.datetime, str]:
    """
    Повертає (scheduled_time HH:MM, наступний datetime спрацювання,
    календарний день (YYYY-MM-DD), якому belongsить це спрацювання).

    Якщо сьогоднішній час ще не настав — наступне спрацювання сьогодні.
    Якщо вже минув — завтра (о тому ж стабільному часі).
    """
    scheduled_time, target_today = scheduled_time_today(account_id, base_time, jitter_minutes, tz)
    n = now(tz)
    if target_today > n:
        return scheduled_time, target_today, target_today.date().isoformat()

    target_tomorrow = target_today + datetime.timedelta(days=1)
    return scheduled_time, target_tomorrow, target_tomorrow.date().isoformat()


def day_token(tz: datetime.tzinfo) -> str:
    """Календарний день 'зараз' у налаштованій зоні, у форматі YYYY-MM-DD."""
    return now(tz).date().isoformat()
