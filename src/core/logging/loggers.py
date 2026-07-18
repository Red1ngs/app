"""
core/logging/loggers.py — фабрика іменованих логерів.

Кожен акаунт отримує власний файл при першому зверненні.
Повторні виклики повертають той самий logger (handlers не дублюються).

Використання:
    from src.core.logging.loggers import get_logger, get_account_logger, get_task_logger

    log = get_logger("scheduler")       # → logs/scheduler.log
    log = get_account_logger("acc_01")  # → logs/accounts/acc_01.log
    log = get_task_logger("acc_01")     # → logs/tasks/acc_01_tasks.log
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

_LOG_DIR      = Path("logs")
_MAX_BYTES    = 10 * 1024 * 1024
_BACKUP_COUNT = 5
_DATE_FMT     = "%Y-%m-%d %H:%M:%S"

# Єдиний формат для всіх — ім'я логера завжди видно
_FMT = "%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s"

_initialized: set[str] = set()


def configure_log_dir(path: str | Path) -> None:
    """Змінює базову папку логів. Викликати ДО першого get_*_logger."""
    global _LOG_DIR
    _LOG_DIR = Path(path)


def _attach(
    logger: logging.Logger,
    path: Path,
    fmt: str,
    level: int = logging.DEBUG,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(
        path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    h.setLevel(level)
    h.setFormatter(logging.Formatter(fmt, datefmt=_DATE_FMT))
    logger.addHandler(h)


def get_logger(name: str) -> logging.Logger:
    """
    Системний логер. Пише у system.log / errors.log через root (propagate).
    Окремого файлу не створює — всі системні логи агрегуються в system.log.
    """
    full_name = f"src.{name}"
    logger = logging.getLogger(full_name)
    if full_name not in _initialized:
        _initialized.add(full_name)
        logger.setLevel(logging.DEBUG)
        logger.propagate = True
    return logger


def get_account_logger(account_id: str) -> logging.Logger:
    """
    Логер акаунта → logs/accounts/{account_id}.log.
    ERROR автоматично також у logs/errors.log через root.
    """
    name = f"src.account.{account_id}"
    logger = logging.getLogger(name)
    if name not in _initialized:
        _initialized.add(name)
        logger.setLevel(logging.DEBUG)
        _attach(logger, _LOG_DIR / "accounts" / f"{account_id}.log", _FMT)
        logger.propagate = True
    return logger


def get_task_logger(account_id: str) -> logging.Logger:
    """
    Логер тасків і HTTP → logs/tasks/{account_id}_tasks.log.
    Сюди ж потрапляють всі httpx event-hooks через set_http_logger().
    """
    name = f"src.tasks.{account_id}"
    logger = logging.getLogger(name)
    if name not in _initialized:
        _initialized.add(name)
        logger.setLevel(logging.DEBUG)
        _attach(logger, _LOG_DIR / "tasks" / f"{account_id}_tasks.log", _FMT)
        logger.propagate = True
    return logger


def get_scheduler_logger() -> logging.Logger:
    """Спеціальний логер для Scheduler."""
    return get_logger("runtime.scheduler")