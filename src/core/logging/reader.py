"""
core/logging/reader.py — утиліти для читання лог-файлів.

Корисно для TG-бота, моніторингу, або власного dashboard-у.

Використання:
    from src.core.logging.reader import LogReader

    reader = LogReader()

    # Останні 30 рядків акаунта
    lines = reader.tail_account("acc_01", n=30)

    # Всі помилки за 24 год
    lines = reader.errors(since_hours=24)

    # Стрімінг у реальному часі (блокуючий — запускати в окремому потоці)
    for line in reader.follow("acc_01"):
        send_to_telegram(line)
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Iterator

from src.utils.time import now


class LogReader:
    def __init__(self, log_dir: str | Path = "logs"):
        self.root = Path(log_dir)

    # ── Шляхи ────────────────────────────────────────────────────────────────

    def account_path(self, account_id: str) -> Path:
        return self.root / "accounts" / f"{account_id}.log"

    def tasks_path(self, account_id: str) -> Path:
        return self.root / "tasks" / f"{account_id}_tasks.log"

    def system_path(self) -> Path:
        return self.root / "system.log"

    def errors_path(self) -> Path:
        return self.root / "errors.log"

    def scheduler_path(self) -> Path:
        return self.root / "scheduler.log"

    # ── Tail ─────────────────────────────────────────────────────────────────

    def tail(self, path: Path, n: int = 50) -> list[str]:
        """Повертає останні n рядків файлу."""
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return [line.rstrip("\n") for line in lines[-n:]]

    def tail_account(self, account_id: str, n: int = 50) -> list[str]:
        return self.tail(self.account_path(account_id), n)

    def tail_tasks(self, account_id: str, n: int = 50) -> list[str]:
        return self.tail(self.tasks_path(account_id), n)

    def tail_system(self, n: int = 50) -> list[str]:
        return self.tail(self.system_path(), n)

    def tail_errors(self, n: int = 50) -> list[str]:
        return self.tail(self.errors_path(), n)

    def tail_scheduler(self, n: int = 50) -> list[str]:
        return self.tail(self.scheduler_path(), n)

    # ── Фільтрація за часом ───────────────────────────────────────────────────

    def errors(self, since_hours: float = 24) -> list[str]:
        """Рядки з errors.log за останні N годин."""
        import re
        from datetime import datetime, timedelta

        path = self.errors_path()
        if not path.exists():
            return []

        cutoff  = now() - timedelta(hours=since_hours)
        pattern = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
        result: list[str] = []

        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                m = pattern.match(line)
                if m:
                    try:
                        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                        if ts.replace(tzinfo=cutoff.tzinfo) >= cutoff:
                            result.append(line.rstrip("\n"))
                    except ValueError:
                        pass
        return result

    # ── Стрімінг (tail -f) ────────────────────────────────────────────────────

    def follow(self, account_id: str, poll: float = 0.5) -> Iterator[str]:
        """
        Генератор: стрімить нові рядки з файлу акаунта в реальному часі.
        Блокуючий — запускати в окремому потоці.

            import threading
            t = threading.Thread(target=lambda: [print(l) for l in reader.follow("acc_01")], daemon=True)
            t.start()
        """
        path = self.account_path(account_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)

        with open(path, "r", encoding="utf-8") as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    yield line.rstrip("\n")
                else:
                    time.sleep(poll)

    def follow_errors(self, poll: float = 1.0) -> Iterator[str]:
        """Стрімить нові рядки з errors.log."""
        path = self.errors_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)

        with open(path, "r", encoding="utf-8") as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    yield line.rstrip("\n")
                else:
                    time.sleep(poll)

    # ── Огляд ─────────────────────────────────────────────────────────────────

    def list_accounts(self) -> list[str]:
        """Account IDs для яких є лог-файли."""
        acc_dir = self.root / "accounts"
        if not acc_dir.exists():
            return []
        return [p.stem for p in sorted(acc_dir.glob("*.log"))]

    def sizes(self) -> dict[str, int]:
        """Розміри всіх лог-файлів у байтах."""
        result: dict[str, int] = {}
        for p in self.root.rglob("*.log"):
            try:
                result[str(p.relative_to(self.root))] = p.stat().st_size
            except OSError:
                pass
        return result