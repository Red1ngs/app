"""
migrate_reads_schema.py — переносить БД зі старої схеми account_reads (chapter_id)
на нову (translit_name + chapter_num + volume).

Нова схема зберігає історію прочитань незалежно від таблиць chapters/mangas:
видалення манги чи глави більше не видаляє record про те, що акаунт її прочитав.

Скрипт НІЧОГО не змінює у старому файлі БД — читає його і пише нову БД
за новою схемою (взятою з src/database/ddl.py, тобто всі таблиці й індекси
будуть актуальними, а не просто скопійованою старою схемою).

Використання:
    python migrate_reads_schema.py <шлях_до_старої_БД> [шлях_до_нової_БД]

Якщо шлях до нової БД не вказано — створюється поруч зі старою,
з суфіксом ".migrated.db" (наприклад bot_state.db -> bot_state.db.migrated.db).

Після успішної міграції: зупиніть бота, підмініть стару БД новою (наприклад
перейменувавши new -> old після резервної копії), і запускайте оновлений код.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

DDL = """
-- УВАГА: таблиця `sessions` нижче більше НЕ використовується бізнес-сервісом
-- (cookies/browser fingerprint тепер власність account-service, окрема БД).
-- Лишена як є заради БД, що вже існують у проді — видалення відкладено,
-- щоб не ускладнювати міграцію. Новий код не повинен на неї покладатись.

