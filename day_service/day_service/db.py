"""
day_service/db.py — власна БД цього сервісу.

Дві таблиці:
  accounts  — які акаунти зареєстровані і з яким base_time/jitter.
  day_runs  — журнал "новий день уже оголошено для account_id на day".
              Саме ця таблиця не дає сервісу задвоїти оповіщення при
              рестарті: перед публікацією в Redis завжди перевіряється
              (account_id, day) тут, і подія публікується лише якщо
              такого запису ще немає.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DDL = """
CREATE TABLE IF NOT EXISTS accounts (
    account_id      TEXT PRIMARY KEY,
    base_time       TEXT NOT NULL,
    jitter_minutes  INTEGER NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS day_runs (
    account_id   TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    day          TEXT NOT NULL,
    triggered_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (account_id, day)
);

CREATE TRIGGER IF NOT EXISTS trg_accounts_updated AFTER UPDATE ON accounts BEGIN
    UPDATE accounts SET updated_at = datetime('now') WHERE account_id = NEW.account_id;
END;
"""


def get_db(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(DDL)
    conn.commit()
    return conn


# ── Queries ───────────────────────────────────────────────────────────────────

def upsert_account(conn: sqlite3.Connection, account_id: str, base_time: str, jitter_minutes: int) -> None:
    conn.execute(
        """
        INSERT INTO accounts (account_id, base_time, jitter_minutes)
        VALUES (?, ?, ?)
        ON CONFLICT(account_id) DO UPDATE SET
            base_time = excluded.base_time,
            jitter_minutes = excluded.jitter_minutes,
            updated_at = datetime('now')
        """,
        (account_id, base_time, jitter_minutes),
    )
    conn.commit()


def delete_account(conn: sqlite3.Connection, account_id: str) -> bool:
    cur = conn.execute("DELETE FROM accounts WHERE account_id = ?", (account_id,))
    conn.commit()
    return cur.rowcount > 0


def get_account(conn: sqlite3.Connection, account_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM accounts WHERE account_id = ?", (account_id,)
    ).fetchone()


def list_accounts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM accounts ORDER BY account_id").fetchall()


def last_triggered_day(conn: sqlite3.Connection, account_id: str) -> str | None:
    row = conn.execute(
        "SELECT day FROM day_runs WHERE account_id = ? ORDER BY day DESC LIMIT 1",
        (account_id,),
    ).fetchone()
    return row["day"] if row else None


def has_triggered(conn: sqlite3.Connection, account_id: str, day: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM day_runs WHERE account_id = ? AND day = ?",
        (account_id, day),
    ).fetchone()
    return row is not None


def record_trigger(conn: sqlite3.Connection, account_id: str, day: str) -> bool:
    """
    Ідемпотентний запис. Повертає True, якщо запис справді додано (тобто
    подію треба публікувати), False — якщо для (account_id, day) вже
    було зафіксовано раніше (напр. після швидкого рестарту сервісу).
    """
    try:
        conn.execute(
            "INSERT INTO day_runs (account_id, day) VALUES (?, ?)",
            (account_id, day),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
