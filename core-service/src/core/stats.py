from __future__ import annotations
from typing import TYPE_CHECKING, Any
from dataclasses import dataclass, field

@dataclass
class BaseStats:
    data: dict[str, Any] = field(default_factory=lambda: {})

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def delete(self, key: str) -> None:
        self.data.pop(key, None)

    def update(self, patch: dict[str, Any]) -> None:
        self.data.update(patch)


class DynamicStats:
    """
    Контейнер статистики, що збирається StatsFactory.build().

    Атрибути записуються через звичайний setattr.
    Анотації нижче існують ТІЛЬКИ для IDE/mypy (TYPE_CHECKING),
    в runtime їх немає — звернення йде напряму через __dict__.
    """
    if TYPE_CHECKING:
        # Сюди можна додавати типізацію для автокомпліту в IDE
        # Наприклад:
        # player: BaseStats
        # match: BaseStats
        pass
    
    def __repr__(self) -> str:
        parts = " ".join(
            f"{k}={v!r}" for k, v in self.__dict__.items() if not k.startswith("_")
        )
        return f"<DynamicStats {parts}>"


class StatsFactory:
    def __init__(self) -> None:
        self._registry: dict[str, tuple[str, type[BaseStats]]] = {}

    def register(self, kind: str, attr: str, cls: type[BaseStats]) -> None:
        self._registry[kind] = (attr, cls)

    def build(self) -> DynamicStats:
        stats = DynamicStats()
        for _kind, (attr, cls) in self._registry.items():
            setattr(stats, attr, cls())
        return stats

    @property
    def registry(self) -> dict[str, tuple[str, type[BaseStats]]]:
        return dict(self._registry)

    def get(self, kind: str) -> tuple[str, type[BaseStats]] | None:
        return self._registry.get(kind)

    def kinds(self) -> list[str]:
        return list(self._registry.keys())

    def __repr__(self) -> str:
        return f"<StatsFactory kinds={list(self._registry)}>"


stats_factory = StatsFactory()