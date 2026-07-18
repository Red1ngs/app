"""
reader/inventory.py — типізовані інвентарі.

Правило для data в BaseInventory:
  ЗБЕРІГАТИ   — те що бот змінює сам своїми діями
  НЕ ЗБЕРІГАТИ — те що змінює сайт (reputation тощо) → через initial_sync
  ВИНЯТОК      — is_banned: критично зберігати

Зміна архітектури:
  Конкретні інвентарі реєструються через inventory_factory в прикладному
  шарі і кладуться на DynamicInventories динамічно.
  INVENTORY_REGISTRY — проксі до inventory_factory.registry для зворотної
  сумісності.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import (
    Any
)

from src.core.inventory.model import BaseInventory


# ─────────────────────────────────────────────────────────────────────────────
# ReaderInventory
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ReaderInventory(BaseInventory):
    """
    Персистентний стан Reader-а.

    active_mode = "" означає «використати default_mode з конфігу».

    slot_counts      — {slot_name: count} — скільки разів сьогодні
                       нагорода цього слота вже зібрана.
    slot_counts_date — дата (YYYY-MM-DD) до якої належать slot_counts.
                       Якщо дата не збігається з today() — дані застарілі.
    """

    @property
    def reading_params(self) -> dict[str, Any]:
        return self.data.get("reading_params", {})

    @reading_params.setter
    def reading_params(self, value: dict[str, Any]) -> None:
        self.data["reading_params"] = value

    @property
    def active_mode(self) -> str:
        return self.data.get("active_mode", "")

    @active_mode.setter
    def active_mode(self, value: str) -> None:
        self.data["active_mode"] = value

    # ── Slot counts ───────────────────────────────────────────────────────────

    @property
    def slot_counts_date(self) -> str:
        return self.data.get("slot_counts_date", "")

    @property
    def slot_counts(self) -> dict[str, int]:
        """Повертає {} якщо дата застаріла."""
        from src.utils.time import today
        if self.slot_counts_date != today():
            return {}
        return dict(self.data.get("slot_counts", {}))

    def get_slot_count(self, slot_name: str) -> int:
        return self.slot_counts.get(slot_name, 0)

    def increment_slot_count(self, slot_name: str) -> int:
        """
        +1 до лічильника слота. Auto-reset якщо новий день.
        Повертає нове значення.
        """
        from src.utils.time import today
        t = today()
        if self.slot_counts_date != t:
            self.data["slot_counts"] = {}
            self.data["slot_chapters_spent"] = {}
            self.data["slot_counts_date"] = t
        counts: dict[str, int] = self.data.setdefault("slot_counts", {})
        counts[slot_name] = counts.get(slot_name, 0) + 1
        return counts[slot_name]

    def reset_slot_counts(self) -> None:
        """Примусовий скид після daily.claimed."""
        from src.utils.time import today
        self.data["slot_counts"] = {}
        self.data["slot_counts_date"] = today()
        self.data["slot_chapters_spent"] = {}

    # ── Slot chapters spent ───────────────────────────────────────────────────

    @property
    def slot_chapters_spent(self) -> dict[str, int]:
        """
        Кількість глав витрачених на кожен слот сьогодні.
        Повертає {} якщо дата застаріла (той самий date-guard що у slot_counts).
        """
        from src.utils.time import today
        if self.slot_counts_date != today():
            return {}
        return dict(self.data.get("slot_chapters_spent", {}))

    def add_slot_chapters_spent(self, slot_name: str, chapters: int) -> int:
        """
        Додає `chapters` до лічильника витрачених глав для слота.
        Auto-reset якщо новий день (разом з slot_counts через спільний date-guard).
        Повертає нове значення.
        """
        from src.utils.time import today
        t = today()
        if self.slot_counts_date != t:
            self.data["slot_counts"] = {}
            self.data["slot_chapters_spent"] = {}
            self.data["slot_counts_date"] = t
        spent: dict[str, int] = self.data.setdefault("slot_chapters_spent", {})
        spent[slot_name] = spent.get(slot_name, 0) + chapters
        return spent[slot_name]

    def __repr__(self) -> str:
        p = self.reading_params
        return (
            f"<ReaderInventory "
            f"mode={self.active_mode or 'default'!r} "
            f"limit={p.get('limit', 2)} "
            f"slot_counts={self.slot_counts}>"
        )