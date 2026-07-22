from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

_FMT_FULL  = "%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s"
_FMT_SHORT = "%(asctime)s  %(levelname)-8s  %(message)s"
_DATE_FMT  = "%Y-%m-%d %H:%M:%S"

# Налаштування часу: ротація кожну добу (D), зберігати 5 останніх файлів
_WHEN = "D"
_INTERVAL = 1
_BACKUP_COUNT = 5


def _make_handler(
    path: Path,
    fmt: str = _FMT_FULL,
    level: int = logging.DEBUG,
) -> logging.handlers.TimedRotatingFileHandler:
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Використовуємо TimedRotatingFileHandler для обмеження за часом
    h = logging.handlers.TimedRotatingFileHandler(
        filename=str(path),
        when=_WHEN,
        interval=_INTERVAL,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
        atTime=None  # можна вказати datetime.time(), щоб ротація була рівно о півночі
    )
    h.setLevel(level)
    h.setFormatter(logging.Formatter(fmt, datefmt=_DATE_FMT))
    return h


def setup_logging(
    log_dir: str | Path = "logs",
    console: bool = False,
    console_level: int = logging.WARNING,
) -> None:
    """
    Налаштовує всю систему логування з ротацією за часом (5 днів).
    """
    root = Path(log_dir)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()

    # system.log — загальні повідомлення INFO+
    root_logger.addHandler(
        _make_handler(root / "system.log", fmt=_FMT_FULL, level=logging.INFO)
    )

    # errors.log — ERROR+ з усього (агрегований)
    root_logger.addHandler(
        _make_handler(root / "errors.log", fmt=_FMT_FULL, level=logging.ERROR)
    )

    # Консоль (опційно)
    if console:
        ch = logging.StreamHandler()
        ch.setLevel(console_level)
        ch.setFormatter(logging.Formatter(_FMT_SHORT, datefmt=_DATE_FMT))
        root_logger.addHandler(ch)

    logging.getLogger("src.core.logging").info(
        f"Logging initialized (Retention: 5 days) → {root.resolve()}"
    )