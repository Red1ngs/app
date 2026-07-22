from __future__ import annotations

import yaml
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Any


@dataclass(frozen=True)
class RewardSlotCfg:
    name:                  str
    daily_limit:           int
    interval_seconds:      float
    reward_keys:           tuple[str, ...] = ()
    max_chapters_per_slot: int             = 0
    """
    Максимальна кількість глав що можна витратити на цей слот.
    Якщо > 0 — після витрати такої кількості глав (навіть якщо слот ще не
    заповнено нагородами) монітор переходить до наступного слота не чекаючи
    заповнення поточного.
    0 = ліміт вимкнено (стара поведінка — перемикатись лише по daily_limit).

    app.yaml:
        reward_slots:
          - name: card
            daily_limit: 5
            interval_seconds: 120
            max_chapters_per_slot: 20   # переключитись після 20 глав
          - name: scroll
            daily_limit: 3
            interval_seconds: 300
            max_chapters_per_slot: 0    # без ліміту по главах (за замовчуванням)
    """

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RewardSlotCfg":
        return cls(
            name=str(d.get("name", "unknown")),
            daily_limit=int(d.get("daily_limit", 0)),
            interval_seconds=float(d.get("interval_seconds", 0.0)),
            reward_keys=tuple(d.get("reward_keys", [])),
            max_chapters_per_slot=int(d.get("max_chapters_per_slot", 0)),
        )

    def matches(self, reward: dict[str, Any]) -> bool:
        if not self.reward_keys:
            return True
        # all() — слот матчиться тільки якщо в reward присутні ВСІ reward_keys.
        # any() давало хибні спрацювання: наприклад candy {"token","type","id"}
        # матчився як card, бо "id" є в обох, а card стоїть першим у списку.
        return all(key in reward for key in self.reward_keys)


@dataclass(frozen=True)
class ParsingConfig:
    url_catalog:       str
    url_chapters:      str
    url_chapters_load: str
    url_candy_claim:   str
    reward_selector:   str
    reward_type_attr:  str
    reward_id_attr:    str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ParsingConfig":
        return cls(
            reward_selector=str(d.get("reward_selector", ".reward-item")),
            reward_type_attr=str(d.get("reward_type_attr", "data-reward-type")),
            reward_id_attr=str(d.get("reward_id_attr", "data-reward-id")),
            url_catalog=str(d.get("url_catalog", "/manga")),
            url_chapters=str(d.get("url_chapters", "/manga/{translit_name}")),
            url_chapters_load=str(d.get("url_chapters_load", "/chapters/load")),
            url_candy_claim=str(d.get("url_candy_claim", "/halloween/takeCandy?r=822")),
        )


@dataclass(frozen=True)
class ReadingModeDef:
    """
    Визначення одного режиму читання.

    name          — унікальна назва режиму (збігається з ключем у reading_modes).
    slots         — список імен reward_slots що визначають інтервал читання.
                    ReadingMonitor бере interval_seconds першого знайденого слота.
                    Якщо список порожній — використовується fallback_interval_s.
    fallback_interval_s — затримка коли жоден слот зі списку не знайдено
                    або slots порожній (default 5400 = 1.5 год).

    Приклад у YAML:
        reading_modes:
          standard:
            slots: ["card"]
            fallback_interval_s: 5400
          event:
            slots: ["card", "scroll"]
            fallback_interval_s: 120
    """
    name:                 str
    slots:                tuple[str, ...]
    fallback_interval_s:  float

    @classmethod
    def from_dict(cls, name: str, d: dict[str, Any]) -> "ReadingModeDef":
        return cls(
            name=name,
            slots=tuple(d.get("slots", [])),
            fallback_interval_s=float(d.get("fallback_interval_s", 5400.0)),
        )

@dataclass(frozen=True)
class ReaderUrls:
    catalog:      str
    manga_page:   str
    api_load:     str
    api_history:  str
    api_candy:    str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ReaderUrls":
        return cls(
            catalog=str(d.get("catalog", "/manga")),
            manga_page=str(d.get("manga_page", "/manga/{translit_name}")),
            api_load=str(d.get("api_load", "/chapters/load")),
            api_history=str(d.get("api_history", "/addHistory")),
            api_candy=str(d.get("api_candy", "/halloween/takeCandy?r=822")),
        )

