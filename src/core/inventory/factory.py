"""
core/inventory/factory.py — фабрика інвентарів.

Конкретні інвентарі реєструються в прикладному шарі:

    from src.core.inventory.factory import inventory_factory
    inventory_factory.register("personal", "personal", PersonalInventory)

Після реєстрації:
    inventories = inventory_factory.build()
    inventories.personal   # PersonalInventory
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Type

if TYPE_CHECKING:
    from src.core.inventory.model import BaseInventory, Inventories


class InventoryFactory:
    def __init__(self) -> None:
        self._registry: dict[str, tuple[str, "Type[BaseInventory]"]] = {}

    def register(self, kind: str, attr: str, cls: "Type[BaseInventory]") -> None:
        self._registry[kind] = (attr, cls)

    def build(self) -> "Inventories":
        from src.core.inventory.model import DynamicInventories
        inv = DynamicInventories()
        for _kind, (attr, cls) in self._registry.items():
            setattr(inv, attr, cls())
        return inv  # type: ignore[return-value]

    @property
    def registry(self) -> dict[str, tuple[str, "Type[BaseInventory]"]]:
        return dict(self._registry)

    def get(self, kind: str) -> "tuple[str, Type[BaseInventory]] | None":
        return self._registry.get(kind)

    def kinds(self) -> list[str]:
        return list(self._registry.keys())

    def __repr__(self) -> str:
        return f"<InventoryFactory kinds={list(self._registry)}>"


inventory_factory = InventoryFactory()