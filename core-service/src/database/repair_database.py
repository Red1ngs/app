import sqlite3
import re
from pathlib import Path

# Шлях до вашої БД
DB_PATH = Path("C:\\Users\\Huste\\OneDrive\\bot_state.db")


def extract_slug(raw_name: str) -> str:
    """Очищує URL, шляхи та розширення, залишаючи лише чистий slug."""
    # 1. Видаляємо протокол та домен, якщо вони є (наприклад, https://site.com/manga/slug -> /manga/slug)
    slug = re.sub(r"https?://[^/]+", "", raw_name)
    # 2. Видаляємо префікси шляхів (наприклад, /mangas/slug або /manga/slug -> slug)
    slug = re.sub(r"^/?(mangas|manga)/", "", slug)
    # 3. Видаляємо початкові/кінцеві косі риски та розширення .html
    slug = slug.strip("/").replace(".html", "")
    return slug


def repair_database(db_path: Path):
    if not db_path.exists():
        print(f"Помилка: Файл БД {db_path} не знайдено!")
        return

    print(f"Зробіть бекап перед продовженням!")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    # Тимчасово вимикаємо foreign keys, щоб мати змогу перебудувати зв'язки
    conn.execute("PRAGMA foreign_keys=OFF")
    
    try:
        # 1. Отримуємо всі лінки для аналізу
        links = conn.execute("SELECT id, translit_name FROM manga_links").fetchall()
        
        print(f"Усього знайдено записів у manga_links: {len(links)}")
        
        # Словник для мапінгу: { нормалізований_slug: [список_id_які_йому_відповідають] }
        slug_groups = {}
        for link in links:
            raw_name = link["translit_name"]
            link_id = link["id"]
            
            clean = extract_slug(raw_name)
            if clean not in slug_groups:
                slug_groups[clean] = []
            slug_groups[clean].append((link_id, raw_name))

        # 2. Шукаємо групи, які потребують злиття
        merges_count = 0
        for clean_slug, group in slug_groups.items():
            if len(group) == 1:
                # Якщо назва вже чиста і вона одна — перевіримо, чи треба її просто оновити
                link_id, raw_name = group[0]
                if raw_name != clean_slug:
                    # Потрібно просто перейменувати "брудний" єдиний лінк на чистий slug
                    print(f"  [Очищення] {raw_name!r} -> {clean_slug!r} (ID: {link_id})")
                    conn.execute(
                        "UPDATE manga_links SET translit_name = ? WHERE id = ?",
                        (clean_slug, link_id)
                    )
                continue
            
            # Якщо для одного slug знайдено кілька записів (наприклад, "slug" та "/manga/slug")
            print(f"\n[Злиття дублікатів] Знайдено конфлікт для slug: {clean_slug!r}")
            
            # Визначаємо "хороший" ID (той, який вже є чистим slug, або просто перший з групи)
            good_id = None
            bad_links = []
            
            for link_id, raw_name in group:
                if raw_name == clean_slug:
                    good_id = link_id
                else:
                    bad_links.append((link_id, raw_name))
            
            # Якщо чистого slug не було в базі взагалі, обираємо перший лінк як основний і перейменовуємо його
            if good_id is None:
                good_id, raw_name_to_rename = bad_links.pop(0)
                print(f"  Встановлюємо ID {good_id} як основний та перейменовуємо {raw_name_to_rename!r} -> {clean_slug!r}")
                conn.execute(
                    "UPDATE manga_links SET translit_name = ? WHERE id = ?",
                    (clean_slug, good_id)
                )

            # Переносимо дані з "поганих" ID на "хороший" ID
            for bad_id, bad_name in bad_links:
                print(f"  Злиття ID {bad_id} ({bad_name!r}) -> ID {good_id} ({clean_slug!r})")
                
                # А) Оновлюємо посилання в таблиці mangas
                conn.execute(
                    "UPDATE mangas SET manga_link_id = ? WHERE manga_link_id = ?",
                    (good_id, bad_id)
                )
                
                # Б) Переносимо історію прочитань в account_reads.
                # Оскільки PK в account_reads складається з (account_id, manga_link_id, chapter_num, volume),
                # простий UPDATE може викликати IntegrityError, якщо користувач прочитав одну й ту саму главу
                # під обома лінками. Використовуємо INSERT OR REPLACE для безпечного переносу:
                conn.execute("""
                    INSERT OR REPLACE INTO account_reads (account_id, manga_link_id, chapter_num, volume, read_at)
                    SELECT account_id, ?, chapter_num, volume, read_at
                    FROM account_reads
                    WHERE manga_link_id = ?
                """, (good_id, bad_id))
                
                # Видаляємо дубльовані записи з account_reads, що були прив'язані до старого ID
                conn.execute("DELETE FROM account_reads WHERE manga_link_id = ?", (bad_id,))
                
                # В) Видаляємо непотрібний дублікат з manga_links
                conn.execute("DELETE FROM manga_links WHERE id = ?", (bad_id,))
                
                merges_count += 1

        conn.commit()
        print(f"\n[Успішно] Ремонт завершено. Об'єднано/виправлено проблемних зв'язків: {merges_count}")

    except Exception as e:
        conn.rollback()
        print(f"\n[Помилка] Під час ремонту виникла помилка: {e}")
        raise e
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.close()


if __name__ == "__main__":
    repair_database(DB_PATH)