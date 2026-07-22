from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from src.core.inventory.model import BaseInventory


@dataclass
class MiningInventory(BaseInventory):
    
    @property
    def ore(self) -> str:
        return str(self.data.get("ore"))
    
    @ore.setter
    def ore(self, value: str | int) -> None:
        self.data["ore"] = str(value)
        
    @property
    def hits_left(self) -> Optional[int]:
        value = self.data.get("hits_left")
        return int(value) if value is not None else None
    
    @hits_left.setter
    def hits_left(self, value: int) -> None:
        self.data["hits_left"] = int(value)
        
    @property
    def hits_used(self) -> str:
        return str(self.data.get("hits_used"))
    
    @hits_used.setter
    def hits_used(self, value: str | int) -> None:
        self.data["hits_used"] = str(value)
        
    @property
    def max_hits(self) -> str:
        return str(self.data.get("max_hits"))
    
    @max_hits.setter
    def max_hits(self, value: str | int) -> None:
        self.data["max_hits"] = str(value)
        
    @property
    def added(self) -> str:
        return str(self.data.get("added"))
    
    @added.setter
    def added(self, value: str | int) -> None:
        self.data["added"] = str(value)
        
    @property
    def last_daily_complete(self) -> str:
        return str(self.data.get("last_daily_complete"))
    
    @last_daily_complete.setter
    def last_daily_complete(self, value: str | int) -> None:
        self.data["last_daily_complete"] = str(value)
        
    @property
    def mining_complete(self) -> bool:
        return bool(self.data.get("mining_complete"))
    
    @mining_complete.setter
    def mining_complete(self, value: bool) -> None:
        self.data["mining_complete"] = value
        
    @property
    def upgrade_cost(self) -> Optional[int]:
        value = self.data.get("upgrade_cost")
        return int(value) if value is not None else None
    
    @upgrade_cost.setter
    def upgrade_cost(self, value: int) -> None:
        self.data["upgrade_cost"] = int(value)
        
    @property
    def upgrade_level(self) -> Optional[int]:
        value = self.data.get("upgrade_level")
        return int(value) if value is not None else None
    
    @upgrade_level.setter
    def upgrade_level(self, value: int) -> None:
        self.data["upgrade_level"] = int(value)
        
    @property
    def upgrade_max(self) -> Optional[int]:
        value = self.data.get("upgrade_max")
        return int(value) if value is not None else None

    @upgrade_max.setter
    def upgrade_max(self, value: int) -> None:
        self.data["upgrade_max"] = int(value)

    @property
    def power_cost(self) -> Optional[int]:
        value = self.data.get("power_cost")
        return int(value) if value is not None else None

    @power_cost.setter
    def power_cost(self, value: int) -> None:
        self.data["power_cost"] = int(value)

    @property
    def power_bought(self) -> bool:
        return bool(self.data.get("power_bought"))

    @power_bought.setter
    def power_bought(self, value: bool) -> None:
        self.data["power_bought"] = value
        
    @property
    def exchange_diamond_cost(self) -> Optional[int]:
        value = self.data.get("exchange_diamond_cost")
        return int(value) if value is not None else None

    @exchange_diamond_cost.setter
    def exchange_diamond_cost(self, value: int) -> None:
        self.data["exchange_diamond_cost"] = int(value)

    @property
    def exchange_diamonds_get(self) -> Optional[int]:
        value = self.data.get("exchange_diamonds_get")
        return int(value) if value is not None else None

    @exchange_diamonds_get.setter
    def exchange_diamonds_get(self, value: int) -> None:
        self.data["exchange_diamonds_get"] = int(value)

    @property
    def mining_params(self) -> dict[str, Any]:
        return self.data.get("mining_params", {})
    
    @mining_params.setter
    def mining_params(self, value: dict[str, Any]) -> None:
        self.data["mining_params"] = value
        
    @property
    def last_mining_claimed(self) -> Optional[str]:
        value = self.data.get("last_mining_claimed")
        return value

    @last_mining_claimed.setter
    def last_mining_claimed(self, value: str) -> None:
        self.data["last_mining_claimed"] = value
        