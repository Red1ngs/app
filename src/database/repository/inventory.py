from __future__ import annotations
import json
import sqlite3
from typing import Any

from src.core.inventory.factory import inventory_factory
from src.core.inventory.model import DynamicInventories

from src.core.logging.loggers import get_logger
log = get_logger("db.inventory")

class InventoryRepository: # Перейменували для відповідності стилю
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def load(self, account_id: str) -> DynamicInventories:
        """Завантажує інвентар для конкретного акаунта."""
        inventories = inventory_factory.build()
        registry = inventory_factory.registry

        rows = self._conn.execute(
            "SELECT kind, data FROM inventory WHERE account_id = ?",
            (account_id,),
        ).fetchall()

        log.debug(f"[{account_id}] load: found {len(rows)} records")
        for row in rows:
            entry = registry.get(row["kind"])
            if entry:
                attr, _ = entry
                inv = getattr(inventories, attr)
                inv.data = json.loads(row["data"])
                log.debug(f"[{account_id}] loaded {row['kind']!r}: {inv.data}")
        return inventories

    def save(self, account_id: str, inventories: DynamicInventories) -> None:
        """Зберігає весь об'єкт інвентарю в БД."""
        registry = inventory_factory.registry
        
        for kind, (attr, _) in registry.items():
            inv = getattr(inventories, attr)
            
            # Якщо даних немає (порожній словник), ми просто пропускаємо цей тип.
            # Це запобігає перезапису існуючих в БД даних "пустотою".
            if not inv.data:
                # Змінюємо WARNING на DEBUG, щоб не лякати користувача, 
                # бо для багатьох модулів це нормальний стан.
                log.debug(f"[{account_id}] Skipping empty inventory kind: {kind!r}")
                continue 
                
            log.debug(f"[{account_id}] saving {kind!r}: {inv.data}")
            self._upsert_kind(account_id, kind, inv.data)
            
        self._conn.commit()

    def _upsert_kind(self, account_id: str, kind: str, data: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT INTO inventory (account_id, kind, data) VALUES (?, ?, ?)
            ON CONFLICT(account_id, kind) DO UPDATE SET data = excluded.data
            """,
            (account_id, kind, json.dumps(data, ensure_ascii=False)),
        )