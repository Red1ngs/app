from typing import Any

from src.core.stats import BaseStats


class DailyRewardStats(BaseStats):
    """
    Статистика для завдання "Щоденні бонуси".
    Зберігає інформацію про останні отримані бонуси.
    """
    
    @property
    def calendar_results(self) -> dict[str, Any] | None:
        """Результат останньої спроби отримати календарний бонус."""
        return self.data.get("calendar_results")
    
    @calendar_results.setter
    def calendar_results(self, value: dict[str, Any]) -> None:
        self.data["calendar_results"] = value
    
    @property
    def daily_results(self) -> dict[str, Any] | None:
        """Результат останньої спроби отримати звичайний щоденний бонус."""
        return self.data.get("daily_results")    
    
    @daily_results.setter
    def daily_results(self, value: dict[str, Any]) -> None:
        self.data["daily_results"] = value