@dataclass(frozen=True)
class ReaderParsing:
    reward_selector:  str
    reward_type_attr: str
    reward_id_attr:   str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ReaderParsing":
        return cls(
            reward_selector=str(d.get("reward_selector", ".reward-item")),
            reward_type_attr=str(d.get("reward_type_attr", "data-reward-type")),
            reward_id_attr=str(d.get("reward_id_attr", "data-reward-id")),
        )

@dataclass(frozen=True)
class ReaderAppCfg:
    urls:                 ReaderUrls
    parsing:              ReaderParsing
    update_interval_days: int
    reward_slots:         tuple[RewardSlotCfg, ...]
    reading_modes:        dict[str, ReadingModeDef]
    default_mode:         str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ReaderAppCfg":
        # Спершу дістаємо вкладені структури
        urls = ReaderUrls.from_dict(d.get("urls", {}))
        parsing = ReaderParsing.from_dict(d.get("parsing", {}))
        
        reward_slots = tuple(RewardSlotCfg.from_dict(s) for s in d.get("reward_slots", []))
        
        raw_modes = d.get("reading_modes", {})
        reading_modes = {name: ReadingModeDef.from_dict(name, cfg) for name, cfg in raw_modes.items()}
        if not reading_modes:
            reading_modes = {"standard": ReadingModeDef("standard", (), 5400.0)}

        return cls(
            urls=urls,
            parsing=parsing,
            update_interval_days=int(d.get("update_interval_days", 1)),
            reward_slots=reward_slots,
            reading_modes=reading_modes,
            default_mode=str(d.get("default_mode", next(iter(reading_modes)))),
        )

    def get_mode(self, name: str) -> ReadingModeDef:
        """Повертає режим за іменем або default_mode якщо не знайдено."""
        return self.reading_modes.get(name) or self.reading_modes[self.default_mode]

    def interval_for_mode(self, mode_name: str) -> float:
        """
        Повертає interval_seconds для режиму.

        Шукає перший reward_slot чиє ім'я є у mode.slots.
        Якщо нічого не знайдено — повертає mode.fallback_interval_s.
        """
        mode = self.get_mode(mode_name)
        slot_map = {s.name: s for s in self.reward_slots}
        for slot_name in mode.slots:
            slot = slot_map.get(slot_name)
            if slot is not None:
                return slot.interval_seconds
        return mode.fallback_interval_s
    
    def next_available_slot_for_mode(
        self,
        mode_name:      str,
        slot_counts:    dict[str, int],
        chapters_spent: Optional[dict[str, int]] = None,
    ) -> Optional[RewardSlotCfg]:
        """
        Перший слот активного режиму що ще не вичерпано.

        Слот вважається вичерпаним якщо виконується хоча б одна умова:
          1. slot_counts[name] >= daily_limit  (нагород зібрано досить)
          2. max_chapters_per_slot > 0 і
             chapters_spent[name] >= max_chapters_per_slot
             (витрачено забагато глав на цей слот — перемикаємось достроково)

        Повертає None якщо всі слоти вичерпані (або mode.slots порожній).
        """
        mode = self.get_mode(mode_name)
        slot_map = {s.name: s for s in self.reward_slots}
        spent = chapters_spent or {}

        for slot_name in mode.slots:
            slot = slot_map.get(slot_name)
            if slot is None:
                continue

            # Ліміт по нагородах
            if slot_counts.get(slot_name, 0) >= slot.daily_limit:
                continue

            # Ліміт по главах (якщо заданий)
            if slot.max_chapters_per_slot > 0:
                if spent.get(slot_name, 0) >= slot.max_chapters_per_slot:
                    continue

            return slot
        return None

    def find_slot(self, reward: dict[str, Any]) -> Optional[RewardSlotCfg]:
        for slot in self.reward_slots:
            if slot.matches(reward):
                return slot
        return None


@dataclass(frozen=True)
class QuizUrls:
    quiz_page: str
    start:     str
    answer:    str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "QuizUrls":
        return cls(
            quiz_page=str(d.get("quiz_page", "/quiz")),
            start=str(d.get("start", "/quiz/start")),
            answer=str(d.get("answer", "/quiz/answer")),
        )
        
        
