"""
core/runtime/profession_spec.py — декларативний опис профессій.

ProfessionSpec замінює два розрізнених хардкоди:
  1. _PROFESSION_MONITORS в scheduler.py   → spec.monitors
  2. неявне «reader тягне manga_loader»    → spec.deps

Реєстрація (в mangabuff/setup.py):

    profession_registry.add(ProfessionSpec(
        id       = "reader",
        cls      = ReaderProfession,
        monitors = ["reading"],
        deps     = ["manga_loader", "catalog_loader"],
    ))
    profession_registry.add(ProfessionSpec(
        id  = "manga_loader",
        cls = MangaLoaderProfession,
    ))
    profession_registry.add(ProfessionSpec(
        id  = "daily",
        cls = DailyProfession,
        monitors = ["daily"],
    ))

Після цього:
    profession_registry.build("reader")
    → [ReaderProfession(), MangaLoaderProfession(), CatalogLoaderProfession()]

    profession_registry.monitors_for("reader")
    → ["reading"]

ProfessionRegistry також зберігає глобальні CoreService-фабрики —
компоненти що автоматично створюються для кожного акаунта незалежно
від вибраних профессій (наприклад AuthService).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Type

if TYPE_CHECKING:
    from src.core.runtime.profession import BaseProfession
    from src.core.runtime.core_service import CoreService


@dataclass
class ProfessionSpec:
    """
    Декларативний опис однієї профессії.

    id       — унікальний рядок, збігається з BaseProfession.profession_id
    cls      — клас профессії (без аргументів конструктора)
    monitors — монітори що підключаються разом з профессією
    deps     — id інших профессій що додаються автоматично при виборі цієї
               (але НЕ зберігаються в БД — лише створюються в пам'яті)
    """
    id:         str
    cls:        Type["BaseProfession"]
    monitors:   list[str] = field(default_factory=list)
    deps:       list[str] = field(default_factory=list)


class ProfessionRegistry:
    """
    Реєстр всіх відомих профессій та глобальних CoreService.

    Замінює profession_factory + _PROFESSION_MONITORS.
    """

    def __init__(self) -> None:
        self._specs:    dict[str, ProfessionSpec]          = {}
        self._services: list[Callable[[], "CoreService"]]  = []

    # ── Реєстрація ────────────────────────────────────────────────────────────

    def add(self, spec: ProfessionSpec) -> None:
        """Реєструє специфікацію профессії."""
        self._specs[spec.id] = spec

    def add_core_service(self, factory: Callable[[], "CoreService"]) -> None:
        """
        Реєструє фабрику CoreService.
        Фабрика викликається один раз на акаунт при його додаванні.
        """
        self._services.append(factory)

    # ── Побудова ──────────────────────────────────────────────────────────────

    def build(self, profession_id: str) -> list["BaseProfession"]:
        """
        Будує список профессій для вказаного id, включаючи всі deps.

        Порядок: спочатку залежності (в порядку оголошення в deps),
        потім сама профессія. Це гарантує що депендансі setup() раніше.

        Якщо profession_id або будь-який dep не знайдено — ValueError.
        Циклічні залежності виявляються і повертають ValueError.
        """
        result: list["BaseProfession"] = []
        seen:   set[str]               = set()
        self._resolve(profession_id, result, seen, stack=[])
        return result

    def _resolve(
        self,
        pid:    str,
        result: list["BaseProfession"],
        seen:   set[str],
        stack:  list[str],
    ) -> None:
        if pid in seen:
            return
        if pid in stack:
            cycle = " → ".join(stack + [pid])
            raise ValueError(f"Циклічна залежність профессій: {cycle}")

        spec = self._specs.get(pid)
        if spec is None:
            raise ValueError(f"Профессію {pid!r} не знайдено в реєстрі")

        stack = stack + [pid]
        for dep_id in spec.deps:
            self._resolve(dep_id, result, seen, stack)

        seen.add(pid)
        result.append(spec.cls())

    def build_core_services(self) -> list["CoreService"]:
        """Створює один екземпляр кожного зареєстрованого CoreService."""
        return [factory() for factory in self._services]

    # ── Запити ────────────────────────────────────────────────────────────────

    def monitors_for(self, profession_id: str) -> list[str]:
        """Повертає список monitor_id для вказаної профессії."""
        spec = self._specs.get(profession_id)
        return list(spec.monitors) if spec else []

    def all_monitors_for(self, profession_ids: list[str]) -> list[str]:
        """Об'єднаний список моніторів для кількох профессій (без дублів)."""
        seen: set[str] = set()
        result: list[str] = []
        for pid in profession_ids:
            for mid in self.monitors_for(pid):
                if mid not in seen:
                    seen.add(mid)
                    result.append(mid)
        return result

    def known_ids(self) -> list[str]:
        """Список всіх зареєстрованих profession_id."""
        return list(self._specs.keys())

    def has(self, profession_id: str) -> bool:
        return profession_id in self._specs

    def __repr__(self) -> str:
        return f"<ProfessionRegistry specs={list(self._specs)}>"


# Глобальний синглтон
profession_registry = ProfessionRegistry()
