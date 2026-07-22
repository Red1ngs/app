"""
utils/log_section.py — друкує розділювач у лог.

Використовує той самий контекстний логер що і HTTP-logging,
тому розділювачі автоматично потрапляють у правильний файл акаунта.

Використання:
    from src.utils.log_section import section

    section("auth")
    section("task: claim_daily_bonus")
"""
from __future__ import annotations

from src.utils.logging import _log


def section(title: str, width: int = 48) -> None:
    pad = width - len(title) - 3
    _log().info(f"── {title} {'─' * max(pad, 2)}")