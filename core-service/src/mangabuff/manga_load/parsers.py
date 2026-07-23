from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from src.mangabuff.manga_load.models import Chapter, Manga
from src.core.logging.loggers import get_logger

log = get_logger("farmer.parsers")

# Кількість манг на одній сторінці каталогу
CATALOG_PAGE_SIZE: int = 30

# Через скільки оброблених манг зберігати глави в БД пачкою (замість
# усіх 30 одразу чи по одній) — компроміс між кількістю запитів до
# БД і втратою прогресу при перериванні циклу.
CHAPTER_SAVE_BATCH_SIZE: int = 5


# =============================================================================
# ЗАГАЛЬНІ ДОПОМІЖНІ ФУНКЦІЇ ДЛЯ БЕЗПЕЧНОЇ РОБОТИ З HTML
# =============================================================================

def _create_soup(html: str) -> Optional[BeautifulSoup]:
    """Створює об'єкт BeautifulSoup з обробкою винятків."""
    try:
        return BeautifulSoup(html, "html.parser")
    except Exception as e:
        log.error("Не вдалося ініціалізувати BeautifulSoup: %s", e)
        return None


def _extract_raw_attribute(item: Tag, attr: str) -> Optional[str]:
    """Безпечно отримує текстове значення атрибута з тегу."""
    try:
        val = item.get(attr)
        if val is None:
            return None
        return str(val)
    except Exception as e:
        log.debug("Помилка отримання атрибута '%s': %s", attr, e)
        return None


def _clean_and_parse_views(raw_text: str) -> int:
    """Очищає текст переглядів від пробілів та конвертує у ціле число."""
    try:
        # Прибираємо нерозривні та звичайні пробіли
        cleaned = raw_text.replace("\xa0", "").replace(" ", "")
        return int(cleaned)
    except (ValueError, TypeError):
        log.debug("Не вдалося конвертувати manga__views у число: %s", raw_text)
        return 0


# =============================================================================
# ПАРСИНГ ЗОБРАЖЕНЬ ТА СТИЛІВ
# =============================================================================

def _extract_style_attribute(img_tag: Tag) -> str:
    """Отримує значення атрибута style."""
    return _extract_raw_attribute(img_tag, "style") or ""


def _parse_url_from_style(style: str) -> str:
    """Витягує URL-посилання з властивості background-image CSS."""
    if "url(" not in style:
        return ""
    try:
        return style.split("url(")[1].split(")")[0].strip("'\"")
    except IndexError:
        log.debug("Помилка парсингу URL з властивості style: %s", style)
        return ""


def _extract_image_url(img_tag: Tag) -> str:
    """Поєднує кроки вилучення URL зображення."""
    style = _extract_style_attribute(img_tag)
    return _parse_url_from_style(style)


# =============================================================================
# ПАРСИНГ ТОМІВ ТА ГЛАВ З URL
# =============================================================================

def _get_path_parts_from_url(url: str) -> list[str]:
    """Розбирає шлях URL на окремі сегменти."""
    try:
        return urlparse(url).path.strip("/").split("/")
    except Exception as e:
        log.debug("Не вдалося розібрати URL '%s': %s", url, e)
        return []


def _parse_vol_chap_from_url(url: str) -> tuple[Optional[int], Optional[float]]:
    """Парсить номери тому та глави з частин URL-шляху."""
    parts = _get_path_parts_from_url(url)
    if len(parts) < 2:
        log.debug("Недостатньо сегментів в URL '%s' для тому/глави", url)
        return None, None

    # Спроба отримати номер тому
    try:
        volume = int(parts[-2])
    except (ValueError, TypeError):
        log.debug("Некоректний формат тому в URL '%s': %s", url, parts[-2])
        volume = None

    # Спроба отримати номер глави
    try:
        chapter = float(parts[-1])
    except (ValueError, TypeError):
        log.debug("Некоректний формат глави в URL '%s': %s", url, parts[-1])
        chapter = None

    return volume, chapter


