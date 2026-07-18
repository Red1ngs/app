"""
queue_service/db.py — власна БД цього сервісу.

Так само, як і в app: жодних даних про акаунти/cookies/сесії тут немає —
це власність account-service. Тут лише те, що бізнес-специфічне саме для
цього сервісу (приклад нижче — журнал виконаних задач; замінити на реальну
схему, коли буде відомо, що саме сервіс рахує/зберігає).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DDL = """
CREATE TABLE IF NOT EXISTS task_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  TEXT    NOT NULL,
    action      TEXT    NOT NULL,
    status      TEXT    NOT NULL,
    detail      TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


def get_db(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(DDL)
    conn.commit()
    return conn
