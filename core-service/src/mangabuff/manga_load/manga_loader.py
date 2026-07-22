"""
farmer/manga_load/manga_loader.py — MangaLoaderProfession.

Архітектура:
    MangaLoaderProfession
        • force_parse (виклик оператора з бота) — примусово оновлює глави
          конкретних манг за translit_name на акаунті, з якого прийшов
          запит. Якщо манга ще відсутня в БД — спершу отримує data_id зі
          сторінки манги і upsert-ить запис.

Автоматичний потік «новий каталог → нові глави» ЦІЄЮ профессією більше
НЕ обробляється: CatalogLoaderProfession (catalog_loader.py) сам вміє
довантажувати глави щойно знайдених манг — той самий акаунт, що
спарсив сторінку каталогу, одразу довантажує їх, без передачі задачі
іншим акаунтам. Спільна логіка «завантажити + розпарсити глави однієї
манги» винесена в fetch_chapter_rows() нижче — її використовують і
force_parse тут, і catalog_loader.py.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from src.core.logging.loggers import get_account_logger
from src.core.runtime.profession import BaseProfession, RequestResult
from src.core.runtime.scheduler import EventDrivenScheduler
from src.mangabuff.manga_load.parsers import parse_chapters, parse_manga_data_id, parse_manga_views

if TYPE_CHECKING:
    from src.core.core_account import Account
    from src.core.runtime.request_router import RequestContext
    from src.database.repository.manga import MangaRow


ChapterRow = tuple[int, int, float, int, Optional[str]]


async def fetch_chapter_rows(bot: "Account", cfg: Any, manga_row: "MangaRow") -> list[ChapterRow]:
    """
    Довантажує сторінку(и) глав ОДНІЄЇ манги і повертає готові рядки для
    ChapterRepository.upsert_many.

    Свідомо НЕ пише глави в БД сама — виклик (force_parse чи
    catalog_loader) вирішує, коли й якою пачкою комітити. Виняток —
    views: якщо на сторінці знайдено перегляди, оновлюються одразу
    (дешевий одиночний UPDATE, батчити його сенсу нема).
    """
    result = await bot.safe_session.fetch_manga_chapters(cfg, manga_row.translit_name, manga_row.data_id)
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
    """

    def __init__(self) -> None:
        self._account_id: str = ""
        self._scheduler: Optional["EventDrivenScheduler"] = None

    @property
    def profession_id(self) -> str:
        return "manga_loader"

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def setup(self, scheduler: "EventDrivenScheduler", account_id: str) -> None:
        self._account_id = account_id
        self._scheduler  = scheduler

    async def restore_state(self, bot: "Account") -> None:
        get_account_logger(self._account_id).info("MangaLoaderProfession відновлено")

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
        return RequestResult.deny(f"unknown intent: {intent!r}")

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

            data_id = parse_manga_data_id(page_html)
            if data_id is None:
                log.warning(f"force_parse: не вдалося визначити data_id для {translit_name!r}")
                return 0

            # Реєструємо мінімальний запис у БД
            bot.repo.mangas.upsert(data_id, translit_name, translit_name)
            manga_row = bot.repo.mangas.get_by_translit_name(translit_name)
            if manga_row is None:
                log.error("force_parse: upsert пройшов успішно, але запис у БД не знайдено")
                return 0

            log.info(f"force_parse: нова манга {translit_name!r} зареєстрована в БД (data_id={data_id})")

        chapters = await fetch_chapter_rows(bot, cfg, manga_row)
        if not chapters:
            log.warning(f"force_parse: глави недоступні для {translit_name!r}")
            return 0

        bot.repo.chapters.upsert_many(chapters)
        log.debug(f"force_parse: {translit_name!r} → {len(chapters)} глав збережено")
        return len(chapters)