# =============================================================================
# ПАРСИНГ ДЕТАЛЕЙ МАНГИ З КАТАЛОГУ (КАРТКИ)
# =============================================================================

def _extract_translit_name(url_raw: str, data_id_raw: str) -> Optional[str]:
    """Визначає транслітеровану назву манги з її URL."""
    try:
        translit_name = url_raw.rstrip("/").split("/")[-1]
        if not translit_name:
            raise ValueError("Отримано порожнє ім'я")
        return translit_name
    except Exception as e:
        log.warning("Помилка визначення translit_name (data-id=%s, href=%s): %s", data_id_raw, url_raw, e)
        return None


def _extract_card_name(card: Tag, data_id_raw: Optional[str]) -> Optional[str]:
    """Безпечно отримує назву манги з відповідного класу."""
    try:
        name_tag = card.select_one(".cards__name")
        if not name_tag:
            log.warning("Відсутній .cards__name (data-id=%s)", data_id_raw)
            return None
        return name_tag.get_text(strip=True)
    except Exception as e:
        log.warning("Помилка при отриманні назви манги (data-id=%s): %s", data_id_raw, e)
        return None


def _extract_card_rating(card: Tag) -> str:
    """Отримує рейтинг, якщо він є."""
    try:
        rating_tag = card.select_one(".cards__rating")
        return rating_tag.get_text(strip=True) if rating_tag else ""
    except Exception as e:
        log.debug("Помилка під час отримання .cards__rating: %s", e)
        return ""


def _extract_tags_text(tags_container: Tag) -> str:
    """Вилучає текст із елементів .tags__item, ігноруючи кнопку 'показати більше'."""
    try:
        tag_elements = tags_container.select(".tags__item")
        if not tag_elements:
            return ""

        cleaned_tags: list[str] = []
        for element in tag_elements:
            # Ігноруємо елементи-кнопки
            if element.name == "button":
                continue

            # Отримуємо класи та явно перевіряємо їхній тип для Pylance
            classes = element.get("class")

            # Якщо класи повернулися списком (стандартна поведінка для HTML)
            if isinstance(classes, list) and "tags__item-more" in classes:
                continue

            # Якщо класи повернулися як один рядок (на випадок XML-режиму)
            if isinstance(classes, str) and "tags__item-more" == classes:
                continue

            text = element.get_text(strip=True)
            if text:
                cleaned_tags.append(text)

        return ", ".join(cleaned_tags)
    except Exception as e:
        log.debug("Помилка обробки списку тегів: %s", e)
        return ""


def _extract_card_info(card: Tag) -> str:
    """
    Парсить інформацію про мангу зі старої структури картки каталогу (.cards__info).

    Увага: .tags та .manga__views належать сторінці конкретної манги
    (div.manga), а не картці каталогу — там їх шукати марно. Повне info
    (жанри/теги) та views заповнюються окремо через parse_manga_info() /
    parse_manga_views() після завантаження сторінки манги.
    """
    try:
        info_tag = card.select_one(".cards__info")
        return info_tag.get_text(strip=True) if info_tag else ""
    except Exception as e:
        log.debug("Не вдалося розпарсити .cards__info: %s", e)
        return ""


def _extract_card_image(card: Tag) -> str:
    """Отримує та парсить тег зображення."""
    try:
        img_tag = card.select_one(".cards__img")
        return _extract_image_url(img_tag) if img_tag else ""
    except Exception as e:
        log.debug("Помилка під час обробки .cards__img: %s", e)
        return ""


