"""
inventory/model.py — базові класи і DynamicInventories.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.mangabuff.reader.inventory import ReaderInventory
    from src.mangabuff.personal.inventory import PersonalInventory
    from src.mangabuff.manga_load.inventory import LoaderInventory
    from src.mangabuff.alliance.inventory import AllianceInventory
    from src.mangabuff.daily.inventory import DailyInventory
    from src.mangabuff.quiz.inventory import QuizInventory
    from src.mangabuff.mining.inventory import MiningInventory
    from src.mangabuff.manga_load.inventory import CatalogLoaderInventory


# ─────────────────────────────────────────────────────────────────────────────
# BaseInventory
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BaseInventory:
    data: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def delete(self, key: str) -> None:
        self.data.pop(key, None)

    def update(self, patch: dict[str, Any]) -> None:
        self.data.update(patch)


# ─────────────────────────────────────────────────────────────────────────────
# DynamicInventories
# ─────────────────────────────────────────────────────────────────────────────

class DynamicInventories:
    """
    Контейнер інвентарів, що збирається InventoryFactory.build().

    Атрибути записуються через звичайний setattr — тому НЕ використовуємо
    @property (вони блокують setattr без сеттера).

    Анотації нижче існують ТІЛЬКИ для IDE/mypy (TYPE_CHECKING),
    в runtime їх немає — звернення йде напряму через __dict__.
    """
    if TYPE_CHECKING:
        personal: "PersonalInventory"
        alliance: "AllianceInventory"
        reader:   "ReaderInventory"
        loader:   "LoaderInventory"
        daily:    "DailyInventory"
        quiz:     "QuizInventory"
        mining:     "MiningInventory"
        catalog_loader: "CatalogLoaderInventory"

    def __repr__(self) -> str:
        parts = " ".join(
            repr(v) for k, v in self.__dict__.items() if not k.startswith("_")
        )
        return f"<Inventories {parts}>"


# Alias
Inventories = DynamicInventories


# ─────────────────────────────────────────────────────────────────────────────
# INVENTORY_REGISTRY — проксі до inventory_factory.registry
# ─────────────────────────────────────────────────────────────────────────────

class _RegistryProxy(dict):  # type: ignore[type-arg]

    def __getitem__(self, key: str) -> tuple[str, type[BaseInventory]]:  # type: ignore[override]
        from src.core.inventory.factory import inventory_factory
        return inventory_factory.registry[key]

    def get(  # type: ignore[override]
        self,
        key: str,
        default: tuple[str, type[BaseInventory]] | None = None,
    ) -> tuple[str, type[BaseInventory]] | None:
        from src.core.inventory.factory import inventory_factory
        return inventory_factory.registry.get(key, default)

    def items(self) -> Any:  # type: ignore[override]
        from src.core.inventory.factory import inventory_factory
        return inventory_factory.registry.items()

    def keys(self) -> Any:  # type: ignore[override]
        from src.core.inventory.factory import inventory_factory
        return inventory_factory.registry.keys()

    def values(self) -> Any:  # type: ignore[override]
        from src.core.inventory.factory import inventory_factory
        return inventory_factory.registry.values()

    def __contains__(self, key: object) -> bool:
        from src.core.inventory.factory import inventory_factory
        return key in inventory_factory.registry

    def __iter__(self) -> Any:  # type: ignore[override]
        from src.core.inventory.factory import inventory_factory
        return iter(inventory_factory.registry)

    def __len__(self) -> int:
        from src.core.inventory.factory import inventory_factory
        return len(inventory_factory.registry)


INVENTORY_REGISTRY: dict[str, tuple[str, type[BaseInventory]]] = _RegistryProxy()