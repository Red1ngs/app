from __future__ import annotations

import sqlite3
from pathlib import Path

from src.core.logging.loggers import get_logger

logger = get_logger("DatabaseInit")

DDL = """
-- Accounts
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    professions TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS inventory (
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (account_id, kind)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Manga
CREATE TABLE IF NOT EXISTS manga_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    translit_name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS mangas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    data_id INTEGER NOT NULL UNIQUE,
    manga_link_id INTEGER NOT NULL REFERENCES manga_links(id),
    name TEXT NOT NULL,
    rating TEXT NOT NULL DEFAULT '',
    info TEXT NOT NULL DEFAULT '',
    image TEXT NOT NULL DEFAULT '',
    views INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    data_id INTEGER NOT NULL UNIQUE,
    manga_id INTEGER NOT NULL REFERENCES mangas(id) ON DELETE CASCADE,
    chapter_num REAL NOT NULL,
    volume INTEGER NOT NULL,
    date TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- account_reads
CREATE TABLE IF NOT EXISTS account_reads (
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    manga_link_id INTEGER NOT NULL REFERENCES manga_links(id),
    chapter_num REAL NOT NULL,
    volume INTEGER NOT NULL,
    read_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (account_id, manga_link_id, chapter_num, volume)
);

-- Індекси
CREATE INDEX IF NOT EXISTS idx_events_pending ON events(account_id, kind, status);
CREATE INDEX IF NOT EXISTS idx_chapters_manga_lookup ON chapters(manga_id, chapter_num);
CREATE INDEX IF NOT EXISTS idx_account_reads_lookup ON account_reads(account_id);
CREATE INDEX IF NOT EXISTS idx_account_reads_manga_lookup ON account_reads(account_id, manga_link_id);
CREATE INDEX IF NOT EXISTS idx_mangas_link_lookup ON mangas(manga_link_id);
-- Гарантує, що один manga_link_id не може одночасно належати двом різним мангам
CREATE UNIQUE INDEX IF NOT EXISTS idx_mangas_link_unique ON mangas(manga_link_id);

-- Тригери updated_at
CREATE TRIGGER IF NOT EXISTS trg_accounts_updated AFTER UPDATE ON accounts BEGIN
    UPDATE accounts SET updated_at = datetime('now') WHERE id = NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_inventory_updated AFTER UPDATE ON inventory BEGIN
    UPDATE inventory SET updated_at = datetime('now') WHERE account_id = NEW.account_id AND kind = NEW.kind;
END;
CREATE TRIGGER IF NOT EXISTS trg_events_updated AFTER UPDATE ON events BEGIN
    UPDATE events SET updated_at = datetime('now') WHERE id = NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_mangas_updated AFTER UPDATE ON mangas BEGIN
    UPDATE mangas SET updated_at = datetime('now') WHERE id = NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_chapters_updated AFTER UPDATE ON chapters BEGIN
    UPDATE chapters SET updated_at = datetime('now') WHERE id = NEW.id;
END;
"""

