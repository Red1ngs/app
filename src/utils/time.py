from __future__ import annotations
import datetime
import hashlib
import random
import time as _time_module
from typing import Literal

_tz: datetime.timezone | None = None  # None = локальний час машини


# --- 1. Налаштування часової зони та внутрішні парсери ---

def set_timezone(tz: str | datetime.timezone | None) -> None:
    global _tz
    if tz is None:
        _tz = None
        return
    if isinstance(tz, datetime.timezone):
        _tz = tz
        return

    s = tz.strip()
    if s.upper() == "UTC":
        _tz = datetime.timezone.utc
        return
    if s.upper().startswith("UTC"):
        offset_str = s[3:]
        if offset_str:
            _tz = _parse_offset(offset_str)
            return
        _tz = datetime.timezone.utc
        return

    # 1. Пробуємо ZoneInfo (вимагає pip install tzdata на Windows)
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            _tz = ZoneInfo(s)  # type: ignore
            return
        except ZoneInfoNotFoundError:
            pass  # Йдемо далі до pytz
    except ImportError:
        pass

    # 2. Пробуємо pytz (як запасний варіант)
    try:
        import pytz
        _tz = pytz.timezone(s)  # type: ignore
        return
    except (ImportError, Exception):
        pass

    raise ValueError(
        f"Не вдалося розпізнати timezone {tz!r}. "
        f"Порада: встановіть 'tzdata' (pip install tzdata)"
    )


def _parse_offset(s: str) -> datetime.timezone:
    s = s.strip()
    sign = -1 if s.startswith("-") else 1
    if s.startswith(("+", "-")):
        s = s[1:]
    if ":" in s:
        h, m = map(int, s.split(":", 1))
    else:
        h, m = int(s), 0
    return datetime.timezone(datetime.timedelta(hours=sign * h, minutes=sign * m))


def _parse_hh_mm(s: str) -> tuple[int, int]:
    """
    Внутрішня функція для безпечного отримання (години, хвилини) з будь-якого формату часу.
    Успішно обробляє формати 'HH:MM', 'HH:MM:SS', а також повні ISO рядки з датою.
    """
    try:
        # Прибираємо дату (якщо вона є), орієнтуючись на пробіл або T
        time_part = s.strip().replace("T", " ").split()[-1]
        parts = time_part.split(":")
        if len(parts) < 2:
            raise ValueError
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        raise ValueError(
            f"Неправильний формат часу: {s!r}. "
            f"Очікується формат 'HH:MM', 'HH:MM:SS' або ISO рядок дати та часу."
        )


# --- 2. Ядро отримання та парсингу часу ---

def now() -> datetime.datetime:
    """Внутрішня функція для отримання datetime об'єкта."""
    return datetime.datetime.now(_tz) if _tz else datetime.datetime.now().astimezone()


def parse_to_ts(s: str) -> float:
    """
    Єдине місце поза цим файлом, де розбираються складні рядки дати.
    Перетворює ISO або 'YYYY-MM-DD HH:MM' у Unix Timestamp.
    """
    s_normalized = s.strip().replace(" ", "T")
    dt = datetime.datetime.fromisoformat(s_normalized)
    
    if dt.tzinfo is None:
        # Якщо в рядку немає зони, примусово ставимо налаштовану зону проекту
        project_tz = now().tzinfo
        dt = dt.replace(tzinfo=project_tz)
    
    return dt.timestamp()


