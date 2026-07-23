from dataclasses import dataclass
from typing import Optional


@dataclass
class MangaRow:
    id:            int
    data_id:       int
    translit_name: str
    name:          str
    rating:        str
    info:          str
    image:         str
    views:         int
    created_at:    Optional[str]
    updated_at:    Optional[str]
    manga_link_id: int | None = None


@dataclass
class ChapterRow:
    id:           Optional[int]
    data_id:      int
    manga_id:     int
    chapter_num:  int
    volume:       int
    date:         str
    created_at:   Optional[str]
    updated_at:   Optional[str]