def _parse_card_item(card: Tag) -> Optional[Manga]:
    """Збирає об'єкт Manga на основі виділених елементів."""
    data_id_raw = _extract_raw_attribute(card, "data-id")
    url_raw = _extract_raw_attribute(card, "href")

    if not data_id_raw or not url_raw:
        return None

    try:
        data_id = int(data_id_raw)
    except ValueError:
        log.warning("Некоректний формат data_id: %s", data_id_raw)
        return None

    name = _extract_card_name(card, data_id_raw)
    if not name:
        return None

    translit_name = _extract_translit_name(url_raw, data_id_raw)
    if not translit_name:
        return None

    return Manga(
        data_id=data_id,
        translit_name=translit_name,
        name=name,
        rating=_extract_card_rating(card),
        info=_extract_card_info(card),
        image=_extract_card_image(card),
    )


# =============================================================================
# ПАРСИНГ ДЕТАЛЕЙ ГЛАВ
# =============================================================================

def _extract_chapter_href(item: Tag) -> Optional[str]:
    """Безпечно отримує та валідує посилання на главу."""
    href = item.get("href")
    if not href or isinstance(href, list):
        return None
    return str(href)


def _extract_chapter_data_id(item: Tag) -> Optional[int]:
    """Шукає кнопку лайка для отримання унікального id глави."""
    try:
        like_btn = item.select_one("button.favourite-send-btn[data-id]")
        if not like_btn:
            return None

        raw_id = like_btn.get("data-id")
        if not raw_id:
            return None

        return int(str(raw_id))
    except (ValueError, TypeError) as e:
        log.warning("Некоректний чи відсутній data-id глави: %s", e)
        return None
    except Exception as e:
        log.debug("Помилка пошуку кнопки лайка глави: %s", e)
        return None


def _extract_chapter_date_from_tag(item: Tag) -> Optional[str]:
    """Спроба дістати дату публікації з тексту відповідного тегу."""
    try:
        date_tag = item.select_one(".chapters__add-date")
        return date_tag.get_text(strip=True) if date_tag else None
    except Exception as e:
        log.debug("Помилка парсингу тексту .chapters__add-date: %s", e)
        return None


def _extract_chapter_date(item: Tag) -> Optional[str]:
    """Визначає дату глави з атрибута або з текстового тегу."""
    try:
        date_attr = item.get("data-chapter-date")
        if date_attr:
            return str(date_attr)
        return _extract_chapter_date_from_tag(item)
    except Exception as e:
        log.debug("Помилка отримання дати глави: %s", e)
        return None


def _parse_chapter_item(item: Tag) -> Optional[Chapter]:
    """Збирає об'єкт Chapter на основі виділених полів."""
    href = _extract_chapter_href(item)
    if not href:
        return None

    log.debug("chapter href: %s", href)

    chapter_data_id = _extract_chapter_data_id(item)
    if chapter_data_id is None:
        return None

    volume, chapter_num = _parse_vol_chap_from_url(href)
    if volume is None or chapter_num is None:
        log.warning("Не вдалося розпарсити том/главу з URL: %s", href)
        return None

    date_val = _extract_chapter_date(item)

    return Chapter(
        data_id=chapter_data_id,
        volume=volume,
        chapter_num=chapter_num,
        date=date_val,
    )


# =============================================================================
# ПАРСИНГ ДЕТАЛЕЙ ЗІ СТОРІНКИ КОНКРЕТНОЇ МАНГИ (div.manga)
# =============================================================================

def _find_manga_page_container(soup: BeautifulSoup) -> Optional[Tag]:
    """Шукає основний контейнер div.manga."""
    try:
        return soup.find("div", class_="manga")
    except Exception as e:
        log.warning("Помилка пошуку div.manga: %s", e)
        return None


def _extract_manga_page_info(soup: BeautifulSoup) -> str:
    """
    Отримує інформацію про мангу (жанри/теги) з <div class="tags">
    на сторінці манги. На відміну від картки каталогу, тут цей
    контейнер справді присутній.
    """
    try:
        tags_container = soup.select_one(".tags")
        return _extract_tags_text(tags_container) if tags_container else ""
    except Exception as e:
        log.debug("Не вдалося розпарсити .tags на сторінці манги: %s", e)
        return ""
    
