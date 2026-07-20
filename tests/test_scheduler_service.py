import asyncio
import unittest
from types import SimpleNamespace

from src.core.services.scheduler_service import SchedulerService


class _DummyStatus:
    def __init__(self, name: str):
        self.name = name


class _DummyBot:
    def __init__(self):
        self.inventory = SimpleNamespace(
            personal=SimpleNamespace(user_name="Alice", user_id="42")
        )
        self.is_connected = True
        self._auth = SimpleNamespace(email="user@example.com")
        self._network = SimpleNamespace(proxy="http://proxy:8080")


class _DummyContainer:
    def __init__(self):
        self.bot = _DummyBot()
        self.monitors = SimpleNamespace(active_ids=lambda: ["reader"])


class _DummyScheduler:
    def __init__(self, container):
        self._container = container

    def get_container(self, _acc_id):
        return self._container

    def status(self, _acc_id):
        return _DummyStatus("connected")

    def profession_names(self, _acc_id):
        return ["daily"]


class SchedulerServiceTests(unittest.TestCase):
    def test_build_info_uses_account_auth_and_network(self):
        service = SchedulerService(repo=object(), app_config=SimpleNamespace())
        container = _DummyContainer()
        scheduler = _DummyScheduler(container)

        info = asyncio.run(service._build_info("acc1", scheduler))

        self.assertIsNotNone(info)
        self.assertEqual(info.email, "user@example.com")
        self.assertEqual(info.proxy, "http://proxy:8080")
        self.assertEqual(info.professions, ["daily"])


if __name__ == "__main__":
    unittest.main()
