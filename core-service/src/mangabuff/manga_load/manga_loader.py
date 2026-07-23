"""
farmer/manga_load/manga_loader.py — MangaLoaderProfession.

Архітектура:
    MangaLoaderProfession
        • force_parse (виклик оператора з бота) — примусово оновлює глави
          конкретних манг за translit_name на акаунті, з якого прийшов
          запит. Якщо манга ще відсутня в БД — спершу отримує data_id зі
          сторінки манги і upsert-ить запис.
        • Слухає «reader.chapters_exhausted» — перший хто встиг захоплює
          розподілений лок (EventDrivenScheduler.try_acquire_loader_lock,
          тепер на Redis — діє на весь кластер, а не лише один процес),
          решта пропускають (чекають «loader.chapters_ready»).
        • Парсить ОДНУ сторінку каталогу (per-account catalog_page,
          CATALOG_PAGE_SIZE манг за раз) і зберігає нові манги в БД.
        • Сам же довантажує глави щойно знайдених манг — на ТОМУ Ж
          акаунті, без передачі задачі іншим акаунтам через scheduler.
          Кожен HTTP-запит на глави вже йде з priority="BACKGROUND"
          (bot_session.py) — тож увесь цикл «каталог → глави» природно
          виконується найнижчим пріоритетом і не заважає інтерактивним
          діям акаунта (читанню, квізу, шахті).

Раніше цей автоматичний потік «новий каталог → нові глави» був окремою
профессією CatalogLoaderProfession (catalog_loader.py) — файл видалено,
логіку злито сюди: окрема профессія лише заради однієї підписки на подію
не виправдовувала свого існування.

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
from src.mangabuff.manga_load.parsers import (
    CATALOG_PAGE_SIZE,
    CHAPTER_SAVE_BATCH_SIZE,
    parse_catalog,
    parse_chapters,
    parse_manga_page,
    parse_manga_views,
)

if TYPE_CHECKING:
    from src.core.core_account import Account
    from src.core.runtime.request_router import RequestContext
    from src.database.repository.manga import MangaRow
    from src.mangabuff.manga_load.inventory import LoaderInventory


ChapterRow = tuple[int, int, float, int, Optional[str]]


async def fetch_chapter_rows(bot: "Account", cfg: Any, manga_row: "MangaRow") -> list[ChapterRow]:
    """
    Довантажує сторінку(и) глав ОДНІЄЇ манги і повертає готові рядки для
    ChapterRepository.upsert_many.

    Свідомо НЕ пише глави в БД сама — виклик (force_parse чи
    catalog-парсинг з _on_chapters_exhausted) вирішує, коли й якою
    пачкою комітити. Виняток —
    views: якщо на сторінці знайдено перегляди, оновлюються одразу
    (дешевий одиночний UPDATE, батчити його сенсу нема).
    """
    result = await bot.safe_session.fetch_manga_page(cfg, manga_row.translit_name)
    html = result.data if result.ok else None
    if not html:
        return []

    views = parse_manga_views(html)
    if views > 0:
        bot.repo.mangas.update_views(manga_row.data_id, views)

    return [
        (ch.data_id, manga_row.id, ch.chapter_num, ch.volume, ch.date)
        for ch in parse_chapters(html)
    ]


class MangaLoaderProfession(BaseProfession):
    """
    Profession «Манга-лоадер».

    Відповідальність:
        • force_parse — примусово довантажує/оновлює глави вказаних манг
          на прохання оператора (адмінський Telegram-бот).
        • reader.chapters_exhausted → парсить одну сторінку каталогу
          (per-account catalog_page) і сам довантажує глави знайдених
          манг, пачками по CHAPTER_SAVE_BATCH_SIZE.
    """

    def __init__(self) -> None:
        self._account_id: str = ""
        self._scheduler: Optional["EventDrivenScheduler"] = None
        self._inv: Optional["LoaderInventory"] = None

    @property
    def profession_id(self) -> str:
        return "manga_loader"

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def setup(self, scheduler: "EventDrivenScheduler", account_id: str) -> None:
        self._account_id = account_id
        self._scheduler  = scheduler
        scheduler.subscribe("reader.chapters_exhausted", self._on_chapters_exhausted)

    async def restore_state(self, bot: "Account") -> None:
        self._inv = bot.inventory.loader
        get_account_logger(self._account_id).info(
            f"MangaLoaderProfession відновлено: catalog_page={self._inv.catalog_page}"
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
        if intent == "force_parse":
            return await self._handle_force_parse(data, ctx)
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
            log.info("MangaLoaderProfession: catalog_page скинуто на 1")
            return RequestResult.approve(data={"catalog_page": 1})
        except Exception as exc:
            log.exception("reset_catalog_page: помилка")
            return RequestResult.deny(str(exc))

    async def _handle_force_parse(
        self,
        data: dict[str, Any],
        ctx:  "RequestContext",
    ) -> RequestResult:
        """Примусово оновлює глави манг за translit_name (без каталогу)."""
        log = get_account_logger(ctx.account_id)
        try:
            if self._scheduler is None:
                raise ValueError("Scheduler не ініціалізовано")

            bot = self._scheduler.get_bot(ctx.account_id)
            if bot is None:
                raise ValueError(f"Бот для акаунта {ctx.account_id} не знайдений")

            translits: list[str] = data.get("translits", [])
            if not translits:
                return RequestResult.deny("translits (список translit_name) обов'язковий")

            total_chapters = 0
            saved_mangas   = 0
            for translit_name in translits:
                chapters = await self._force_load_manga(bot, translit_name)
                total_chapters += chapters
                if chapters > 0:
                    saved_mangas += 1

            log.info(
                f"force_parse завершено: "
                f"{total_chapters} глав збережено для {saved_mangas}/{len(translits)} манг"
            )
            return RequestResult.approve(data={
                "chapters_saved": total_chapters,
                "mangas":         saved_mangas,
            })
        except Exception as exc:
            log.exception("force_parse: помилка")
            return RequestResult.deny(str(exc))

    # ── Internal Logic ────────────────────────────────────────────────────────

    async def _force_load_manga(self, bot: "Account", translit_name: str) -> int:
        """Парсить та зберігає глави для translit_name без залежності від каталогу."""
        log = get_account_logger(self._account_id)
        if not bot.is_connected:
            log.warning("manga_loader: акаунт відключено, force_parse скасовано")
            return 0

        cfg = bot.app_config.reader
        manga_row = bot.repo.mangas.get_by_translit_name(translit_name)

        if manga_row is None:
            # Манга невідома — отримуємо сторінку, щоб дізнатися data_id
            result = await bot.safe_session.fetch_manga_page(cfg, translit_name)
            page_html = result.data if result.ok else None
            if not page_html:
                log.warning(f"force_parse: сторінка манги {translit_name!r} недоступна")
                return 0
            manga = parse_manga_page(page_html, translit_name)
            if not manga:
                return 0
            
            bot.repo.mangas.upsert(
                data_id=manga.data_id, 
                translit_name=manga.translit_name, 
                name=manga.name,
                rating=manga.rating,
                info=manga.info if manga.info else "",
                image=manga.image,
                views=manga.views
            )
            manga_row = bot.repo.mangas.get_by_translit_name(translit_name)
            if manga_row is None:
                log.error("force_parse: upsert пройшов успішно, але запис у БД не знайдено")
                return 0

            log.info(f"force_parse: нова манга {translit_name!r} зареєстрована в БД (data_id={manga.data_id})")

        chapters = await fetch_chapter_rows(bot, cfg, manga_row)
        if not chapters:
            log.warning(f"force_parse: глави недоступні для {translit_name!r}")
            return 0

        bot.repo.chapters.upsert_many(chapters)
        log.debug(f"force_parse: {translit_name!r} → {len(chapters)} глав збережено")
        return len(chapters)

    # ── Catalog loading (reader.chapters_exhausted) ─────────────────────────────

    async def _parse_catalog_page(self, bot: "Account") -> list[str]:
        """
        Завантажує й парсить одну сторінку каталогу. Повертає ВСІ
        translit_name на сторінці (без фільтрації і без запису в БД —
        каталог сам по собі більше нічого не зберігає, тільки дає
        список імен).
        """
        log = get_account_logger(self._account_id)
        if self._inv is None:
            raise ValueError("Inventory не ініціалізовано")

        page = self._inv.catalog_page
        cfg = bot.app_config.reader

        result = await bot.safe_session.fetch_manga_catalog(cfg, page=page)
        html = result.data if result.ok else None

        if not html:
            log.warning(f"MangaLoader: каталог недоступний (сторінка {page})")
            return []

        translits = parse_catalog(html)
        if not translits:
            log.info(f"MangaLoader: сторінка {page} порожня → скидаємо на 1")
            self._inv.catalog_page = 1

        return translits
    
    def _advance_catalog_page(self, bot: "Account") -> None:
        """Оновлює номер наступної сторінки на основі поточної кількості манг у БД."""
        if self._inv is None:
            return
        total_mangas = bot.repo.mangas.count()
        self._inv.catalog_page = (total_mangas // CATALOG_PAGE_SIZE) + 1

    async def _register_new_mangas_and_load_chapters(
        self,
        bot:       "Account",
        translits: list[str],
    ) -> tuple[int, int]:
        """
        Для кожної ще незнайомої манги зі сторінки каталогу — ОДНИМ
        запитом забирає сторінку самої манги, реєструє мангу в БД
        (повні дані: назва/рейтинг/info/зображення/views беруться саме
        звідси — каталог давав лише translit_name) і одразу парсить її
        глави з того ж HTML, без другого запиту.

        Зберігає глави пачками по CHAPTER_SAVE_BATCH_SIZE манг: обробили
        пачку — upsert_many() — і далі. Якщо акаунт відключиться посеред
        сторінки — вже оброблені пачки лишаються збереженими.

        Повертає (кількість збережених глав, кількість зареєстрованих нових манг).
        """
        log = get_account_logger(self._account_id)
        cfg = bot.app_config.reader

        total_chapters    = 0
        registered_mangas = 0

        for offset in range(0, len(translits), CHAPTER_SAVE_BATCH_SIZE):
            batch = translits[offset : offset + CHAPTER_SAVE_BATCH_SIZE]
            batch_rows: list[ChapterRow] = []

            for translit_name in batch:
                if not bot.is_connected:
                    log.warning("MangaLoader: акаунт відключено, обробку каталогу перервано")
                    if batch_rows:
                        bot.repo.chapters.upsert_many(batch_rows)
                        total_chapters += len(batch_rows)
                    return total_chapters, registered_mangas

                if bot.repo.mangas.get_by_translit_name(translit_name) is not None:
                    continue  # вже є в БД

                result = await bot.safe_session.fetch_manga_page(cfg, translit_name)
                html = result.data if result.ok else None
                if not html:
                    log.warning(f"MangaLoader: сторінка манги {translit_name!r} недоступна")
                    continue

                manga = parse_manga_page(html, translit_name)
                if not manga:
                    continue

                bot.repo.mangas.upsert(
                    data_id=manga.data_id,
                    translit_name=translit_name,
                    name=manga.name,
                    rating=manga.rating,
                    info=manga.info or "",
                    image=manga.image,
                    views=manga.views,
                )
                registered_mangas += 1

                manga_row = bot.repo.mangas.get_by_translit_name(translit_name)
                if manga_row is None:
                    log.error(f"MangaLoader: upsert пройшов, але {translit_name!r} не знайдено в БД")
                    continue

                batch_rows.extend(
                    (ch.data_id, manga_row.id, ch.chapter_num, ch.volume, ch.date)
                    for ch in parse_chapters(html)
                )

            if batch_rows:
                bot.repo.chapters.upsert_many(batch_rows)
                total_chapters += len(batch_rows)
                log.info(
                    f"MangaLoader: пачка з {len(batch)} манг оброблена → "
                    f"{len(batch_rows)} глав збережено (усього на сторінці: {total_chapters})"
                )

        return total_chapters, registered_mangas

    async def _on_chapters_exhausted(self, payload: dict[str, Any]) -> None:
        log = get_account_logger(self._account_id)
        if self._scheduler is None:
            return

        acquired = await self._scheduler.try_acquire_loader_lock()
        if not acquired:
            log.info("MangaLoaderProfession: інший manga_loader вже парсить — пропускаємо")
            return

        log.info("MangaLoaderProfession: chapters_exhausted → парсимо каталог")

        bot = self._scheduler.get_bot(self._account_id)
        if bot is None:
            log.warning("MangaLoaderProfession: акаунт не знайдено")
            await self._scheduler.release_loader_lock()
            return

        try:
            translits = await self._parse_catalog_page(bot)

            if not translits:
                log.info("MangaLoaderProfession: сторінка каталогу порожня/недоступна")
                await self._scheduler.emit_event(
                    "loader.chapters_ready", {"empty": True}, source=self._account_id,
                )
                return

            chapters_saved, mangas_registered = await self._register_new_mangas_and_load_chapters(
                bot, translits,
            )
            self._advance_catalog_page(bot)

            log.info(
                f"MangaLoaderProfession: сторінку оброблено — "
                f"{mangas_registered}/{len(translits)} нових манг, {chapters_saved} глав збережено"
            )
            await self._scheduler.emit_event("loader.chapters_ready", {}, source=self._account_id)

        except Exception:
            log.exception("MangaLoaderProfession: критична помилка під час обробки")
            try:
                await self._scheduler.emit_event(
                    "loader.chapters_ready", {"error": True}, source=self._account_id,
                )
            except Exception:
                log.exception(
                    "MangaLoaderProfession: не вдалося навіть надіслати loader.chapters_ready(error) — "
                    "інші акаунти можуть зависнути в очікуванні"
                )
        finally:
            await self._scheduler.release_loader_lock()
