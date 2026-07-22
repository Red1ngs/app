"""
daily/inventory.py
"""
from __future__ import annotations

from dataclasses import dataclass

from src.core.inventory.model import BaseInventory


@dataclass
class DailyInventory(BaseInventory):
    """
    Весь стан зберігається виключно в self.data (dict), який
    InventoryRepository серіалізує в БД як JSON.

    Поля-датакласи (поза self.data) не персистуються — тому їх тут немає.
    """

    @property
    def day(self) -> int:
        """Поточний день стріку (календаря)."""
        return self.data.get("day", 1)

    @day.setter
    def day(self, value: int) -> None:
        self.data["day"] = value

    @property
    def can_claim_calendar(self) -> bool:
        """
        True — день стріку відомий, можна одразу збирати календарний бонус.
        False — треба спочатку спарсити сторінку /balance.

        Скидається в False після успішного claim або якщо бонус недоступний.
        """
        return self.data.get("can_claim_calendar", False)

    @can_claim_calendar.setter
    def can_claim_calendar(self, value: bool) -> None:
        self.data["can_claim_calendar"] = value
        
    @property
    def can_claim_daily(self) -> bool:
        return self.data.get("can_claim_daily", True)
    
    @can_claim_daily.setter
    def can_claim_daily(self, value: bool) -> None:
        self.data["can_claim_daily"] = value

    @property
    def last_daily_claimed(self) -> str:
        """UTC дата останнього збору звичайного щоденного бонусу."""
        last_daily_claimed = self.data.get("last_daily_claimed", "")
        return last_daily_claimed

    @last_daily_claimed.setter
    def last_daily_claimed(self, value: str) -> None:
        self.data["last_daily_claimed"] = value

    @property
    def last_calendar_claimed(self) -> str:
        """UTC дата останнього збору календарного (streak) бонусу."""
        last_calendar_claimed = self.data.get("last_calendar_claimed", "")
        return last_calendar_claimed

    @last_calendar_claimed.setter
    def last_calendar_claimed(self, value: str) -> None:
        self.data["last_calendar_claimed"] = value

    def __repr__(self) -> str:
        return (
            f"<DailyInventory "
            f"day={self.day} "
            f"can_claim={self.can_claim_calendar} "
            f"last_daily={self.last_daily_claimed} "
            f"last_cal={self.last_calendar_claimed}>"
        )