-- Accounts
CREATE TABLE IF NOT EXISTS accounts (
    id                TEXT PRIMARY KEY,
    email             TEXT NOT NULL UNIQUE,
    -- JSON array: '["reader","daily"]'  (замінює старе TEXT profession)
    professions       TEXT NOT NULL DEFAULT '[]',
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS inventory (
    account_id  TEXT    NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    kind        TEXT    NOT NULL,
    data        TEXT    NOT NULL DEFAULT '{}',
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (account_id, kind)
);

CREATE TABLE IF NOT EXISTS sessions (
    account_id  TEXT    PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    cookies     TEXT    NOT NULL DEFAULT '{}',
    browser     TEXT    NOT NULL DEFAULT '{}',
    is_valid    INTEGER NOT NULL DEFAULT 1,
    saved_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  TEXT    NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    kind        TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending',
    payload     TEXT    NOT NULL DEFAULT '{}',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Manga
CREATE TABLE IF NOT EXISTS mangas (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    data_id       INTEGER NOT NULL UNIQUE,
    translit_name TEXT    NOT NULL,
    name          TEXT    NOT NULL,
    rating        TEXT    NOT NULL DEFAULT '',
    info          TEXT    NOT NULL DEFAULT '',
    image         TEXT    NOT NULL DEFAULT '',
    views         INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chapters (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    data_id      INTEGER NOT NULL UNIQUE,
    manga_id     INTEGER NOT NULL REFERENCES mangas(id) ON DELETE CASCADE,
    chapter_num  REAL    NOT NULL,
    volume       INTEGER NOT NULL,
    date         TEXT,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- account_reads навмисно НЕ має FK на mangas/chapters: ідентифікація прочитаної
-- глави тримається на (translit_name, chapter_num, volume) — стабільних "природних"
-- ключах з боку сайту, а не на внутрішньому chapters.id. Завдяки цьому видалення
-- манги чи глави (і будь-який ON DELETE CASCADE на chapters/mangas) більше НЕ
-- зачіпає історію прочитань акаунта.
CREATE TABLE IF NOT EXISTS account_reads (
    account_id     TEXT    NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    translit_name  TEXT    NOT NULL,
    chapter_num    REAL    NOT NULL,
    volume         INTEGER NOT NULL,
    read_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (account_id, translit_name, chapter_num, volume)
);

-- Індекси
CREATE INDEX IF NOT EXISTS idx_events_pending ON events(account_id, kind, status);
CREATE INDEX IF NOT EXISTS idx_chapters_manga_lookup ON chapters(manga_id, chapter_num);
CREATE INDEX IF NOT EXISTS idx_account_reads_lookup ON account_reads(account_id);
CREATE INDEX IF NOT EXISTS idx_account_reads_manga_lookup ON account_reads(account_id, translit_name);
CREATE INDEX IF NOT EXISTS idx_mangas_translit_name ON mangas(translit_name);

-- Тригери updated_at
CREATE TRIGGER IF NOT EXISTS trg_accounts_updated AFTER UPDATE ON accounts BEGIN
    UPDATE accounts SET updated_at = datetime('now') WHERE id = NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_inventory_updated AFTER UPDATE ON inventory BEGIN
    UPDATE inventory SET updated_at = datetime('now')
    WHERE account_id = NEW.account_id AND kind = NEW.kind;
END;
CREATE TRIGGER IF NOT EXISTS trg_events_updated AFTER UPDATE ON events BEGIN
    UPDATE events SET updated_at = datetime('now') WHERE id = NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_sessions_updated AFTER UPDATE ON sessions BEGIN
    UPDATE sessions SET updated_at = datetime('now') WHERE account_id = NEW.account_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_mangas_updated AFTER UPDATE ON mangas BEGIN
    UPDATE mangas SET updated_at = datetime('now') WHERE id = NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_chapters_updated AFTER UPDATE ON chapters BEGIN
    UPDATE chapters SET updated_at = datetime('now') WHERE id = NEW.id;
END;
"""


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _copy_table_plain(src: sqlite3.Connection, dst: sqlite3.Connection, table: str) -> int:
    """Копіює таблицю 1:1 — колонки не змінились між старою і новою схемою."""
    if not _table_exists(src, table):
        return 0
    cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
    rows = src.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
    if not rows:
        return 0
    placeholders = ", ".join("?" * len(cols))
    dst.executemany(
        f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
        rows,
    )
    return len(rows)


def _migrate_account_reads(old_conn: sqlite3.Connection, new_conn: sqlite3.Connection) -> None:
    print("→ Перебудовую account_reads (chapter_id → translit_name/chapter_num/volume) ...")

    if not _table_exists(old_conn, "account_reads"):
        print("  (таблиці account_reads не було в старій БД — пропускаю)")
        return

    old_cols = {row[1] for row in old_conn.execute("PRAGMA table_info(account_reads)")}

    if "chapter_id" not in old_cols:
        # Стара БД вже мала нову схему (наприклад скрипт запускають повторно) —
        # просто копіюємо як є.
        n = _copy_table_plain(old_conn, new_conn, "account_reads")
        new_conn.commit()
        print(f"  account_reads вже в новій схемі, скопійовано {n} рядків без змін")
        return

    rows = old_conn.execute(
        """
        SELECT
            ar.account_id   AS account_id,
            m.translit_name AS translit_name,
            c.chapter_num   AS chapter_num,
            c.volume        AS volume,
            ar.read_at      AS read_at
        FROM account_reads ar
        JOIN chapters c ON c.id = ar.chapter_id
        JOIN mangas   m ON m.id = c.manga_id
        """
    ).fetchall()

    skipped_row = old_conn.execute(
        """
        SELECT COUNT(*) FROM account_reads ar
        WHERE NOT EXISTS (SELECT 1 FROM chapters c WHERE c.id = ar.chapter_id)
        """
    ).fetchone()
    skipped = skipped_row[0] if skipped_row else 0

    if rows:
        new_conn.executemany(
            """
            INSERT OR IGNORE INTO account_reads
                (account_id, translit_name, chapter_num, volume, read_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (r["account_id"], r["translit_name"], r["chapter_num"], r["volume"], r["read_at"])
                for r in rows
            ],
        )
    new_conn.commit()

    print(f"  {len(rows)} рядків перенесено")
    if skipped:
        print(
            f"  ⚠ {skipped} рядків account_reads посилались на вже видалену главу "
            f"(chapter_id без відповідного chapters.id) — їх довелось пропустити, "
            f"бо в старій схемі translit_name/chapter_num/volume для них уже втрачені."
        )


def migrate(old_path: Path, new_path: Path) -> None:
    if new_path.exists():
        raise SystemExit(f"Файл призначення вже існує, оберіть інший шлях: {new_path}")

    old_conn = sqlite3.connect(str(old_path))
    old_conn.row_factory = sqlite3.Row

    new_conn = sqlite3.connect(str(new_path))
    new_conn.execute("PRAGMA foreign_keys=OFF")
    new_conn.executescript(DDL)
    new_conn.commit()

    for table in ("accounts", "inventory", "sessions", "events", "mangas", "chapters"):
        print(f"→ Копіюю {table} ...")
        n = _copy_table_plain(old_conn, new_conn, table)
        new_conn.commit()
        print(f"  {n} рядків")

    _migrate_account_reads(old_conn, new_conn)

    old_conn.close()
    new_conn.close()
    print(f"\n✅ Готово. Нова БД: {new_path}")
    print("Зупиніть бота і підмініть стару БД новим файлом перед наступним запуском.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("old_db", type=Path, help="шлях до існуючої (старої) БД")
    parser.add_argument(
        "new_db",
        type=Path,
        nargs="?",
        default=None,
        help="шлях до нової БД (за замовчуванням: <old_db>.migrated.db)",
    )
    args = parser.parse_args()

    old_path: Path = args.old_db
    if not old_path.exists():
        raise SystemExit(f"Не знайдено файл: {old_path}")

    new_path: Path = args.new_db or old_path.with_suffix(old_path.suffix + ".migrated.db")
    migrate(old_path, new_path)


if __name__ == "__main__":
    main()