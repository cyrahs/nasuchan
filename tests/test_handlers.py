from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nasuchan.bot.handlers.commands import handle_config, handle_jobs, handle_notifications, handle_run_callback
from nasuchan.bot.handlers.hanime1 import handle_cancel, handle_hanime1_seed_delete, handle_hanime1_seed_input, handle_hanime1_seed_list
from nasuchan.clients import ControlRequest, Hanime1Seed, JobSummary
from nasuchan.config.settings import PollingSettings
from nasuchan.services.notifications import DeliveryReport


class FakeState:
    def __init__(self) -> None:
        self.value: str | None = None

    async def set_state(self, value: str) -> None:
        self.value = value

    async def get_state(self) -> str | None:
        return self.value

    async def clear(self) -> None:
        self.value = None


class FakeBackendClient:
    def __init__(self) -> None:
        self.jobs = [JobSummary(key='bilibili', name='Bilibili', enabled=True, run_on_start=False)]
        self.requests = [
            ControlRequest(
                request_id=10,
                kind='trigger_job',
                target='bilibili',
                status='running',
                requested_at='2026-03-08T12:00:00Z',
                started_at='2026-03-08T12:00:01Z',
                finished_at=None,
                result='',
                error='',
            ),
            ControlRequest(
                request_id=10,
                kind='trigger_job',
                target='bilibili',
                status='succeeded',
                requested_at='2026-03-08T12:00:00Z',
                started_at='2026-03-08T12:00:01Z',
                finished_at='2026-03-08T12:00:05Z',
                result='ok',
                error='',
            ),
        ]
        self.seeds = [Hanime1Seed(video_id='12488', title='屈辱', label='屈辱 {id-12488}')]
        self.deleted_video_ids: list[str] = []
        self.added_raw_seeds: list[str] = []

    async def list_jobs(self) -> list[JobSummary]:
        return self.jobs

    async def create_trigger_request(self, _target: str) -> ControlRequest:
        return self.requests[0]

    async def get_control_request(self, _request_id: int) -> ControlRequest:
        return self.requests.pop(0)

    async def list_hanime1_seeds(self) -> list[Hanime1Seed]:
        return self.seeds

    async def add_hanime1_seed(self, raw_seed: str) -> Hanime1Seed:
        self.added_raw_seeds.append(raw_seed)
        return self.seeds[0]

    async def delete_hanime1_seed(self, video_id: str) -> Hanime1Seed:
        self.deleted_video_ids.append(video_id)
        return self.seeds[0]


class FakeDeliveryService:
    def __init__(self) -> None:
        self.calls = 0

    async def deliver_once(self) -> DeliveryReport:
        self.calls += 1
        return DeliveryReport(
            fetched=3,
            delivered=2,
            failed=1,
            acked=2,
            delivered_ids=[1, 2],
            failed_ids=[3],
        )


def build_message() -> SimpleNamespace:
    return SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock(), text='seed text')


def build_callback(data: str = 'run:bilibili') -> SimpleNamespace:
    return SimpleNamespace(data=data, message=build_message(), answer=AsyncMock())


@pytest.mark.asyncio
async def test_jobs_handler_renders_job_list() -> None:
    message = build_message()
    backend_client = FakeBackendClient()

    await handle_jobs(message, backend_client, logger=SimpleNamespace(exception=lambda *args, **kwargs: None))

    message.answer.assert_awaited_once()
    assert 'Bilibili' in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_config_handler_renders_config_menu() -> None:
    message = build_message()

    await handle_config(message)

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args[0] == 'Choose a config area:'
    reply_markup = message.answer.await_args.kwargs['reply_markup']
    assert reply_markup.inline_keyboard[0][0].text == 'hanime1'


@pytest.mark.asyncio
async def test_run_callback_creates_request_and_polls_until_terminal_state() -> None:
    callback = build_callback()
    backend_client = FakeBackendClient()
    polling = PollingSettings(control_poll_interval_seconds=0.01, control_poll_timeout_seconds=1)

    await handle_run_callback(
        callback,
        backend_client,
        polling,
        logger=SimpleNamespace(exception=lambda *args, **kwargs: None),
    )

    callback.answer.assert_awaited_once()
    assert callback.message.edit_text.await_count >= 2
    assert 'Status: succeeded' in callback.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_hanime1_seed_list_renders_seed_data() -> None:
    message = build_message()
    backend_client = FakeBackendClient()

    await handle_hanime1_seed_list(message, backend_client, logger=SimpleNamespace(exception=lambda *args, **kwargs: None))

    message.answer.assert_awaited_once()
    assert '12488' in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_hanime1_seed_add_uses_raw_input_and_clears_state() -> None:
    message = build_message()
    message.text = '屈辱 {id-12488}'
    state = FakeState()
    state.value = 'waiting'
    backend_client = FakeBackendClient()

    await handle_hanime1_seed_input(
        message,
        state,
        backend_client,
        logger=SimpleNamespace(exception=lambda *args, **kwargs: None),
    )

    assert backend_client.added_raw_seeds == ['屈辱 {id-12488}']
    assert state.value is None
    assert 'Added Hanime1 seed' in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_cancel_clears_any_active_state() -> None:
    message = build_message()
    state = FakeState()
    state.value = 'some-active-state'

    await handle_cancel(message, state)

    assert state.value is None
    assert message.answer.await_args.args[0] == 'Active bot state cleared.'


@pytest.mark.asyncio
async def test_hanime1_seed_delete_calls_backend_with_video_id() -> None:
    callback = build_callback('seed:rm:12488')
    backend_client = FakeBackendClient()

    await handle_hanime1_seed_delete(
        callback,
        backend_client,
        logger=SimpleNamespace(exception=lambda *args, **kwargs: None),
        video_id='12488',
    )

    assert backend_client.deleted_video_ids == ['12488']
    assert 'Deleted Hanime1 seed' in callback.message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_notifications_command_triggers_single_delivery_cycle() -> None:
    message = build_message()
    delivery_service = FakeDeliveryService()

    await handle_notifications(
        message,
        delivery_service,
        logger=SimpleNamespace(exception=lambda *args, **kwargs: None),
    )

    assert delivery_service.calls == 1
    assert 'Fetched: 3' in message.answer.await_args.args[0]
