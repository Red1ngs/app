from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any, Type


if TYPE_CHECKING:
    from src.database.repository.account import AccountRepository
    from src.database.repository.inventory import InventoryRepository
    from src.database.repository.manga import MangaRepository, ChapterRepository


class Repositories:
    """
    Контейнер для ініціалізованих репозиторіїв.
    Створюється через RepositoryFactory.build(conn).
    """
    
    if TYPE_CHECKING:
        # Типізація для IDE
        accounts: AccountRepository
        mangas: MangaRepository
        chapters: ChapterRepository
        inventory: InventoryRepository

    def __repr__(self) -> str:
        parts = " ".join(
            f"{k}={type(v).__name__}" for k, v in self.__dict__.items() if not k.startswith("_")
        )
        return f"<Repositories {parts}>"


class RepositoryFactory:
    def __init__(self) -> None:
        # Реєстр: {назва: (назва_атрибута, клас_репозиторію)}
        self._registry: dict[str, tuple[str, Type[Any]]] = {}

    def register(self, kind: str, attr: str, cls: Type[Any]) -> None:
        """Реєструє клас репозиторію."""
        self._registry[kind] = (attr, cls)

    def build(self, conn: sqlite3.Connection) -> Repositories:
        """
        Створює об'єкт Repositories та ініціалізує всі зареєстровані 
        репозиторії, передаючи їм підключення до БД.
        """
        repos = Repositories()
        for _kind, (attr, cls) in self._registry.items():
            # Кожен репозиторій отримує conn у конструктор
            setattr(repos, attr, cls(conn))
        return repos

    def kinds(self) -> list[str]:
        return list(self._registry.keys())

    def __repr__(self) -> str:
        return f"<RepositoryFactory kinds={list(self._registry)}>"


# Глобальний інстанс (сінглтон)
repository_factory = RepositoryFactory()