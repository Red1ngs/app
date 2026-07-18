from __future__ import annotations

import asyncio

from queue_service.worker import run


if __name__ == "__main__":
    asyncio.run(run())
