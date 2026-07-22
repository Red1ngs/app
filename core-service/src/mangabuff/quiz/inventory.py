"""
quiz/inventory.py — QuizInventory.

Принцип: інвентар є єдиним джерелом правди.
Config (yaml) використовується ТІЛЬКИ для ініціалізації відсутніх полів —
через init_from_config(), яку викликає restore_state().
Після першого запуску всі значення живуть виключно в inventory.data (і БД).

Поля:
    # Конфігураційні (ініціалізуються з yaml, потім змінюються тільки через inventory)
    mode          : "daily" | "fixed"
    answer_limit  : int    — правильних відповідей до зупинки
    answer_delay  : float  — секунди між відповідями

    # Стан сесії
    session_active    : bool
    current_question  : dict | None

    # Лічильники
    correct_count     : int   — правильних у поточній сесії
    answers_today     : int   — правильних за сьогодні (daily)
    answers_done      : int   — правильних всього (fixed, не скидається)
    answers_reset_date: str | None — коли скидали answers_today

    # Завершення
    last_quiz_date : str | None — коли закрита остання сесія (daily)
    fixed_done     : bool       — ліміт досягнуто назавжди (fixed)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from src.core.inventory.model import BaseInventory

if TYPE_CHECKING:
    from src.core.config.app import QuizCfg


@dataclass
class QuizInventory(BaseInventory):

    # ── Ініціалізація з конфігу ───────────────────────────────────────────────

    def init_from_config(self, cfg: "QuizCfg") -> None:
        """
        Заповнює відсутні поля з config.yaml.
        Якщо поле вже є в data — НЕ перезаписує.
        Викликати один раз у restore_state().
        """
        if "mode" not in self.data:
            self.data["mode"] = cfg.mode
        if "answer_limit" not in self.data:
            self.data["answer_limit"] = cfg.answer_limit
        if "answer_delay" not in self.data:
            self.data["answer_delay"] = cfg.answer_delay

    # ── Конфігураційні поля ───────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        """Режим: "daily" або "fixed". KeyError якщо init_from_config() не викликано."""
        return str(self.data["mode"])

    @mode.setter
    def mode(self, value: str) -> None:
        self.data["mode"] = value

    @property
    def answer_limit(self) -> int:
        """Ліміт правильних відповідей до зупинки. KeyError якщо init_from_config() не викликано."""
        return int(self.data["answer_limit"])

    @answer_limit.setter
    def answer_limit(self, value: int) -> None:
        self.data["answer_limit"] = value

    @property
    def answer_delay(self) -> float:
        """Секунди між відповідями. KeyError якщо init_from_config() не викликано."""
        return float(self.data["answer_delay"])

    @answer_delay.setter
    def answer_delay(self, value: float) -> None:
        self.data["answer_delay"] = value

    # ── Стан сесії ────────────────────────────────────────────────────────────

    @property
    def session_active(self) -> bool:
        return bool(self.data.get("session_active", False))

    @session_active.setter
    def session_active(self, value: bool) -> None:
        self.data["session_active"] = value

    @property
    def current_question(self) -> Optional[dict[str, Any]]:
        return self.data.get("current_question")

    @current_question.setter
    def current_question(self, value: Optional[dict[str, Any]]) -> None:
        self.data["current_question"] = value

    # ── Лічильники ────────────────────────────────────────────────────────────

    @property
    def correct_count(self) -> int:
        """Правильних у поточній сесії."""
        return int(self.data.get("correct_count", 0))

    @correct_count.setter
    def correct_count(self, value: int) -> None:
        self.data["correct_count"] = value

    @property
    def answers_today(self) -> int:
        """Правильних за сьогодні (daily-режим)."""
        answers_today = self.data.get("answers_today")
        return int(answers_today) if answers_today is not None else 0

    @answers_today.setter
    def answers_today(self, value: int) -> None:
        self.data["answers_today"] = value

    @property
    def answers_done(self) -> int:
        """Правильних всього (fixed-режим, не скидається)."""
        return int(self.data.get("answers_done", 0))

    @answers_done.setter
    def answers_done(self, value: int) -> None:
        self.data["answers_done"] = value

    @property
    def answers_reset_date(self) -> str:
        """Дата скидання answers_today ("YYYY-MM-DD" UTC)."""
        answers_reset_date = self.data.get("answers_reset_date", "")
        return answers_reset_date

    @answers_reset_date.setter
    def answers_reset_date(self, value: str) -> None:
        self.data["answers_reset_date"] = value

    # ── Завершення ────────────────────────────────────────────────────────────

    @property
    def last_quiz_date(self) -> Optional[str]:
        """Дата закриття останньої сесії (daily-режим)."""
        return self.data.get("last_quiz_date")

    @last_quiz_date.setter
    def last_quiz_date(self, value: str) -> None:
        self.data["last_quiz_date"] = value

    @property
    def fixed_done(self) -> bool:
        """True коли fixed-ліміт досягнуто і тригер помер назавжди."""
        return bool(self.data.get("fixed_done", False))

    @fixed_done.setter
    def fixed_done(self, value: bool) -> None:
        self.data["fixed_done"] = value

    # ── Helpers ───────────────────────────────────────────────────────────────

    def current_counter(self) -> int:
        """Актуальний лічильник залежно від режиму."""
        return self.answers_today if self.mode == "daily" else self.answers_done

    def increment_counter(self) -> int:
        """Інкрементує потрібний лічильник, повертає нове значення."""
        if self.mode == "daily":
            self.answers_today += 1
            return self.answers_today
        else:
            self.answers_done += 1
            return self.answers_done

    def open_session(self, first_question: dict[str, Any]) -> None:
        self.session_active   = True
        self.correct_count    = 0
        self.current_question = first_question

    def close_session(self, date_str: str) -> None:
        """Закриває сесію. Для fixed також виставляє fixed_done=True."""
        self.session_active   = False
        self.current_question = None
        self.last_quiz_date   = date_str
        if self.mode == "fixed":
            self.fixed_done = True

    def reset_daily_counter(self, date_str: str) -> None:
        """Скидає денний лічильник (тільки daily-режим)."""
        self.answers_today      = 0
        self.answers_reset_date = date_str

    def __repr__(self) -> str:
        counter = self.answers_today if self.mode == "daily" else self.answers_done
        return (
            f"<QuizInventory "
            f"mode={self.mode!r} "
            f"active={self.session_active} "
            f"counter={counter}/{self.answer_limit} "
            f"delay={self.answer_delay}s>"
        )