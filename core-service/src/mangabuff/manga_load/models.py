# reader/models.py — типізовані моделі для професії «Читач манги».

from __future__ import annotations

from dataclasses import dataclass, field
from typing import  Any, Literal, Optional

from src.database.repository.manga import MangaRow
from src.utils.time import now_ts


@dataclass
class ItemReceivedEvent:
    account_id:  str
    slot_name:   str
    reward:      dict[str, Any]
    received_at: float = field(default_factory=now_ts)


@dataclass
class Manga:
    data_id:       int
    translit_name: str
    name:          str
    rating:        str
    image:         str
    info:          str = ""
    views:         int = 0


@dataclass
class Chapter:
    data_id:     int
    chapter_num: float
    volume:      int
    date:        Optional[str]
    manga_id:    Optional[int] = None


@dataclass
class ReaderWork:
    mode:             Literal["stale", "catalog"]
    targets:          Optional[list[MangaRow]] = None
    mangas_to_save:   list[Manga]              = field(default_factory=lambda: [])
    chapters_to_save: list[Chapter]            = field(default_factory=lambda: [])