@dataclass(frozen=True)
class QuizCfg:
    urls:         QuizUrls
    mode:         str
    answer_limit: int
    answer_delay: float

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "QuizCfg":
        return cls(
            urls=QuizUrls.from_dict(d.get("urls", {})),
            mode=str(d.get("mode", "daily")),
            answer_limit=int(d.get("answer_limit", 5)),
            answer_delay=float(d.get("answer_delay", 8.0)),
        )


@dataclass(frozen=True)
class DailyUrls:
    balance:   str
    ping:      str
    api_calendar: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DailyUrls":
        return cls(
            balance=str(d.get("balance", "/balance")),
            ping=str(d.get("ping", "/visit/ping")),
            api_calendar=str(d.get("api_calendar", d.get("api_claim", "/balance/claim/{}"))),
        )

@dataclass(frozen=True)
class DailyCfg:
    urls:          DailyUrls
    item_selector: str
    claim_text:    str
    day_attr:      str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DailyCfg":
        return cls(
            urls=DailyUrls.from_dict(d.get("urls", {})),
            item_selector=str(d.get("item_selector", ".daily-rewards .daily-rewards-item")),
            claim_text=str(d.get("claim_text", "Забрать")),
            day_attr=str(d.get("day_attr", "data-day")),
        )
               

@dataclass(frozen=True)
class MiningUrls:
    mining_page:    str
    hit:            str
    upgrade:        str
    buy_strong_hit: str
    exchange:       str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MiningUrls":
        return cls(
            mining_page=str(d.get("mining_page", "/mine")),
            hit=str(d.get("hit", "/mine/hit")),
            upgrade=str(d.get("upgrade", "/mine/upgrade")),
            buy_strong_hit=str(d.get("buy_strong_hit", "/mine/buy-strong-hit")),
            exchange=str(d.get("exchange", "/mine/exchange")),
        )
        
        
@dataclass(frozen=True)
class MiningCfg:
    delay: float
    urls:  MiningUrls

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MiningCfg":
        return cls(
            delay=float(d.get("delay", 2.1)),
            urls=MiningUrls.from_dict(d.get("urls", {})),
        )


@dataclass(frozen=True)
class StartupCfg:
    """
    Параметри плавного запуску акаунтів.

    app.yaml:
        startup:
          connect_delay: 5.0       # пауза між connect() двох акаунтів (сек)
          connect_timeout: 30.0    # таймаут одного connect()
          skip_failed: true        # пропустити збійний акаунт, не зупиняти старт
    """
    connect_delay:   float = 5.0
    connect_timeout: float = 30.0
    skip_failed:     bool  = True

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StartupCfg":
        return cls(
            connect_delay=float(d.get("connect_delay", 5.0)),
            connect_timeout=float(d.get("connect_timeout", 30.0)),
            skip_failed=bool(d.get("skip_failed", True)),
        )
 
@dataclass(frozen=True)
class PersonalUrls:
    user_page:   str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PersonalUrls":
        return cls(
            user_page=str(d.get("user_page", "/users/{user_id}"))
        )
               
@dataclass(frozen=True)
class PersonalCfg:
    urls: PersonalUrls
    
    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PersonalCfg":
        return cls(
            urls=PersonalUrls.from_dict(d.get("urls", {})),
        )
    
@dataclass(frozen=True)
class AppConfig:
    base_url: str
    reader:   ReaderAppCfg
    daily:    DailyCfg
    quiz:     QuizCfg
    mining:   MiningCfg
    startup:  StartupCfg
    personal: PersonalCfg
    
    @classmethod
    def from_dict(cls, raw_data: dict[str, Any]) -> "AppConfig":
        return cls(
            base_url=str(raw_data.get("base_url", "https://mangabuff.ru")),
            reader=ReaderAppCfg.from_dict(raw_data.get("reader", {})),
            daily=DailyCfg.from_dict(raw_data.get("daily", {})),
            quiz=QuizCfg.from_dict(raw_data.get("quiz", {})),
            mining=MiningCfg.from_dict(raw_data.get("mining", {})),
            startup=StartupCfg.from_dict(raw_data.get("startup", {})),
            personal=PersonalCfg.from_dict(raw_data.get("personal", {})),
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AppConfig":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(yaml.safe_load(f) or {})