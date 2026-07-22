"""
farmer/manga_load/catalog_loader.py — CatalogLoaderProfession.

Архітектура:
    CatalogLoaderProfession
        • Слухає «reader.chapters_exhausted» — перший хто встиг захоплює лок,
          решта пропускають (чекають «loader.chapters_ready»).
        • Парсить ОДНУ сторінку каталогу (per-account catalog_page,
          CATALOG_PAGE_SIZE манг за раз) і зберігає нові манги в БД.
        • Сам же довантажує глави щойно знайдених манг — на ТОМУ Ж
          акаунті, без передачі задачі іншим акаунтам через scheduler.
          Кожен HTTP-запит на глави вже йде з priority="BACKGROUND"
          (bot_session.py) — тож увесь цикл «каталог → глави» природно
          виконується найнижчим пріоритетом і не заважає інтерактивним
          діям акаунта (читанню, квізу, шахті).

Збереження глав пачками:
    Глави пишуться в БД не по одній манзі і не всі CATALOG_PAGE_SIZE (30)
    одразу, а пачками по CHAPTER_SAVE_BATCH_SIZE (5) манг: обробили 5 —
    одразу upsert_many() — і далі наступні 5. Якщо цикл перерветься
    посередині сторінки (акаунт відключився, помилка мережі) — уже
    оброблені пачки залишаються збереженими в БД, а не губляться разом
    з усією сторінкою.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from src.core.logging.loggers import get_account_logger
from src.core.runtime.profession import BaseProfession, RequestResult
from src.core.runtime.scheduler import EventDrivenScheduler
from src.mangabuff.manga_load.manga_loader import ChapterRow, fetch_chapter_rows
from src.mangabuff.manga_load.parsers import parse_catalog, CATALOG_PAGE_SIZE, CHAPTER_SAVE_BATCH_SIZE

if TYPE_CHECKING:
    from src.core.core_account import Account
    from src.core.runtime.request_router import RequestContext
    from src.mangabuff.manga_load.inventory import CatalogLoaderInventory


class CatalogLoaderProfession(BaseProfession):
    """
    Profession «Каталог-лоадер».

    Відповідальність:
        • Слухає «reader.chapters_exhausted».
        • Перший хто встиг захоплює глобальний лок — решта пропускають.
        • Парсить одну сторінку каталогу (per-account catalog_page).
        • Довантажує глави знайдених манг сам, пачками по
          CHAPTER_SAVE_BATCH_SIZE, без розподілу між акаунтами.
    """

    def __init__(self) -> None:
        self._account_id: str                               = ""
        self._scheduler:  Optional["EventDrivenScheduler"] = None
        self._inv:        Optional["CatalogLoaderInventory"] = None

    @property
    def profession_id(self) -> str:
        return "catalog_loader"

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def setup(self, scheduler: "EventDrivenScheduler", account_id: str) -> None:
        self._account_id = account_id
        self._scheduler  = scheduler
        scheduler.subscribe("reader.chapters_exhausted", self._on_chapters_exhausted)

    async def restore_state(self, bot: "Account") -> None:
        self._inv = bot.inventory.catalog_loader
        get_account_logger(self._account_id).info(
            f"CatalogLoaderProfession відновлено: "
            f"catalog_page={self._inv.catalog_page}"
        )

    async def teardown(self, scheduler: "EventDrivenScheduler", account_id: str) -> None:
        self._scheduler = None

    def check_guard(self, bot: "Account") -> bool:
        return not bool(bot.inventory.personal.is_banned)

    # ── handle_request ────────────────────────────────────────────────────────

    async def handle_request(
        self,
        intent: str,
        data:   dict[str, Any],
        ctx:    "RequestContext",
    ) -> RequestResult:
        if intent == "get_state":
            return await self._handle_get_state(ctx)
        if intent == "reset_catalog_page":
            return await self._handle_reset_catalog_page(ctx)
        return RequestResult.deny(f"unknown intent: {intent!r}")

    async def _handle_get_state(self, ctx: "RequestContext") -> RequestResult:
        log = get_account_logger(self._account_id)
        try:
            if self._inv is None:
                raise ValueError("Inventory не ініціалізовано")
            return RequestResult.approve(data={"catalog_page": self._inv.catalog_page})
        except Exception as exc:
            log.exception("get_state: помилка")
            return RequestResult.deny(str(exc))

    async def _handle_reset_catalog_page(self, ctx: "RequestContext") -> RequestResult:
        log = get_account_logger(self._account_id)
        try:
            if self._inv is None:
                raise ValueError("Inventory не ініціалізовано")
            self._inv.catalog_page = 1
            log.info("CatalogLoaderProfession: catalog_page скинуто на 1")
            return RequestResult.approve(data={"catalog_page": 1})
        except Exception as exc:
            log.exception("reset_catalog_page: помилка")
            return RequestResult.deny(str(exc))

    # ── Internal Logic ────────────────────────────────────────────────────────

    async def _parse_catalog_page(self, bot: "Account") -> list[str]:
        """
        Парсить поточну сторінку каталогу для цього акаунта.
        Повертає список транслітерацій НОВИХ манг (яких ще не було в БД).
        """
        log = get_account_logger(self._account_id)
        if self._inv is None:
            raise ValueError("Inventory не ініціалізовано")

        page = self._inv.catalog_page
        cfg = bot.app_config.reader

        # 1. Завантажуємо HTML сторінки
        result = await bot.safe_session.fetch_manga_catalog(cfg, page=page)
        html = result.data if result.ok else None

        if not html:
            log.warning(f"CatalogLoader: каталог недоступний (сторінка {page})")
            return []

        # 2. Парсимо манги зі сторінки
        mangas = parse_catalog(html)
        if not mangas:
            log.info(f"CatalogLoader: сторінка {page} порожня → скидаємо на 1")
            self._inv.catalog_page = 1
            return []

        translits: list[str] = []

        # 3. Зберігаємо результати та дедуплікуємо
        existing_ids = bot.repo.mangas.get_existing_data_ids(list(mangas.keys()))
        for data_id, manga in mangas.items():
            if data_id in existing_ids:
                continue

            bot.repo.mangas.upsert(
                data_id,
                manga.translit_name,
                manga.name,
                manga.rating or "",
                manga.info or "",
                manga.image or "",
            )
            translits.append(manga.translit_name)

        # 4. Логіка округлення вгору для наступної сторінки
        total_mangas = bot.repo.mangas.count()
        self._inv.catalog_page = (total_mangas // CATALOG_PAGE_SIZE) + 1

        log.info(
            f"CatalogLoader: оброблено сторінку {page} → "
            f"отримано {len(translits)} нових манг. "
            f"В базі всього: {total_mangas}. "
            f"Наступна сторінка для парсингу: {self._inv.catalog_page}"
        )

        return translits

    async def _load_chapters_for_page(
        self,
        bot:       "Account",
        translits: list[str],
    ) -> tuple[int, int]:
        """
        Довантажує глави для нових манг цієї сторінки каталогу — на
        ЦЬОМУ Ж акаунті, без розподілу по інших акаунтах.

        Зберігає в БД пачками по CHAPTER_SAVE_BATCH_SIZE манг: спарсили
        пачку — одразу upsert_many() — і далі, а не всі манги сторінки
        одним записом наприкінці. Якщо акаунт відключиться посеред
        сторінки — уже оброблені пачки лишаються збереженими.

        Повертає (кількість збережених глав, кількість оброблених манг).
        """
        log = get_account_logger(self._account_id)
        cfg = bot.app_config.reader

        total_chapters   = 0
        processed_mangas = 0

        for offset in range(0, len(translits), CHAPTER_SAVE_BATCH_SIZE):
            batch = translits[offset : offset + CHAPTER_SAVE_BATCH_SIZE]
            batch_rows: list[ChapterRow] = []

            for translit_name in batch:
                if not bot.is_connected:
                    log.warning("CatalogLoader: акаунт відключено, довантаження глав перервано")
                    if batch_rows:
                        bot.repo.chapters.upsert_many(batch_rows)
                        total_chapters += len(batch_rows)
                    return total_chapters, processed_mangas

                manga_row = bot.repo.mangas.get_by_translit_name(translit_name)
                if manga_row is None:
                    log.warning(f"CatalogLoader: manga {translit_name!r} не знайдено в БД — пропускаємо")
                    continue

                rows = await fetch_chapter_rows(bot, cfg, manga_row)
                if rows:
                    batch_rows.extend(rows)
                processed_mangas += 1

            if batch_rows:
                bot.repo.chapters.upsert_many(batch_rows)
                total_chapters += len(batch_rows)
                log.info(
                    f"CatalogLoader: пачка з {len(batch)} манг оброблена → "
                    f"{len(batch_rows)} глав збережено (усього на сторінці: {total_chapters})"
                )

        return total_chapters, processed_mangas

    async def _on_chapters_exhausted(self, payload: dict[str, Any]) -> None:
        log = get_account_logger(self._account_id)
        if self._scheduler is None:
            return

        # Перший хто встиг — парсить. Решта пропускають і чекають chapters_ready.
        acquired = await self._scheduler.try_acquire_loader_lock()
        if not acquired:
            log.info("CatalogLoaderProfession: інший catalog_loader вже парсить — пропускаємо")
            return

        log.info("CatalogLoaderProfession: chapters_exhausted → парсимо каталог")

        bot = self._scheduler.get_bot(self._account_id)
        if bot is None:
            log.warning("CatalogLoaderProfession: акаунт не знайдено")
            await self._scheduler.release_loader_lock()
            return

        try:
            translits = await self._parse_catalog_page(bot)

            if not translits:
                log.info("CatalogLoaderProfession: нових манг на сторінці немає (порожньо/недоступно/усе відоме)")
                await self._scheduler.emit_event(
                    "loader.chapters_ready",
                    {"empty": True},
                    source=self._account_id,
                )
                return

            chapters_saved, mangas_processed = await self._load_chapters_for_page(bot, translits)
            log.info(
                f"CatalogLoaderProfession: сторінку оброблено — "
                f"{mangas_processed}/{len(translits)} нових манг, {chapters_saved} глав збережено"
            )
        except Exception:
            log.exception("CatalogLoaderProfession: критична помилка під час обробки")
            await self._scheduler.emit_event(
                "loader.chapters_ready",
                {"error": True},
                source=self._account_id,
            )
        else:
            await self._scheduler.emit_event(
                "loader.chapters_ready",
                {},
                source=self._account_id,
            )
        finally:
            await self._scheduler.release_loader_lock()
