from __future__ import annotations

import sqlite3
import threading
from typing import Any, Optional

from src.database.DTO.manga import ChapterRow, MangaRow
from src.core.logging.loggers import get_logger

logger = get_logger("MangaRepository")

class MangaRepository:
    """Керування даними манг у БД."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._lock = threading.Lock()

    def get_by_data_id(self, data_id: int) -> Optional[MangaRow]:
        """Отримує мангу за її зовнішнім числовим ID (з сайту)."""
        row = self._conn.execute(
            "SELECT * FROM mangas WHERE data_id = ?", (data_id,)
        ).fetchone()
        return self._to_model(row) if row else None

    def get_by_translit_name(self, translit_name: str) -> Optional[MangaRow]:
        """Отримує мангу за її рядковим ID (з сайту)."""
        row = self._conn.execute(
            "SELECT * FROM mangas WHERE translit_name = ?", (translit_name,)
        ).fetchone()
        return self._to_model(row) if row else None

    def get_stale_mangas(self, days: int = 3, limit: int = 5) -> list[MangaRow]:
        """
        Повертає список манг, які не оновлювалися вказану кількість днів.
        Використовується для перевірки наявності нових глав на сайті.
        """
        rows = self._conn.execute(
            f"""
            SELECT * FROM mangas 
            WHERE datetime(updated_at) <= datetime('now', '-{days} days')
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def upsert(
        self,
        data_id: int,
        translit_name: str,
        name: str,
        rating: str = "",
        info: str = "",
        image: str = "",
        views: int = 0,
    ) -> int:
        """Створює або оновлює мангу. Повертає внутрішній ID БД (id)."""
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO mangas (data_id, translit_name, name, rating, info, image, views)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(data_id) DO UPDATE SET
                    translit_name   = excluded.translit_name,
                    name            = excluded.name,
                    rating          = excluded.rating,
                    info            = excluded.info,
                    image           = excluded.image,
                    views           = CASE WHEN excluded.views > 0
                                          THEN excluded.views
                                          ELSE mangas.views END
                RETURNING id
                """,
                (data_id, translit_name, name, rating, info, image, views),
            )
            res = cursor.fetchone()
            self._conn.commit()
            return res["id"]

    def update_views(self, data_id: int, views: int) -> None:
        """Оновлює кількість переглядів манги за її зовнішнім data_id."""
        with self._lock:
            self._conn.execute(
                "UPDATE mangas SET views = ? WHERE data_id = ?",
                (views, data_id),
            )
            self._conn.commit()
            
    def get_existing_data_ids(self, data_ids: list[int]) -> set[int]:
        if not data_ids:
            return set()
        placeholders = ",".join("?" * len(data_ids))
        rows = self._conn.execute(
            f"SELECT data_id FROM mangas WHERE data_id IN ({placeholders})",
            data_ids,
        ).fetchall()
        return {row["data_id"] for row in rows}

    def count(self) -> int:
        """Повертає загальну кількість манг у БД."""
        row = self._conn.execute("SELECT COUNT(*) FROM mangas").fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def _to_model(row: sqlite3.Row) -> MangaRow:
        return MangaRow(**dict(row))


class ChapterRepository:
    """Керування главами та історією їх прочитань."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._lock = threading.Lock()

    def get_chapter_sequence(
        self,
        account_id: str,
        limit: int,
        include_tags: Optional[list[str]] = None,
        exclude_tags: Optional[list[str]] = None
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """
        Шукає глави, які конкретний акаунт ЩЕ НЕ ЧИТАВ.
        "Прочитано" визначається за (translit_name, chapter_num, volume) —
        а не за внутрішнім chapters.id, щоб історія не залежала від того,
        чи глава досі існує в таблиці chapters.
        Повертає зовнішні data_id для манги та глав (для HTTP-запитів до сайту),
        а також translit_name/chapter_num/volume (для позначення прочитаною).
        """
        query = """
            SELECT
                m.translit_name,
                m.data_id    AS manga_data_id,
                c.data_id    AS chapter_data_id,
                c.chapter_num,
                c.volume
            FROM chapters c
            JOIN mangas m ON c.manga_id = m.id          -- manga_id — внутрішній FK
            LEFT JOIN account_reads ar
                ON ar.translit_name = m.translit_name
               AND ar.chapter_num   = c.chapter_num
               AND ar.volume        = c.volume
               AND ar.account_id    = ?
            WHERE ar.translit_name IS NULL
        """
        params: list[Any] = [account_id]

        if include_tags:
            for tag in include_tags:
                query += " AND (m.name LIKE ? OR m.info LIKE ?)"
                params.extend([f"%{tag}%", f"%{tag}%"])

        if exclude_tags:
            for tag in exclude_tags:
                query += " AND (m.name NOT LIKE ? AND m.info NOT LIKE ?)"
                params.extend([f"%{tag}%", f"%{tag}%"])

        query += """
            ORDER BY m.views DESC, c.chapter_num ASC, c.id ASC
            LIMIT ?
        """
        params.append(limit)

        rows = self._conn.execute(query, tuple(params)).fetchall()

        sequence: list[dict[str, Any]] = []
        mangas_set: set[str] = set()

        for row in rows:
            sequence.append({
                "manga_id":      row["manga_data_id"],    # зовнішній data_id манги (для сайту)
                "chapter_id":    row["chapter_data_id"],  # зовнішній data_id глави (для сайту)
                "translit_name": row["translit_name"],    # природний ключ для account_reads
                "chapter_num":   row["chapter_num"],       # природний ключ для account_reads
                "volume":        row["volume"],            # природний ключ для account_reads
            })
            mangas_set.add(row["translit_name"])
        logger.info(
            f"get_chapter_sequence: "
            f"account_id={account_id}, limit={limit}, "
            f"include_tags={include_tags}, exclude_tags={exclude_tags} → {len(sequence)} chapters, {len(mangas_set)} mangas"
        )
        return sequence, list(mangas_set)

    def mark_chapter_read(
        self,
        account_id: str,
        translit_name: str,
        chapter_num: float,
        volume: int,
    ) -> None:
        """
        Записує главу в історію як прочитану для даного акаунта.
        Ключ — (translit_name, chapter_num, volume), а не внутрішній id глави:
        запис лишається навіть якщо главу чи мангу згодом видалять з БД.
        """
        with self._lock:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO account_reads (account_id, translit_name, chapter_num, volume)
                VALUES (?, ?, ?, ?)
                """,
                (account_id, translit_name, float(chapter_num), int(volume))
            )
            self._conn.commit()

    def mark_mangas_read(self, account_id: str, translit_names: list[str]) -> int:
        if not translit_names:
            return 0
        placeholders = ",".join("?" * len(translit_names))
        query = f"""
            INSERT OR IGNORE INTO account_reads (account_id, translit_name, chapter_num, volume)
            SELECT ?, m.translit_name, c.chapter_num, c.volume
            FROM chapters c
            JOIN mangas m ON c.manga_id = m.id
            WHERE m.translit_name IN ({placeholders})
        """
        params = [account_id] + translit_names
        with self._lock:
            cursor = self._conn.execute(query, tuple(params))
            self._conn.commit()
            return cursor.rowcount
        
    def has_unread_chapters(self, account_id: str) -> bool:
        row = self._conn.execute(
            """
            SELECT 1
            FROM chapters c
            JOIN mangas m ON c.manga_id = m.id
            LEFT JOIN account_reads ar
                ON ar.translit_name = m.translit_name
               AND ar.chapter_num   = c.chapter_num
               AND ar.volume        = c.volume
               AND ar.account_id    = ?
            WHERE ar.translit_name IS NULL
            LIMIT 1
            """,
            (account_id,)
        ).fetchone()
        return row is not None

    def upsert(
        self,
        data_id: int,
        manga_id: int,
        chapter_num: float,
        volume: int,
        date: Optional[str] = None
    ) -> None:
        """Додає або оновлює одну главу."""
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO chapters (data_id, manga_id, chapter_num, volume, date)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(data_id) DO UPDATE SET
                    chapter_num = excluded.chapter_num,
                    volume      = excluded.volume,
                    date        = excluded.date
                """,
                (data_id, manga_id, float(chapter_num), int(volume), date),
            )
            self._conn.commit()

    def upsert_many(
        self,
        chapters_data: list[tuple[int, int, float, int, Optional[str]]]
    ) -> None:
        """
        Масове збереження глав. Очікує список кортежів:
        (data_id, manga_id, chapter_num, volume, date)
        Перед записом автоматично сортує глави: спочатку за volume, потім за chapter_num.
        """
        sorted_chapters = sorted(chapters_data, key=lambda x: (x[3], x[2]))

        with self._lock:
            self._conn.executemany(
                """
                INSERT INTO chapters (data_id, manga_id, chapter_num, volume, date)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(data_id) DO UPDATE SET
                    chapter_num = excluded.chapter_num,
                    volume      = excluded.volume,
                    date        = excluded.date
                """,
                sorted_chapters
            )
            self._conn.commit()

    @staticmethod
    def _to_model(row: sqlite3.Row) -> ChapterRow:
        return ChapterRow(**dict(row))