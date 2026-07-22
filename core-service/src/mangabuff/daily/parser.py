from bs4 import BeautifulSoup

def get_claimable_day(html: str, item_selector: str, claim_text: str, day_attr: str) -> str | None:
    """
    Шукає день для отримання бонусу, використовуючи селектори з конфігу.
    """
    soup = BeautifulSoup(html, 'html.parser')
    items = soup.select(item_selector)
    
    for item in items:
        # Перевіряємо текст кнопки
        if claim_text in item.get_text(strip=True):
            return item.get(day_attr)
            
    return None