def parse_manga_data_id(soup: BeautifulSoup) -> Optional[int]:
    """Парсить data_id манги зі сторінки манги (не з каталогу)."""

    page_container = _find_manga_page_container(soup)
    if not page_container:
        log.warning("Не вдалося знайти div.manga на сторінці манги")
        return None

    raw_id = _extract_raw_attribute(page_container, "data-id")
    if not raw_id:
        log.warning("Не вдалося знайти data_id в елементі div.manga")
        return None

    try:
        return int(raw_id)
    except ValueError:
        log.warning("Помилка конвертації data_id у число: %s", raw_id)
        return None


def parse_manga_views(soup: BeautifulSoup) -> int:
    """Парсить кількість переглядів манги з <div class="manga__views">."""
    try:
        views_tag = soup.find("div", class_="manga__views")
    except Exception as e:
        log.error("Помилка пошуку div.manga__views: %s", e)
        return 0

    raw_text = views_tag.get_text(strip=True) if views_tag else ""
    return _clean_and_parse_views(raw_text)


# =============================================================================
# ГОЛОВНІ ПУБЛІЧНІ ФУНКЦІЇ ПАРСИНГУ
# =============================================================================

def parse_catalog(html: str) -> list[str]:
    """
    Повертає список унікальних translit_name манг з HTML каталогу.

    Раніше тут парсилась уся картка (назва/рейтинг/info/зображення) —
    тепер цього не треба: повні дані манги (включно з views) все одно
    беруться зі сторінки самої манги, бо це єдине місце, звідки можна
    дістати глави. Каталог лишається лише джерелом translit_name —
    списком "які манги взагалі існують".
    """
    soup = _create_soup(html)
    if not soup:
        return []

    try:
        items = soup.select("a.cards__item")
    except Exception as e:
        log.error("Помилка під час виконання селектора cards__item: %s", e)
        return []

    translits: list[str] = []
    seen: set[str] = set()

    for card in items:
        href = _extract_raw_attribute(card, "href")
        if not href:
            continue
        data_id_raw = _extract_raw_attribute(card, "data-id") or ""
        translit_name = _extract_translit_name(href, data_id_raw)
        if translit_name and translit_name not in seen:
            seen.add(translit_name)
            translits.append(translit_name)

    return translits


def parse_manga_page(html: str, translit_name: str) -> Optional[Manga]:
    """Парсить повні дані манги зі сторінки самої манги (div.manga)."""
    soup = _create_soup(html)
    if not soup:
        return None

    data_id = parse_manga_data_id(soup)
    if data_id is None:
        log.warning(f"parse_manga_page: не вдалося визначити data_id для {translit_name!r}")
        return None

    name_tag = soup.find(class_="manga__name")
    name = name_tag.get_text(strip=True) if name_tag else ""
    if not name:
        log.warning(f"parse_manga_page: не вдалося визначити назву манги для {translit_name!r}")

    rating_tag = soup.find("div", class_="manga__rating")
    rating = rating_tag.get_text(strip=True) if rating_tag else ""

    img_tag = soup.select_one(".manga__img img")
    image = _extract_raw_attribute(img_tag, "src") if img_tag else ""

    info = _extract_manga_page_info(soup)
    views = parse_manga_views(soup)

    return Manga(
        data_id=data_id,
        translit_name=translit_name,
        name=name,
        rating=rating,
        image=image or "",
        info=info,
        views=views,
    )
        

def parse_chapters(html: str) -> list[Chapter]:
    """Повертає список об'єктів Chapter з HTML сторінки манги."""
    soup = _create_soup(html)
    if not soup:
        return []

    chapters: list[Chapter] = []
    try:
        items = soup.select("a.chapters__item")
    except Exception as e:
        log.error("Помилка під час виконання селектора chapters__item: %s", e)
        return []

    for item in items:
        try:
            ch = _parse_chapter_item(item)
            if ch:
                chapters.append(ch)
        except Exception as e:
            log.error("Неочікувана помилка під час обробки глави: %s", e)

    return chapters