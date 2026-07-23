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

from src.core.inventory.model import BaseInventory


@dataclass
class LoaderInventory(BaseInventory):
    """
    Зберігає стан LoaderProfession між запусками.

    catalog_page — фікс #6: кожен акаунт пам'ятає свою сторінку каталогу
                   незалежно від кількості манг у спільній БД.
    """

    @property
    def catalog_page(self) -> int:
        """Остання успішно оброблена сторінка каталогу (1-based)."""
        return int(self.data.get("catalog_page", 1))

    @catalog_page.setter
    def catalog_page(self, value: int) -> None:
        self.data["catalog_page"] = max(1, value)

    def __repr__(self) -> str:
        return f"<LoaderInventory catalog_page={self.catalog_page}>"