def _apply_migrations(conn: sqlite3.Connection) -> None:
    """
    Ідемпотентні міграції окремих колонок. 
    Виконуються після основного DDL-скрипта.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(accounts)")}

    # Додаємо колонку views до mangas, якщо вона відсутня
    manga_cols = {row[1] for row in conn.execute("PRAGMA table_info(mangas)")}
    if "views" not in manga_cols:
        conn.execute("ALTER TABLE mangas ADD COLUMN views INTEGER NOT NULL DEFAULT 0")
        conn.commit()

    # Переносимо застарілу profession -> professions (JSON array)
    if "profession" in cols and "professions" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN professions TEXT NOT NULL DEFAULT '[]'")
        conn.execute("""
            UPDATE accounts
            SET professions = json_array(profession)
            WHERE profession IS NOT NULL AND profession != ''
        """)
        conn.commit()
    elif "professions" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN professions TEXT NOT NULL DEFAULT '[]'")
        conn.commit()


def _migrate_to_manga_links_schema(conn: sqlite3.Connection) -> None:
    """
    Мігрує стару структуру на схему з використанням manga_links
    та безповоротно видаляє непотрібну таблицю sessions.
    """
    # 1. Видаляємо таблицю sessions, якщо вона досі існує в базі
    conn.execute("DROP TABLE IF EXISTS sessions")
    conn.commit()

    # Перевіряємо, чи таблиця account_reads взагалі існує (для нових баз міграція не потрібна)
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='account_reads'"
    ).fetchone()
    if row is None:
        return

    mangas_cols = {r[1] for r in conn.execute("PRAGMA table_info(mangas)")}
    reads_cols = {r[1] for r in conn.execute("PRAGMA table_info(account_reads)")}

    # Якщо міграцію на manga_links вже було проведено раніше
    if "manga_link_id" in mangas_cols and "manga_link_id" in reads_cols:
        return

    logger.info("Запуск міграції БД на manga_links схему для оптимізації пам'яті...")

    # Тимчасово вимикаємо foreign keys на час перебудови таблиць
    conn.execute("PRAGMA foreign_keys=OFF")

    # Створюємо таблицю лінків
    conn.execute("""
        CREATE TABLE IF NOT EXISTS manga_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            translit_name TEXT UNIQUE NOT NULL
        )
    """)

    # Переносимо унікальні посилання з mangas та account_reads в нову таблицю
    conn.execute("""
        INSERT OR IGNORE INTO manga_links (translit_name)
        SELECT DISTINCT translit_name FROM mangas WHERE translit_name IS NOT NULL AND translit_name != ''
    """)
    conn.execute("""
        INSERT OR IGNORE INTO manga_links (translit_name)
        SELECT DISTINCT translit_name FROM account_reads WHERE translit_name IS NOT NULL AND translit_name != ''
    """)

    # Перебудовуємо таблицю mangas
    conn.execute("ALTER TABLE mangas RENAME TO old_mangas")
    conn.execute("""
        CREATE TABLE mangas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_id INTEGER NOT NULL UNIQUE,
            manga_link_id INTEGER NOT NULL REFERENCES manga_links(id),
            name TEXT NOT NULL,
            rating TEXT NOT NULL DEFAULT '',
            info TEXT NOT NULL DEFAULT '',
            image TEXT NOT NULL DEFAULT '',
            views INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        INSERT INTO mangas (id, data_id, manga_link_id, name, rating, info, image, views, created_at, updated_at)
        SELECT om.id, om.data_id, ml.id, om.name, om.rating, om.info, om.image, om.views, om.created_at, om.updated_at
        FROM old_mangas om
        JOIN manga_links ml ON om.translit_name = ml.translit_name
    """)
    conn.execute("DROP TABLE old_mangas")

    # Перебудовуємо таблицю account_reads
    conn.execute("ALTER TABLE account_reads RENAME TO old_account_reads")
    conn.execute("""
        CREATE TABLE account_reads (
            account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            manga_link_id INTEGER NOT NULL REFERENCES manga_links(id),
            chapter_num REAL NOT NULL,
            volume INTEGER NOT NULL,
            read_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (account_id, manga_link_id, chapter_num, volume)
        )
    """)
    conn.execute("""
        INSERT INTO account_reads (account_id, manga_link_id, chapter_num, volume, read_at)
        SELECT oar.account_id, ml.id, oar.chapter_num, oar.volume, oar.read_at
        FROM old_account_reads oar
        JOIN manga_links ml ON oar.translit_name = ml.translit_name
    """)
    conn.execute("DROP TABLE old_account_reads")

    # Вмикаємо foreign keys назад та зберігаємо транзакцію
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    logger.info("Міграцію на manga_links успішно завершено.")


def _check_account_reads_schema(conn: sqlite3.Connection) -> None:
    """
    Запобіжна перевірка: якщо використовується занадто стара схема
    з chapter_id, вимагаємо запустити ручний скрипт міграції.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='account_reads'"
    ).fetchone()
    if row is None:
        return

    reads_cols = {r[1] for r in conn.execute("PRAGMA table_info(account_reads)")}
    if "chapter_id" in reads_cols:
        raise RuntimeError(
            "БД використовує дуже стару схему account_reads (chapter_id). "
            "Перед запуском виконайте міграцію: "
            "python migrate_reads_schema.py <шлях_до_БД> [шлях_до_нової_БД]"
        )


