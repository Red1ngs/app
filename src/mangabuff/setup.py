"""
mangabuff/setup.py — реєстрація інвентарів, професій та моніторів.

Викликати ОДИН РАЗ при старті програми.

Профессії описуються декларативно через ProfessionSpec:
  - monitors: які монітори підключаються разом з профессією
  - deps:     які інші профессії додаються автоматично

CoreService реєструються через add_core_service() — вони не є
«вибраними профессіями», а інфраструктурними компонентами
що автоматично прив'язуються до кожного акаунта.
"""
from __future__ import annotations

from src.core.inventory.factory import inventory_factory
from src.core.runtime.profession_spec import ProfessionSpec, profession_registry
from src.core.stats import stats_factory
from src.core.monitoring.monitor import monitor_registry

# Інвентарі
from src.mangabuff.daily.inventory import DailyInventory
from src.mangabuff.mining.inventory import MiningInventory
from src.mangabuff.quiz.inventory import QuizInventory
from src.mangabuff.manga_load.inventory import CatalogLoaderInventory, LoaderInventory
from src.mangabuff.reader.inventory import ReaderInventory
from src.mangabuff.alliance.inventory import AllianceInventory
from src.mangabuff.personal.inventory import PersonalInventory

# Професії
from src.mangabuff.manga_load.manga_loader import MangaLoaderProfession
from src.mangabuff.manga_load.catalog_loader import CatalogLoaderProfession
from src.mangabuff.reader.reader import ReaderProfession
from src.mangabuff.daily.daily_monitor import DailyMonitor
from src.mangabuff.daily.build import DailyProfession
from src.mangabuff.quiz.quiz import QuizProfession
from src.mangabuff.mining.mining import MiningProfession

# CoreService
from src.mangabuff.personal.auth_service import AuthService

# Монітори
from src.mangabuff.reader.reading_monitor import ReadingMonitor
from src.mangabuff.quiz.quiz_monitor import QuizMonitor
from src.mangabuff.mining.mining_monitor import MiningMonitor

# Статистика
from src.mangabuff.daily.stats import DailyRewardStats


def register_inventories() -> None:
    inventory_factory.register("personal",       "personal",       PersonalInventory)
    inventory_factory.register("alliance",        "alliance",        AllianceInventory)
    inventory_factory.register("reader",          "reader",          ReaderInventory)
    inventory_factory.register("loader",          "loader",          LoaderInventory)
    inventory_factory.register("daily",           "daily",           DailyInventory)
    inventory_factory.register("quiz",            "quiz",            QuizInventory)
    inventory_factory.register("mining",          "mining",          MiningInventory)
    inventory_factory.register("catalog_loader",  "catalog_loader",  CatalogLoaderInventory)


def register_professions() -> None:
    """
    Декларативна реєстрація профессій.

    deps  — ці профессії додаються автоматично в пам'яті при виборі батьківської,
            але НЕ зберігаються в БД як окремо вибрані (вони сервісні).
    monitors — монітори що підключаються при активації профессії.
    """
    profession_registry.add(ProfessionSpec(
        id       = "manga_loader",
        cls      = MangaLoaderProfession,
    ))
    profession_registry.add(ProfessionSpec(
        id       = "catalog_loader",
        cls      = CatalogLoaderProfession,
    ))
    profession_registry.add(ProfessionSpec(
        id       = "reader",
        cls      = ReaderProfession,
        monitors = ["reading"],
        deps     = ["manga_loader", "catalog_loader"],
    ))
    profession_registry.add(ProfessionSpec(
        id       = "daily",
        cls      = DailyProfession,
        monitors = ["daily"],
    ))
    profession_registry.add(ProfessionSpec(
        id       = "quiz",
        cls      = QuizProfession,
        monitors = ["quiz"],
    ))
    profession_registry.add(ProfessionSpec(
        id       = "mining",
        cls      = MiningProfession,
        monitors = ["mining"],
    ))


def register_core_services() -> None:
    """
    Реєструє інфраструктурні сервіси що автоматично прив'язуються до кожного акаунта.
    AuthService — завжди активний, не потребує вибору адміном.
    """
    profession_registry.add_core_service(AuthService)


def register_monitors() -> None:
    monitor_registry.register("reading", ReadingMonitor)
    monitor_registry.register("quiz",    QuizMonitor)
    monitor_registry.register("daily",   DailyMonitor)
    monitor_registry.register("mining",  MiningMonitor)


def register_recorders() -> None:
    stats_factory.register("daily_rewards", "daily_rewards", DailyRewardStats)


def bootstrap() -> None:
    """
    Єдина точка ініціалізації всіх підсистем mangabuff.
    Викликається ОДИН РАЗ при старті програми, до створення будь-яких акаунтів.

    Порядок важливий:
        1. inventories   — inventory_factory має бути готовий до Account.__init__
        2. professions   — ProfessionSpec реєструються до першого build()
        3. core_services — AuthService реєструється до першого add_account()
        4. monitors      — monitor_registry до першого attach_all()
        5. recorders     — stats_factory до першого build()
    """
    register_inventories()
    register_professions()
    register_core_services()
    register_monitors()
    register_recorders()
