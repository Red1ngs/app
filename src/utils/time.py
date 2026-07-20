from __future__ import annotations
import datetime
import time as _time_module

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


def now_ts() -> float:
    return _time_module.time()


def monotonic() -> float:
    return _time_module.monotonic()


def sleep(seconds: float) -> None:
    _time_module.sleep(seconds)


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