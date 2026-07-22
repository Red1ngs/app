from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AccountRow:
    """Snapshot рядка акаунта з БД."""
    id:          str
    email:       str
    # JSON-список імен профессій, впорядкований за пріоритетом (індекс 0 = найвищий)
    professions: list[str] = field(default_factory=list)
    updated_at:  str = ""

    # ── Сумісність ───────────────────────────────────────────────────────────

    @property
    def profession(self) -> Optional[str]:
        """Зворотна сумісність: перша (найпріоритетніша) профессія або None."""
        return self.professions[0] if self.professions else None

    @staticmethod
    def parse_professions(raw: str | None) -> list[str]:
        """
        Перетворює стовпець professions (TEXT / JSON) у list[str].
        Обробляє три формати, що можуть зустрітись у БД:
          - '["reader","daily"]'   — новий JSON-масив
          - 'reader'               — стара схема (одиничний рядок)
          - NULL / ''              — порожньо
        """
        if not raw:
            return []
        raw = raw.strip()
        if raw.startswith("["):
            try:
                result = json.loads(raw)
                return result if isinstance(result, list) else []
            except json.JSONDecodeError:
                return []
        # Стара схема — одиничний рядок
        return [raw] if raw else []
