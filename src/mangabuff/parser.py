import re
import logging
from bs4 import BeautifulSoup, Tag
from typing import Any, Optional


def parse_int(value: Any) -> Optional[int]:
    """Безпечно перетворює числовий рядок на int, прибираючи пробіли і роздільники."""
    if value is None:
        return None
    if isinstance(value, int):
        return value

    text = str(value).strip()
    if not text:
        return None

    # Прибираємо пробіли та неразривні пробіли як тисячні роздільники.
    normalized = text.replace('\u00A0', '').replace(' ', '')

    # Дозволяємо тільки цифри та негативний знак.
    normalized = re.sub(r'[^0-9\-]', '', normalized)
    if not normalized:
        return None

    try:
        return int(normalized)
    except ValueError:
        return None


def parse_csrf_token(html: str) -> Optional[str]:
    """Витягує CSRF-токен зі сторінки HTML."""
    soup = BeautifulSoup(html, 'html.parser')
    meta_tag = soup.select_one('meta[name="csrf-token"]')
    if isinstance(meta_tag, Tag):
        content = meta_tag.get('content')
        if isinstance(content, str):
            return content
    logging.warning("⚠️ Мета-тег 'csrf-token' не знайдено або має некоректний формат.")
    return None

def parse_user_name(soup: BeautifulSoup) -> str:
    """Витягує ім'я користувача зі сторінки HTML."""
    user_div = soup.select_one(".menu__name")
    if isinstance(user_div, Tag):
        return user_div.get_text(strip=True)
    return ""

def parse_user_id(soup: BeautifulSoup) -> str:
    """Витягує user_id зі сторінки HTML."""
    bookmark_link = soup.select_one('a.header-bookmark')
    if isinstance(bookmark_link, Tag):
        href = bookmark_link.get('href')
        if isinstance(href, str):
            match = re.search(r'/users/(\d+)', href)
            if match:
                return match.group(1)
    logging.warning("⚠️ ID користувача не знайдено.")
    return ""

def parse_is_banned(soup: BeautifulSoup) -> bool:
    """Визначає, чи користувач забанений, за наявністю відповідного банера."""
    is_banned = bool(soup.find('img', src='/img/frames/x150/216.png'))
    return is_banned

def parse_hits_count(soup: BeautifulSoup) -> Optional[int]:
    element = soup.find('span', class_='main-mine__game-hits-left')

    if not element:
        return None
    
    return parse_int(element.text)

def parse_mine_count(soup: BeautifulSoup) -> Optional[int]:
    element = soup.find('div', class_='main-mine__game-panel')

    if not element:
        return None
    
    # Використовуємо .get(), щоб не отримати KeyError, якщо атрибута немає
    # Перетворюємо на str, щоб заспокоїти Pylance (тип _AttributeValue -> str)
    number_raw = element.get('data-max-hit')

    if number_raw is None:
        return None

    try:
        # number_raw може бути списком у теорії BS4, тому явно робимо рядок
        return parse_int(number_raw)
    except (ValueError, TypeError):
        # Якщо в атрибуті не число (наприклад, порожньо або текст)
        return None
    
def parse_ore(soup: BeautifulSoup) -> Optional[int]:
    element = soup.find('span', class_='mine-shop__ore-count')
    
    if not element:
        return None
    
    return parse_int(element.text)

def parse_upgrade_card(soup: BeautifulSoup) -> tuple[Optional[int], Optional[int], bool]:
    """
    Парсить картку 'Улучшение кирки' (upgrade).
    Повертає ціну, поточний рівень та прапорець досягнення максимуму.
    """
    card = soup.find('div', class_='mine-shop__card--upgrade')
    if not card:
        return None, None, False

    card_text = card.get_text().lower()
    is_max = "максимум" in card_text

    # Шукаємо поточний рівень
    level = None
    level_element = card.find('div', class_='mine-shop__card-text')
    if level_element:
        # Значення рівня лежить всередині тегу <b>
        b_element = level_element.find('b')
        if b_element:
            level = parse_int(b_element.text)

    # Шукаємо ціну
    cost = None
    price_element = card.find('div', class_='mine-shop__price')
    if price_element:
        cost = parse_int(price_element.text)

    return cost, level, is_max


