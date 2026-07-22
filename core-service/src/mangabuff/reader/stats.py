"""
reader/stats.py — in-memory статистика нагород.

Збирає спостереження за сесію і виводить аналіз у лог при зупинці.

Що збирається:
  - кожне читання: timestamp, отримана нагорода чи ні, тип нагороди
  - інтервали між нагородами одного типу
  - розподіл по годинах доби

Використання:
    stats = ReaderRewardStats()
    stats.record(slot_name="card", reward={"id": 1, ...})   # нагорода
    stats.record(slot_name="scroll", reward=None)            # без нагороди
    stats.dump()                                             # при зупинці
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Optional

from src.utils.time import format_ts, now_ts

from src.core.logging.loggers import get_logger
log = get_logger("farmer.stats")


@dataclass
class ReadEvent:
    """Одне читання."""
    ts:          float          # unix timestamp
    slot_name:   str            # який слот очікувався
    reward_type: Optional[str]  # який тип нагороди реально випав (None = без нагороди)


class ReaderRewardStats:
    """
    збір статистики нагород за сесію.
    
    """

    def __init__(self) -> None:
        self._events: list[ReadEvent] = []

    # ── Запис ────────────────────────────────────────────────────────────────

    def record(
        self,
        slot_name:   str,
        reward:      Optional[dict[str, Any]],
        ts:          Optional[float] = None,
    ) -> None:
        """
        Викликати після кожного читання.

        slot_name : який слот планувався (card / scroll / …)
        reward    : dict від сайту якщо нагорода є, None якщо не випала
        """
        reward_type: Optional[str] = None
        if reward:
            # Визначаємо тип за ключами відповіді сайту
            keys = set(reward.keys())
            if {"id", "name", "image"} & keys:
                reward_type = "card"
            elif {"scroll", "rank", "is_blessed"} & keys:
                reward_type = "scroll"
            else:
                # Невідомий тип — беремо перший ключ
                reward_type = next(iter(keys), "unknown")

        self._events.append(ReadEvent(
            ts          = ts or now_ts(),
            slot_name   = slot_name,
            reward_type = reward_type,
        ))

    # ── Аналіз ───────────────────────────────────────────────────────────────

    def _intervals_for(self, reward_type: str) -> list[float]:
        """Інтервали між послідовними нагородами одного типу (хвилини)."""
        timestamps = sorted(
            e.ts for e in self._events if e.reward_type == reward_type
        )
        if len(timestamps) < 2:
            return []
        return [(b - a) / 60 for a, b in zip(timestamps, timestamps[1:])]

    def _hourly_distribution(self, reward_type: str) -> dict[int, int]:
        """Кількість нагород по годинах доби (0-23)."""
        dist: dict[int, int] = {}
        for e in self._events:
            if e.reward_type == reward_type:
                hour = int(format_ts(e.ts, "%H"))
                dist[hour] = dist.get(hour, 0) + 1
        return dist

    def _reads_between_rewards(self, reward_type: str) -> list[int]:
        """Скільки читань без нагороди між кожними двома нагородами типу."""
        counts: list[int] = []
        current = 0
        for e in self._events:
            if e.reward_type == reward_type:
                counts.append(current)
                current = 0
            elif e.reward_type is None or e.reward_type != reward_type:
                current += 1
        return counts

    # ── Вивід ─────────────────────────────────────────────────────────────────

    def dump(self) -> None:
        """
        Виводить повний звіт у лог.
        Викликати при зупинці програми.
        """
        if not self._events:
            log.info("[ReaderRewardStats] Немає даних за сесію")
            return

        total_reads   = len(self._events)
        session_start = format_ts(self._events[0].ts,  "%Y-%m-%d %H:%M:%S")
        session_end   = format_ts(self._events[-1].ts, "%Y-%m-%d %H:%M:%S")
        duration_min  = (self._events[-1].ts - self._events[0].ts) / 60

        log.info("=" * 60)
        log.info("[ReaderRewardStats] ЗВІТ ЗА СЕСІЮ")
        log.info(f"  Початок : {session_start}")
        log.info(f"  Кінець  : {session_end}")
        log.info(f"  Тривалість : {duration_min:.0f} хв")
        log.info(f"  Читань всього : {total_reads}")

        # Загальний розподіл нагород
        reward_counts: dict[str, int] = {}
        no_reward = 0
        for e in self._events:
            if e.reward_type is None:
                no_reward += 1
            else:
                reward_counts[e.reward_type] = reward_counts.get(e.reward_type, 0) + 1

        log.info(f"  Без нагороди  : {no_reward} ({no_reward/total_reads*100:.1f}%)")
        for rtype, count in sorted(reward_counts.items()):
            log.info(f"  {rtype:10s} : {count} ({count/total_reads*100:.1f}%)")

        # Детальна статистика по кожному типу
        for reward_type in sorted(reward_counts):
            count = reward_counts[reward_type]
            log.info("-" * 60)
            log.info(f"[ReaderRewardStats] {reward_type.upper()}")

            # Інтервали між нагородами
            intervals = self._intervals_for(reward_type)
            if intervals:
                log.info(f"  Інтервали між нагородами (хв):")
                log.info(f"    min    = {min(intervals):.1f}")
                log.info(f"    max    = {max(intervals):.1f}")
                log.info(f"    median = {statistics.median(intervals):.1f}")
                log.info(f"    mean   = {statistics.mean(intervals):.1f}")
                if len(intervals) >= 2:
                    log.info(f"    stdev  = {statistics.stdev(intervals):.1f}")
                log.info(f"    всього інтервалів = {len(intervals)}")
            else:
                log.info(f"  Недостатньо даних для інтервалів (потрібно >= 2 нагороди)")

            # Читань між нагородами
            between = self._reads_between_rewards(reward_type)
            if between:
                log.info(f"  Читань між нагородами:")
                log.info(f"    min    = {min(between)}")
                log.info(f"    max    = {max(between)}")
                log.info(f"    median = {statistics.median(between):.1f}")

            # Розподіл по годинах
            dist = self._hourly_distribution(reward_type)
            if dist:
                log.info(f"  По годинах доби (год: кількість):")
                for hour in sorted(dist):
                    bar = "█" * dist[hour]
                    log.info(f"    {hour:02d}:00  {bar} {dist[hour]}")

            # Рекомендований інтервал
            if intervals and len(intervals) >= 3:
                median_interval = statistics.median(intervals)
                stdev = statistics.stdev(intervals) if len(intervals) >= 2 else 0
                log.info(f"  ► Рекомендований інтервал читання: "
                         f"{median_interval:.0f} ± {stdev:.0f} хв")
                log.info(f"    (читати у вікні [{median_interval - stdev:.0f}, "
                         f"{median_interval + stdev:.0f}] хв після останньої нагороди)")

        log.info("=" * 60)

    def summary(self) -> str:
        """Короткий рядок для проміжного логування."""
        counts: dict[str, int] = {}
        for e in self._events:
            if e.reward_type:
                counts[e.reward_type] = counts.get(e.reward_type, 0) + 1
        parts = [f"{k}={v}" for k, v in sorted(counts.items())]
        return f"reads={len(self._events)} " + " ".join(parts)