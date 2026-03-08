from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic

from nasuchan.clients import ControlRequest, FavBackendClient

TERMINAL_CONTROL_REQUEST_STATUSES = {'succeeded', 'failed', 'rejected'}


@dataclass(slots=True)
class ControlPollResult:
    request: ControlRequest
    timed_out: bool = False


async def poll_control_request(
    backend_client: FavBackendClient,
    request_id: int,
    *,
    interval_seconds: float,
    timeout_seconds: float,
    on_update: Callable[[ControlRequest], Awaitable[None]] | None = None,
) -> ControlPollResult:
    deadline = monotonic() + timeout_seconds
    while True:
        request = await backend_client.get_control_request(request_id)
        if on_update is not None:
            await on_update(request)
        if request.status in TERMINAL_CONTROL_REQUEST_STATUSES:
            return ControlPollResult(request=request, timed_out=False)
        if monotonic() >= deadline:
            return ControlPollResult(request=request, timed_out=True)
        await asyncio.sleep(interval_seconds)