def _cleanup_orphaned_manga_links(conn: sqlite3.Connection) -> None:
    """
    Видаляє записи в manga_links, на які ніхто не посилається ані з mangas,
    ані з account_reads. Такі "осиротілі" лінки з'являлись через старий баг
    в MangaRepository.upsert, коли зміна translit_name на сайті створювала
    НОВИЙ manga_link_id замість оновлення існуючого — старий лінк лишався
    висіти, а прив'язані до нього account_reads переставали матчитись
    з поточною мангою (глави випадали з get_chapter_sequence).
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='manga_links'"
    ).fetchone()
    if row is None:
        return

    orphans = conn.execute("""
        SELECT id, translit_name FROM manga_links
        WHERE id NOT IN (SELECT manga_link_id FROM mangas)
          AND id NOT IN (SELECT DISTINCT manga_link_id FROM account_reads)
    """).fetchall()

    if orphans:
        logger.warning(
            f"Знайдено {len(orphans)} осиротілих записів у manga_links "
            f"(нікуди не прив'язані): {[dict(o) for o in orphans]}. Видаляю."
        )
        conn.execute("""
            DELETE FROM manga_links
            WHERE id NOT IN (SELECT manga_link_id FROM mangas)
              AND id NOT IN (SELECT DISTINCT manga_link_id FROM account_reads)
        """)
        conn.commit()


def _repair_duplicate_manga_links(conn: sqlite3.Connection) -> None:
    """
    КРИТИЧНО виконувати ДО створення idx_mangas_link_unique.

    На БД, які застали старий баг (переприв'язка manga_links по translit_name
    замість data_id), могла виникнути ситуація, коли два РІЗНІ mangas-рядки
    (різні data_id) посилаються на ОДИН manga_link_id — наприклад, якщо стара
    манга X перейменувала слаг (лишивши свій старий link "вільним" по суті,
    хоч формально він ще належав їй), а потім якась нова манга Y отримала на
    сайті translit_name, що збігається зі старим слагом X, і ON CONFLICT
    підхопив чужий link. Це не лише порушує цілісність, а й означає, що
    account_reads від X могли помилково "рахуватись" як прочитані глави Y.

    Без цього кроку CREATE UNIQUE INDEX idx_mangas_link_unique впаде з
    IntegrityError і бот не зможе стартувати на пошкодженій БД.

    Найстаріший (за id) рядок лишає собі існуючий link, решта отримують
    новий тимчасовий manga_link (translit_name буде замінено на реальний
    автоматично при наступному звичайному upsert цієї манги).
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mangas'"
    ).fetchone()
    if row is None:
        return

    dup_links = conn.execute("""
        SELECT manga_link_id, COUNT(*) as cnt
        FROM mangas
        GROUP BY manga_link_id
        HAVING cnt > 1
    """).fetchall()

    if not dup_links:
        return

    logger.warning(
        f"[repair] Знайдено {len(dup_links)} manga_link_id, помилково спільних "
        f"для кількох різних манг. Розділяю на окремі лінки."
    )

    for dup in dup_links:
        link_id = dup["manga_link_id"]
        rows = conn.execute(
            "SELECT id, data_id FROM mangas WHERE manga_link_id = ? ORDER BY id",
            (link_id,)
        ).fetchall()

        kept = rows[0]
        logger.warning(
            f"[repair] link={link_id}: data_id={kept['data_id']} лишається на ньому "
            f"(найстаріший запис), решта отримують нові лінки"
        )

        for r in rows[1:]:
            temp_name = f"__dup_repair_{r['data_id']}__"
            cur = conn.execute(
                "INSERT OR IGNORE INTO manga_links (translit_name) VALUES (?)",
                (temp_name,)
            )
            new_link_row = conn.execute(
                "SELECT id FROM manga_links WHERE translit_name = ?", (temp_name,)
            ).fetchone()
            new_link_id = new_link_row["id"]
            conn.execute(
                "UPDATE mangas SET manga_link_id = ? WHERE id = ?",
                (new_link_id, r["id"])
            )
            logger.warning(
                f"[repair]   data_id={r['data_id']}: перенесено на новий link={new_link_id} "
                f"(тимчасовий translit_name={temp_name!r} — оновиться при наступному "
                f"звичайному upsert цієї манги; читання цієї манги, позначені ДО репеіру, "
                f"могли посилатись на неправильний link={link_id} і зараз виглядатимуть "
                f"як непрочитані — це неминучий наслідок вже пошкоджених даних, "
                f"новий баг більше не виникатиме)"
            )

    conn.commit()


def _report_orphaned_reads(conn: sqlite3.Connection) -> None:
    """
    Тільки діагностика, нічого не змінює. Показує manga_links з історією
    прочитань (account_reads), які не прив'язані до жодної ІСНУЮЧОЇ манги.

    Це ОЧІКУВАНО і нормально для манг, видалених через delete_by_data_id
    (саме так і задумано — читання переживають видалення). Але якщо тут
    з'являється щось несподіване — варто перевірити вручну, чи це не
    рештки старого бага, які варто відновити для конкретної манги.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='manga_links'"
    ).fetchone()
    if row is None:
        return

    rows = conn.execute("""
        SELECT ml.id, ml.translit_name, COUNT(ar.rowid) as reads_count
        FROM manga_links ml
        JOIN account_reads ar ON ar.manga_link_id = ml.id
        WHERE ml.id NOT IN (SELECT manga_link_id FROM mangas)
        GROUP BY ml.id
    """).fetchall()

    if rows:
        logger.info(
            f"[diag] {len(rows)} manga_links з історією читань не прив'язані до "
            f"поточних манг (нормально, якщо ці манги видалені навмисно): "
            f"{[dict(r) for r in rows]}"
        )


def get_db(path: str | Path = "bot_state.db") -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    
    # 1. Робимо перевірку застарілої схеми з chapter_id
    _check_account_reads_schema(conn)
    
    # 2. Переносимо дані на схему manga_links та остаточно видаляємо sessions
    _migrate_to_manga_links_schema(conn)

    # 3. Лагодимо дублікати manga_link_id (наслідок старого бага) — ОБОВ'ЯЗКОВО
    #    до executescript, інакше CREATE UNIQUE INDEX впаде на пошкоджених БД
    _repair_duplicate_manga_links(conn)

    # 4. Виконуємо DDL (створення нових таблиць, індексів — зокрема unique — і тригерів)
    conn.executescript(DDL)
    conn.commit()
    
    # 5. Застосовуємо дрібні міграції колонок (views, professions)
    _apply_migrations(conn)

    # 6. Прибираємо повністю осиротілі manga_links (без жодних посилань)
    _cleanup_orphaned_manga_links(conn)

    # 7. Діагностика (нічого не змінює) — лог про reads без поточної манги
    _report_orphaned_reads(conn)

    return conn