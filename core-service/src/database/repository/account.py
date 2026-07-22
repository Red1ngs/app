"""
database/repository/account.py

CRUD для таблиці accounts.

Ключові рішення:
  - professions зберігається як JSON-масив у TEXT-стовпці.
  - Порядок елементів масиву = пріоритет (індекс 0 — найвищий).
  - Всі write-методи атомарні (один INSERT/UPDATE + commit під lock).
  - Метод set_professions є єдиним «truth source» для запису;
    add/remove — тонкі обгортки навколо нього.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Optional

from src.database.DTO.account import AccountRow


class AccountRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    # ── Reads ─────────────────────────────────────────────────────────────────

    def get(self, account_id: str) -> Optional[AccountRow]:
        row = self._conn.execute(
            "SELECT id, email, professions, updated_at FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        return self._to_model(row) if row else None

    def get_all_accounts(self) -> list[AccountRow]:
        rows = self._conn.execute(
            "SELECT id, email, professions, updated_at FROM accounts ORDER BY id"
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def get_by_email(self, email: str) -> Optional[AccountRow]:
        """Знайти акаунт за email."""
        row = self._conn.execute(
            "SELECT id, email, professions, updated_at FROM accounts WHERE email = ?",
            (email,),
        ).fetchone()
        return self._to_model(row) if row else None

    # ── Writes ────────────────────────────────────────────────────────────────

    def upsert(
        self,
        account_id:  str,
        email:       str,
        professions: Optional[list[str]] = None,
    ) -> None:
        """Створює або оновлює акаунт. professions=None → зберігає наявний список.
        
        Raises:
            ValueError: Якщо email вже зайнято іншим аккаунтом.
        """
        with self._lock:
            # Перевіримо чи email вже займає інший account
            existing = self.get_by_email(email)
            if existing and existing.id != account_id:
                raise ValueError(
                    f"Email '{email}' вже зареєстрований для аккаунту '{existing.id}'. "
                    f"Використайте інший email або видаліть попередній аккаунт."
                )
            
            if professions is None:
                # Не чіпаємо professions якщо не передано явно
                self._conn.execute(
                    """
                    INSERT INTO accounts (id, email)
                    VALUES (?, ?)
                    ON CONFLICT(id) DO UPDATE SET email = excluded.email
                    """,
                    (account_id, email),
                )
            else:
                self._conn.execute(
                    """
                    INSERT INTO accounts (id, email, professions)
                    VALUES (?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        email       = excluded.email,
                        professions = excluded.professions
                    """,
                    (account_id, email, json.dumps(professions, ensure_ascii=False)),
                )
            self._conn.commit()

    def set_professions(self, account_id: str, professions: list[str]) -> None:
        """
        Повністю замінює список профессій.
        Порядок елементів = пріоритет (0-й — найвищий).
        """
        with self._lock:
            self._conn.execute(
                "UPDATE accounts SET professions = ? WHERE id = ?",
                (json.dumps(professions, ensure_ascii=False), account_id),
            )
            self._conn.commit()

    def add_profession(
        self,
        account_id:   str,
        profession:   str,
        *,
        priority:     int = -1,
    ) -> list[str]:
        """
        Додає profession до списку якщо її ще немає.

        priority=-1  → додати в кінець (найнижчий пріоритет)
        priority=0   → вставити першою (найвищий пріоритет)
        priority=N   → вставити на позицію N

        Повертає оновлений список.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT professions FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            if row is None:
                return []
            profs = AccountRow.parse_professions(row["professions"])
            if profession in profs:
                return profs  # idempotent

            if priority < 0 or priority >= len(profs):
                profs.append(profession)
            else:
                profs.insert(priority, profession)

            self._conn.execute(
                "UPDATE accounts SET professions = ? WHERE id = ?",
                (json.dumps(profs, ensure_ascii=False), account_id),
            )
            self._conn.commit()
            return profs

    def remove_profession(self, account_id: str, profession: str) -> list[str]:
        """
        Видаляє profession зі списку (idempotent).
        Повертає оновлений список.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT professions FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            if row is None:
                return []
            profs = AccountRow.parse_professions(row["professions"])
            profs = [p for p in profs if p != profession]
            self._conn.execute(
                "UPDATE accounts SET professions = ? WHERE id = ?",
                (json.dumps(profs, ensure_ascii=False), account_id),
            )
            self._conn.commit()
            return profs

    def set_active(self, account_id: str, active: bool) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE accounts SET is_active = ? WHERE id = ?",
                (int(active), account_id),
            )
            self._conn.commit()

    # ── Admin / monitoring ────────────────────────────────────────────────────

    def summary(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, email, professions, updated_at FROM accounts ORDER BY id"
        ).fetchall()
        return [
            {
                "id":         r["id"],
                "email":      r["email"],
                "professions": AccountRow.parse_professions(r["professions"]),
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]

    # ── Internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _to_model(row: sqlite3.Row) -> AccountRow:
        return AccountRow(
            id=row["id"],
            email=row["email"],
            professions=AccountRow.parse_professions(row["professions"]),
            updated_at=row["updated_at"],
        )
