from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from src.core.inventory.model import BaseInventory


@dataclass
class AllianceInventory(BaseInventory):
    @property
    def name(self) -> str:
        return cast(str, self.data.get("name", ""))

    @property
    def rank(self) -> int:
        return cast(int, self.data.get("rank", 0))

    @property
    def shared_items(self) -> list[str]:
        return cast(list[str], self.data.get("shared_items", []))

    @property
    def member_count(self) -> int:
        return cast(int, self.data.get("member_count", 0))

    def __repr__(self) -> str:
        return f"<AllianceInventory name={self.name!r} rank={self.rank}>"