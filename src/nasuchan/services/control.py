from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Generic, Protocol, TypeVar

TRequest = TypeVar('TRequest', bound='SupportsRequestStatus')

TERMINAL_JOB_REQUEST_STATUSES = {'succeeded', 'failed', 'rejected'}


class SupportsRequestStatus(Protocol):
    status: str


class SupportsJobRequestPolling(Protocol[TRequest]):
    async def get_job_request(self, request_id: int) -> TRequest: ...


@dataclass(slots=True)
class JobRequestPollResult(Generic[TRequest]):
    request: TRequest
    timed_out: bool = False


async def poll_job_request(
    backend_client: SupportsJobRequestPolling[TRequest],
    request_id: int,
    *,
    interval_seconds: float,
    timeout_seconds: float,
    on_update: Callable[[TRequest], Awaitable[None]] | None = None,
) -> JobRequestPollResult[TRequest]:
    deadline = monotonic() + timeout_seconds
    while True:
        request = await backend_client.get_job_request(request_id)
        if on_update is not None:
            await on_update(request)
        if request.status in TERMINAL_JOB_REQUEST_STATUSES:
            return JobRequestPollResult(request=request, timed_out=False)
        if monotonic() >= deadline:
            return JobRequestPollResult(request=request, timed_out=True)
        await asyncio.sleep(interval_seconds)