def format_ts(ts: float, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Форматує Unix timestamp у рядок з урахуванням налаштованої timezone."""
    default_tz = datetime.datetime.now().astimezone().tzinfo
    return datetime.datetime.fromtimestamp(ts, tz=_tz or default_tz).strftime(fmt)


# --- 3. Отримання поточних дат та системного часу ---

def today(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Поточна дата та час у налаштованій часовій зоні.
    За замовчуванням формат містить і час: 'YYYY-MM-DD HH:MM:SS'.
    """
    return now().strftime(fmt)


def tomorrow(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Завтрашня дата та час у налаштованій часовій зоні.
    За замовчуванням формат містить і час: 'YYYY-MM-DD HH:MM:SS'.
    """
    tomorrow_dt = now() + datetime.timedelta(days=1)
    return tomorrow_dt.strftime(fmt)


def now_ts() -> float:
    return _time_module.time()


def monotonic() -> float:
    return _time_module.monotonic()


def sleep(seconds: float) -> None:
    _time_module.sleep(seconds)


# --- 4. Перетворення, модифікація та генерація часу ---

def reformat_date(
    date_str: str, 
    out_fmt: str, 
    in_fmt: str | None = None
) -> str:
    """
    Перетворює рядок дати у заданий цільовий формат.
    
    Параметри:
      - date_str: вхідний рядок із датою.
      - out_fmt: бажаний формат на виході (наприклад, '%d.%m.%Y').
      - in_fmt: формат вхідного рядка. Якщо None — використовується стандартний парсер 
                модуля parse_to_ts (підтримує ISO-формати та 'YYYY-MM-DD HH:MM').
    """
    if in_fmt:
        # Парсимо за специфічним вхідним форматом
        project_tz = _tz or datetime.datetime.now().astimezone().tzinfo
        dt = datetime.datetime.strptime(date_str.strip(), in_fmt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=project_tz)
        ts = dt.timestamp()
    else:
        # Використовуємо стандартний парсинг вашого модуля
        ts = parse_to_ts(date_str)
        
    return format_ts(ts, out_fmt)


def shift_date(
    s: str, 
    days: float = 0, 
    hours: float = 0, 
    minutes: float = 0, 
    seconds: float = 0, 
    fmt: str = "%Y-%m-%d %H:%M:%S"
) -> str:
    """Зсуває дату-рядок на заданий інтервал часу та повертає новий рядок у заданому форматі."""
    ts = parse_to_ts(s)
    total_seconds = seconds + (minutes * 60) + (hours * 3600) + (days * 86400)
    return format_ts(ts + total_seconds, fmt)


def get_stable_random_time(base_time: str, account_id: str, max_jitter_minutes: int = 60) -> str:
    """
    Повертає псевдовипадковий час у форматі HH:MM, зміщений відносно base_time.
    Зсув стабільний для конкретного account_id (MD5-хеш).
    """
    try:
        h, m = _parse_hh_mm(base_time)
    except ValueError:
        return base_time

    hash_val = int(hashlib.md5(account_id.encode()).hexdigest(), 16)
    jitter = hash_val % (max_jitter_minutes + 1)

    total_minutes = (h * 60 + m + jitter) % 1440
    new_h, new_m = divmod(total_minutes, 60)
    return f"{new_h:02d}:{new_m:02d}"


def format_duration(
    seconds: float | int, 
    style: Literal["HH:MM:SS", "DD HH:MM:SS", "readable"] = "HH:MM:SS"
) -> str:
    """
    Перетворює кількість секунд (тривалість) у зручний текстовий формат.
    
    Параметри:
      - seconds: тривалість у секундах.
      - style: стиль відображення:
          * "HH:MM:SS" (дефолт) -> наприклад, "04:30:15" або "28:15:00" (якщо більше доби).
          * "DD HH:MM:SS" -> наприклад, "01d 04:30:15".
          * "readable" -> компактний текстовий вигляд, наприклад, "1d 4h 30m 15s".
    """
    total_sec = int(round(seconds))
    sign = "-" if total_sec < 0 else ""
    total_sec = abs(total_sec)
        
    days, remainder = divmod(total_sec, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    
    if style == "HH:MM:SS":
        total_hours = days * 24 + hours
        return f"{sign}{total_hours:02d}:{minutes:02d}:{secs:02d}"
        
    if style == "DD HH:MM:SS":
        return f"{sign}{days:02d}d {hours:02d}:{minutes:02d}:{secs:02d}"
        
    if style == "readable":
        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        if secs > 0 or not parts:
            parts.append(f"{secs}s")
        return sign + " ".join(parts)
        
    raise ValueError(f"Невідомий стиль форматування: {style!r}")


# --- 5. Обчислення інтервалів та планування ---

def next_timestamp_for_time(hh_mm: str) -> float:
    """
    Повертає Unix timestamp наступного запланованого виконання на основі часу.
    Враховує секунди та дату у вхідному рядку за потреби.
    """
    h, m = _parse_hh_mm(hh_mm)
    n = now()
    target = n.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= n:
        target += datetime.timedelta(days=1)
    return target.timestamp()


def next_day_timestamp_for_time(hh_mm: str) -> float:
    """Повертає Unix timestamp завтрашнього дня о заданому часі HH:MM.
    На відміну від next_timestamp_for_time — завжди завтра, навіть якщо час ще не настав сьогодні.
    Використовується, щоб не повторити тригер в той самий день після restore_state().
    """
    h, m = _parse_hh_mm(hh_mm)
    n = now()
    tomorrow_dt = (n + datetime.timedelta(days=1)).replace(
        hour=h, minute=m, second=0, microsecond=0
    )
    return tomorrow_dt.timestamp()


def seconds_until_midnight() -> float:
    """
    Секунд від зараз до 00:00:00 наступного дня в налаштованій часовій зоні.
    Замінює системний локальний час на налаштований у проекті.
    """
    n = now()  # бере налаштовану зону (або локальну, якщо set_timezone не викликався)
    tomorrow_dt = (n + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(60.0, (tomorrow_dt - n).total_seconds())


def seconds_until_tomorrow_time(target_time: str, jitter_minutes: int = 30) -> float:
    """
    Обчислює кількість секунд від поточного моменту до заданого часу наступного дня
    з урахуванням випадкового зсуву.
    """
    h, m = _parse_hh_mm(target_time)

    to_midnight = seconds_until_midnight()
    base_offset = h * 3600.0 + m * 60.0
    random_seconds = random.randint(-jitter_minutes * 60, jitter_minutes * 60)
    
    total_seconds = to_midnight + base_offset + random_seconds
    return max(60.0, total_seconds)


def seconds_until_tomorrow_time_stable(
    target_time: str, 
    account_id: str, 
    jitter_minutes: int = 30
) -> float:
    """
    Обчислює кількість секунд до заданого часу наступного дня зі стабільним 
    псевдовипадковим зсувом для конкретного account_id.
    """
    h, m = _parse_hh_mm(target_time)

    to_midnight = seconds_until_midnight()
    base_offset = h * 3600.0 + m * 60.0
    
    # Генеруємо зсув на основі MD5-хешу
    hash_val = int(hashlib.md5(account_id.encode()).hexdigest(), 16)
    
    # Перетворюємо в діапазон від -jitter_minutes до +jitter_minutes
    max_range = jitter_minutes * 2
    jitter_offset_minutes = (hash_val % (max_range + 1)) - jitter_minutes
    
    total_seconds = to_midnight + base_offset + (jitter_offset_minutes * 60)
    return max(60.0, total_seconds)


# --- 6. Продвинута система порівняння часу ---

def compare_dates(s1: str, s2: str) -> int:
    """
    Порівнює два рядки дат.
    Повертає:
      -1, якщо s1 < s2
       0, якщо s1 == s2
       1, якщо s1 > s2
    """
    ts1 = parse_to_ts(s1)
    ts2 = parse_to_ts(s2)
    if ts1 < ts2:
        return -1
    if ts1 > ts2:
        return 1
    return 0


def is_before(s1: str, s2: str) -> bool:
    """Перевіряє, чи дата s1 передує s2."""
    return parse_to_ts(s1) < parse_to_ts(s2)


def is_after(s1: str, s2: str) -> bool:
    """Перевіряє, чи дата s1 є пізнішою за s2."""
    return parse_to_ts(s1) > parse_to_ts(s2)


def is_equal(s1: str, s2: str) -> bool:
    """Перевіряє, чи вказують дати s1 та s2 на один і той самий момент часу."""
    return parse_to_ts(s1) == parse_to_ts(s2)


def is_between(s: str, start: str, end: str, inclusive: bool = True) -> bool:
    """Перевіряє, чи входить дата s в інтервал між start та end."""
    ts = parse_to_ts(s)
    ts_start = parse_to_ts(start)
    ts_end = parse_to_ts(end)
    
    if inclusive:
        return ts_start <= ts <= ts_end
    return ts_start < ts < ts_end


def time_diff(
    s1: str, 
    s2: str, 
    unit: Literal["seconds", "minutes", "hours", "days"] = "seconds"
) -> float:
    """
    Повертає різницю між двома датами (s1 - s2) у вказаних одиницях.
    Значення може бути від'ємним, якщо s1 передує s2.
    """
    diff_sec = parse_to_ts(s1) - parse_to_ts(s2)
    if unit == "seconds":
        return diff_sec
    elif unit == "minutes":
        return diff_sec / 60.0
    elif unit == "hours":
        return diff_sec / 3600.0
    elif unit == "days":
        return diff_sec / 86400.0
    raise ValueError(f"Невідома одиниця виміру часу: {unit}")


def is_next_day(date_str: str, date2_str: str, strictly_tomorrow: bool = True) -> bool:
    """
    Порівнює дві дати-рядки за календарними днями (без врахування часу).
    
    Параметри:
      - date_str: перша дата (базова)
      - date2_str: друга дата, яку перевіряємо
      - strictly_tomorrow: 
          Якщо True — поверне True тільки якщо date2_str це саме наступний день (завтра).
          Якщо False — поверне True для будь-якого наступного дня у майбутньому (завтра і пізніше).
    
    Повертає False, якщо date2_str — це той самий день або минулий.
    """
    # Отримуємо часову зону проекту для точного визначення календарного дня
    project_tz = _tz or datetime.datetime.now().astimezone().tzinfo
    
    # Перетворюємо рядки на об'єкти datetime з урахуванням таймзони
    dt1 = datetime.datetime.fromtimestamp(parse_to_ts(date_str), tz=project_tz)
    dt2 = datetime.datetime.fromtimestamp(parse_to_ts(date2_str), tz=project_tz)
    
    # Вираховуємо різницю суто між календарними датами (без часу)
    diff_days = (dt2.date() - dt1.date()).days
    
    if strictly_tomorrow:
        return diff_days == 1
    
    return diff_days >= 1

def is_today(date_input: str | datetime.datetime | datetime.date | float | int) -> bool:
    """
    Перевіряє, чи відноситься вказана дата до поточного календарного дня (сьогодні)
    у налаштованій часовій зоні проекту.
    
    Параметри:
      - date_input: Може бути рядком дати (ISO-формат або 'YYYY-MM-DD HH:MM'),
                    об'єктом datetime/date або Unix timestamp (float/int).
    """
    # 1. Отримуємо поточний момент часу у налаштованій зоні
    current_now = now()
    today_date = current_now.date()
    project_tz = current_now.tzinfo

    # 2. Обробляємо різні типи вхідних даних
    if isinstance(date_input, str):
        # Використовуємо наявний парсер модуля для отримання timestamp
        ts = parse_to_ts(date_input)
        dt = datetime.datetime.fromtimestamp(ts, tz=project_tz)
        return dt.date() == today_date

    if isinstance(date_input, (float, int)):
        dt = datetime.datetime.fromtimestamp(date_input, tz=project_tz)
        return dt.date() == today_date

    if isinstance(date_input, datetime.datetime):
        # Якщо datetime наївний (без таймзони), приводимо до зони проекту
        if date_input.tzinfo is None:
            dt = date_input.replace(tzinfo=project_tz)
        else:
            dt = date_input.astimezone(project_tz)
        return dt.date() == today_date

    if isinstance(date_input, datetime.date):
        # Оскільки об'єкт date не має часової зони, порівнюємо напряму
        return date_input == today_date

    raise TypeError(
        f"Непідтримуваний тип даних: {type(date_input)}. "
        f"Очікується str, datetime, date або число (timestamp)."
    )