def parse_power_card(soup: BeautifulSoup) -> tuple[Optional[int], bool]:
    """
    Парсить картку 'Сильный удар' (power).
    Повертає ціну та прапорець купівлі.
    """
    card = soup.find('div', class_='mine-shop__card--power')
    if not card:
        return None, False

    card_text = card.get_text().lower()
    is_bought = "куплено" in card_text

    cost = None
    price_element = card.find('div', class_='mine-shop__price')
    if price_element:
        cost = parse_int(price_element.text)

    return cost, is_bought


def parse_exchange_info(soup: BeautifulSoup) -> Optional[int]:
    """
    Парсить блок обміну руди на алмази.
    Повертає кількість руди, необхідну для отримання 1 алмаза.
    """
    card_text_element = soup.find("div", class_="mine-shop__card-text")
    if not card_text_element:
        return None

    text = card_text_element.get_text().strip()  # Отримуємо "100 руды = 1 алмаз"
    
    # Регулярний вираз шукає групи цифр перед і після знака "="
    match = re.search(r"([\d\s]+)[^=]+=\s*([\d\s]+)", text)
    if not match:
        return None

    try:
        # Видаляємо можливі пробіли з чисел та перетворюємо в int
        ore_amount = int(match.group(1).replace(" ", ""))
        diamonds_amount = int(match.group(2).replace(" ", ""))
        
        if diamonds_amount == 0:
            print("Помилка: кількість алмазів у співвідношенні дорівнює нулю.")
            return None
            
        # Обчислюємо ціну за 1 алмаз за допомогою цілочисельного ділення
        price_per_diamond = ore_amount // diamonds_amount
        return price_per_diamond

    except ValueError:
        return None

def parse_mining_page(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, 'html.parser')
    
    mine_count = parse_hits_count(soup)
    ore = parse_ore(soup)
    max_hits = parse_mine_count(soup)
    
    # Тепер отримуємо також і рівень кирки (upgrade_level)
    upgrade_cost, upgrade_level, upgrade_max = parse_upgrade_card(soup)
    power_cost, power_bought = parse_power_card(soup)
    
    exchange_diamond_cost = parse_exchange_info(soup)
    
    data: dict[str, Any] = {
        "hits_left": mine_count,
        "ore": ore,
        "max_hits": max_hits,
        
        # Дані покращення кирки
        "upgrade_cost": upgrade_cost,
        "upgrade_level": upgrade_level,
        "upgrade_max": upgrade_max,
        
        # Дані сильного удару
        "power_cost": power_cost,
        "power_bought": power_bought,
        
        # Дані обміну
        "exchange_diamond_cost": exchange_diamond_cost,
    }
    
    return data

def parse_main_page(html: str, only_token: bool = False) -> dict[str, Any]:
    """
    Витягує CSRF-токен, ім'я користувача та user_id зі сторінки HTML.
    """
    soup = BeautifulSoup(html, 'html.parser')

    # 1. CSRF Token
    csrf_token = parse_csrf_token(html)
    
    if only_token:
        return {"csrf_token": csrf_token}
    
    # 2. User Name
    user_name = parse_user_name(soup)
        
    # 3. User ID
    user_id = parse_user_id(soup)
    
    # 4. Is Banned
    is_banned = parse_is_banned(soup)

    if not user_id:
        logging.warning("⚠️ ID користувача не знайдено.")
        
    data: dict[str, Any] = {
        "csrf_token": csrf_token,
        "user_name": user_name,
        "user_id": user_id,
        "is_banned": is_banned
    }

    return data