from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from src.mangabuff.manga_load.models import Chapter, Manga
from src.core.logging.loggers import get_logger

log = get_logger("farmer.parsers")

# Кількість манг на одній сторінці каталогу
CATALOG_PAGE_SIZE: int = 30


# --- ДОПОМІЖНІ ФУНКЦІЇ ДЛЯ БЕЗПЕЧНОЇ РОБОТИ З HTML ---

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


# --- ПАРСИНГ ЗОБРАЖЕНЬ ТА СТИЛІВ ---

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


# --- ПАРСИНГ ТОМІВ ТА ГЛАВ З URL ---

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


# --- ПАРСИНГ ДЕТАЛЕЙ МАНГИ З КАТАЛОГУ ---

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


def _extract_manga_name(item: Tag, data_id_raw: Optional[str]) -> Optional[str]:
    """Безпечно отримує назву манги з відповідного класу."""
    try:
        name_tag = item.select_one(".cards__name")
        if not name_tag:
            log.warning("Відсутній .cards__name (data-id=%s)", data_id_raw)
            return None
        return name_tag.get_text(strip=True)
    except Exception as e:
        log.warning("Помилка при отриманні назви манги (data-id=%s): %s", data_id_raw, e)
        return None


def _extract_manga_rating(item: Tag) -> str:
    """Отримує рейтинг, якщо він є."""
    try:
        rating_tag = item.select_one(".cards__rating")
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

def _extract_manga_info(item: Tag) -> str:
    """
    Парсить інформацію про мангу. 
    Підтримує нову структуру з тегами .tags та стару з .cards__info.
    """
    try:
        # Спочатку шукаємо новий контейнер з тегами
        tags_container = item.select_one(".tags")
        if tags_container:
            return _extract_tags_text(tags_container)

        # Резервний варіант для старої структури картки
        info_tag = item.select_one(".cards__info")
        return info_tag.get_text(strip=True) if info_tag else ""

    except Exception as e:
        log.debug("Не вдалося розпарсити інформацію (info): %s", e)
        return ""


def _extract_manga_image(item: Tag) -> str:
    """Отримує та парсить тег зображення."""
    try:
        img_tag = item.select_one(".cards__img")
        return _extract_image_url(img_tag) if img_tag else ""
    except Exception as e:
        log.debug("Помилка під час обробки .cards__img: %s", e)
        return ""


def _parse_manga_item(item: Tag) -> Optional[Manga]:
    """Збирає об'єкт Manga на основі виділених елементів."""
    data_id_raw = _extract_raw_attribute(item, "data-id")
    url_raw = _extract_raw_attribute(item, "href")

    if not data_id_raw or not url_raw:
        return None

    try:
        data_id = int(data_id_raw)
    except ValueError:
        log.warning("Некоректний формат data_id: %s", data_id_raw)
        return None

    name = _extract_manga_name(item, data_id_raw)
    if not name:
        return None

    translit_name = _extract_translit_name(url_raw, data_id_raw)
    if not translit_name:
        return None

    return Manga(
        data_id=data_id,
        translit_name=translit_name,
        name=name,
        rating=_extract_manga_rating(item),
        info=_extract_manga_info(item),
        image=_extract_manga_image(item),
    )


# --- ПАРСИНГ ДЕТАЛЕЙ ГЛАВ ---

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


# --- ГОЛОВНІ ПУБЛІЧНІ ФУНКЦІЇ ПАРСИНГУ ---

def parse_catalog(html: str) -> dict[int, Manga]:
    """Повертає {data_id: Manga} з HTML каталогу."""
    soup = _create_soup(html)
    if not soup:
        return {}

    result: dict[int, Manga] = {}
    try:
        items = soup.select("a.cards__item")
    except Exception as e:
        log.error("Помилка під час виконання селектора cards__item: %s", e)
        return {}

    for item in items:
        try:
            manga = _parse_manga_item(item)
            if manga:
                result[manga.data_id] = manga
        except Exception as e:
            log.error("Неочікувана помилка під час обробки картки манги: %s", e)

    return result


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


# --- ПАРСИНГ ID ТА ПЕРЕГЛЯДІВ НА СТОРІНЦІ МАНГИ ---

def _find_manga_element(soup: BeautifulSoup) -> Optional[Tag]:
    """Шукає основний контейнер div.manga."""
    try:
        return soup.find('div', class_='manga')
    except Exception as e:
        log.warning("Помилка пошуку div.manga: %s", e)
        return None


def parse_manga_data_id(html: str) -> Optional[int]:
    """Парсить data_id манги зі сторінки манги (не з каталогу)."""
    soup = _create_soup(html)
    if not soup:
        return None

    manga_element = _find_manga_element(soup)
    if not manga_element:
        log.warning("Не вдалося знайти div.manga на сторінці манги")
        return None

    raw_id = _extract_raw_attribute(manga_element, 'data-id')
    if not raw_id:
        log.warning("Не вдалося знайти data_id в елементі div.manga")
        return None

    try:
        return int(raw_id)
    except ValueError:
        log.warning("Помилка конвертації data_id у число: %s", raw_id)
        return None


def _find_views_tag(soup: BeautifulSoup) -> Optional[Tag]:
    """Шукає елемент з кількістю переглядів."""
    try:
        return soup.find("div", class_="manga__views")
    except Exception as e:
        log.debug("Помилка пошуку manga__views: %s", e)
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


def parse_manga_views(html: str) -> int:
    """Парсить кількість переглядів манги з <div class="manga__views">."""
    soup = _create_soup(html)
    if not soup:
        return 0

    views_tag = _find_views_tag(soup)
    if not views_tag:
        log.debug("manga__views не знайдено на сторінці манги")
        return 0

    try:
        raw_text = views_tag.get_text(strip=True)
    except Exception as e:
        log.debug("Помилка при вилученні тексту з views_tag: %s", e)
        return 0

    return _clean_and_parse_views(raw_text)