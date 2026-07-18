"""
accounts/profession_menu.py
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProfessionMenuItem:
    profession_id:     str
    label:             str
    callback_template: str

    def build_callback(self, acc_id: str) -> str:
        return self.callback_template.format(acc_id=acc_id)


class ProfessionMenuRegistry:
    def __init__(self) -> None:
        self._items: list[ProfessionMenuItem] = []

    def register(
        self,
        profession_id:     str,
        label:             str,
        callback_template: str,
    ) -> None:
        self._items.append(ProfessionMenuItem(
            profession_id=profession_id,
            label=label,
            callback_template=callback_template,
        ))

    def items_for(self, active_professions: list[str]) -> list[ProfessionMenuItem]:
        active_set = set(active_professions)
        return [item for item in self._items if item.profession_id in active_set]

    def professions_with_items(self, active_professions: list[str]) -> list[str]:
        """Активні професії (у порядку пріоритету), для яких є хоч один пункт налаштувань."""
        registered = {item.profession_id for item in self._items}
        seen: set[str] = set()
        result: list[str] = []
        for pid in active_professions:
            if pid in registered and pid not in seen:
                seen.add(pid)
                result.append(pid)
        return result

    def items_for_profession(self, profession_id: str) -> list[ProfessionMenuItem]:
        """Усі зареєстровані пункти налаштувань для однієї конкретної професії."""
        return [item for item in self._items if item.profession_id == profession_id]


profession_menu_registry = ProfessionMenuRegistry()