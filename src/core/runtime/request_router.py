"""src/core/runtime/request_router.py — маршрутизатор scheduler.ask()."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.core.core_account import Account
    from src.core.runtime.profession import BaseProfession, RequestResult

from src.core.logging.loggers import get_logger
log = get_logger("runtime.request_router")


@dataclass
class RequestContext:
    account_id:    str
    profession_id: str
    intent:        str
    caller:        str
    _bot:           "Account"
    timeout:       float = 30.0
    created_at:    float = field(default_factory=time.time)


class RequestRouter:
    def __init__(self) -> None:
        self._registry: dict[tuple[str, str], "BaseProfession"] = {}

    def register(self, account_id: str, profession: "BaseProfession") -> None:
        self._registry[(account_id, profession.profession_id)] = profession

    def unregister(self, account_id: str, profession_id: str) -> None:
        self._registry.pop((account_id, profession_id), None)

    def unregister_account(self, account_id: str) -> None:
        for k in [k for k in self._registry if k[0] == account_id]:
            del self._registry[k]

    async def route(self, ctx: RequestContext, data: dict[str, Any]) -> "RequestResult":
        from src.core.runtime.profession import RequestResult

        profession = self._registry.get((ctx.account_id, ctx.profession_id))
        if profession is None:
            return RequestResult.deny(
                f"profession {ctx.profession_id!r} not found for {ctx.account_id!r}"
            )

        log.info(
            f"[Router] {ctx.account_id}/{ctx.profession_id} "
            f"intent={ctx.intent!r} caller={ctx.caller!r}"
        )
        try:
            result = await asyncio.wait_for(
                profession.handle_request(ctx.intent, data, ctx),
                timeout=ctx.timeout,
            )
        except asyncio.TimeoutError:
            return RequestResult.deny("timeout")
        except Exception as e:
            log.error(f"[Router] {ctx.profession_id!r}: {e}", exc_info=True)
            return RequestResult.deny(f"internal error: {e}")

        # Автозбереження інвентарю лише якщо approved — тільки тоді
        # profession реально змінила стан. Deny = нічого не змінилось.
        # Profession не повинна викликати repo.inventory.save() напряму.
        if result.approved:
            try:
                ctx._bot.repo.inventory.save(ctx.account_id, ctx._bot.inventory) # type: ignore
            except Exception as e:
                log.warning(f"[Router] auto-save inventory failed for {ctx.account_id!r}: {e}")

        return result