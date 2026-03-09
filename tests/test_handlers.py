from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nasuchan.bot.handlers.commands import (
    handle_aninamer_apply_job_callback,
    handle_aninamer_apply_page,
    handle_aninamer_scan_now_callback,
    handle_config,
    handle_fav_run_callback,
    handle_jobs,
    handle_run,
)
from nasuchan.bot.handlers.hanime1 import handle_cancel, handle_hanime1_seed_delete, handle_hanime1_seed_input, handle_hanime1_seed_list
from nasuchan.clients import (
    AninamerJobRequest,
    AninamerStatusItem,
    AninamerStatusResponse,
    AninamerStatusSummary,
    Hanime1Seed,
    JobRequest,
    JobSummary,
)
from nasuchan.config.settings import PollingSettings
from nasuchan.services import BackendCommandService


class FakeState:
    def __init__(self) -> None:
        self.value: str | None = None

    async def set_state(self, value: str) -> None:
        self.value = value

    async def get_state(self) -> str | None:
        return self.value

    async def clear(self) -> None:
        self.value = None


class FakeFavBackendClient:
    def __init__(self) -> None:
        self.jobs = [JobSummary(key='bilibili', name='Bilibili', enabled=True, run_on_start=False, cron='0 * * * *')]
        self.requests = [
            JobRequest(
                id=10,
                target='bilibili',
                status='running',
                requested_at='2026-03-08T12:00:00Z',
                started_at='2026-03-08T12:00:01Z',
                finished_at=None,
                result='',
                error='',
            ),
            JobRequest(
                id=10,
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

    async def create_job_request(self, _target: str) -> JobRequest:
        return self.requests[0]

    async def get_job_request(self, _request_id: int) -> JobRequest:
        return self.requests.pop(0)

    async def list_hanime1_seeds(self) -> list[Hanime1Seed]:
        return self.seeds

    async def add_hanime1_seed(self, raw_seed: str) -> Hanime1Seed:
        self.added_raw_seeds.append(raw_seed)
        return self.seeds[0]

    async def delete_hanime1_seed(self, video_id: str) -> None:
        self.deleted_video_ids.append(video_id)


class FakeAninamerClient:
    def __init__(self) -> None:
        self.status = AninamerStatusResponse(
            summary=AninamerStatusSummary(
                pending_count=1,
                planning_count=0,
                planned_count=1,
                apply_requested_count=0,
                applying_count=0,
                failed_count=1,
            ),
            pending_items=[
                AninamerStatusItem(
                    job_id=101,
                    series_name='ShowA',
                    watch_root_key='downloads',
                    status='planned',
                    updated_at='2026-03-08T12:00:00Z',
                    tmdb_id=123,
                    video_moves_count=1,
                    subtitle_moves_count=0,
                    error_stage=None,
                    error_message=None,
                )
            ],
            failed_items=[
                AninamerStatusItem(
                    job_id=102,
                    series_name='ShowB',
                    watch_root_key='downloads',
                    status='failed',
                    updated_at='2026-03-08T12:01:00Z',
                    tmdb_id=None,
                    video_moves_count=0,
                    subtitle_moves_count=0,
                    error_stage='plan',
                    error_message='LLMOutputError',
                )
            ],
        )
        self.created_requests: list[tuple[str, int | None]] = []
        self.requests = [
            AninamerJobRequest(
                id=20,
                action='scan_now',
                status='running',
                job_id=None,
                created_at='2026-03-08T12:00:00Z',
                updated_at='2026-03-08T12:00:00Z',
                started_at='2026-03-08T12:00:01Z',
                finished_at=None,
                error_message=None,
            ),
            AninamerJobRequest(
                id=20,
                action='scan_now',
                status='succeeded',
                job_id=None,
                created_at='2026-03-08T12:00:00Z',
                updated_at='2026-03-08T12:00:05Z',
                started_at='2026-03-08T12:00:01Z',
                finished_at='2026-03-08T12:00:05Z',
                error_message=None,
            ),
        ]
        self.apply_requests = [
            AninamerJobRequest(
                id=21,
                action='apply_job',
                status='running',
                job_id=101,
                created_at='2026-03-08T12:10:00Z',
                updated_at='2026-03-08T12:10:00Z',
                started_at='2026-03-08T12:10:01Z',
                finished_at=None,
                error_message=None,
            ),
            AninamerJobRequest(
                id=21,
                action='apply_job',
                status='succeeded',
                job_id=101,
                created_at='2026-03-08T12:10:00Z',
                updated_at='2026-03-08T12:10:05Z',
                started_at='2026-03-08T12:10:01Z',
                finished_at='2026-03-08T12:10:05Z',
                error_message=None,
            ),
        ]

    async def get_status(self) -> AninamerStatusResponse:
        return self.status

    async def create_job_request(self, action: str, *, job_id: int | None = None) -> AninamerJobRequest:
        self.created_requests.append((action, job_id))
        if action == 'apply_job':
            return self.apply_requests[0]
        return self.requests[0]

    async def get_job_request(self, _request_id: int) -> AninamerJobRequest:
        if self.created_requests and self.created_requests[-1][0] == 'apply_job':
            return self.apply_requests.pop(0)
        return self.requests.pop(0)


def build_message() -> SimpleNamespace:
    return SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock(), text='seed text')


def build_callback(data: str) -> SimpleNamespace:
    return SimpleNamespace(data=data, message=build_message(), answer=AsyncMock())


def build_service(*, fav: FakeFavBackendClient | None = None, aninamer: FakeAninamerClient | None = None) -> BackendCommandService:
    return BackendCommandService(fav_client=fav, aninamer_client=aninamer)


@pytest.mark.asyncio
async def test_jobs_handler_renders_fav_and_aninamer_sections() -> None:
    message = build_message()

    await handle_jobs(
        message,
        build_service(fav=FakeFavBackendClient(), aninamer=FakeAninamerClient()),
        logger=SimpleNamespace(exception=lambda *args, **kwargs: None),
    )

    message.answer.assert_awaited_once()
    text = message.answer.await_args.args[0]
    assert 'FAV' in text
    assert 'Bilibili' in text
    assert 'ANINAMER' in text
    assert 'ShowA' in text
    assert 'ShowB' in text


@pytest.mark.asyncio
async def test_config_handler_renders_config_menu_when_hanime1_is_available() -> None:
    message = build_message()

    await handle_config(message, has_hanime1=True)

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args[0] == 'Choose a config area:'
    reply_markup = message.answer.await_args.kwargs['reply_markup']
    assert reply_markup.inline_keyboard[0][0].text == 'hanime1'


@pytest.mark.asyncio
async def test_run_handler_renders_backend_picker_when_multiple_backends_exist() -> None:
    message = build_message()

    await handle_run(
        message,
        build_service(fav=FakeFavBackendClient(), aninamer=FakeAninamerClient()),
        logger=SimpleNamespace(exception=lambda *args, **kwargs: None),
    )

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args[0] == 'Choose a backend:'
    reply_markup = message.answer.await_args.kwargs['reply_markup']
    button_texts = [button.text for row in reply_markup.inline_keyboard for button in row]
    assert button_texts == ['fav', 'aninamer']


@pytest.mark.asyncio
async def test_run_handler_skips_picker_when_only_aninamer_exists() -> None:
    message = build_message()

    await handle_run(
        message,
        build_service(aninamer=FakeAninamerClient()),
        logger=SimpleNamespace(exception=lambda *args, **kwargs: None),
    )

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args[0] == 'Choose an Aninamer action:'


@pytest.mark.asyncio
async def test_fav_run_callback_creates_request_and_polls_until_terminal_state() -> None:
    callback = build_callback('run:fav:bilibili')
    polling = PollingSettings(control_poll_interval_seconds=0.01, control_poll_timeout_seconds=1)

    await handle_fav_run_callback(
        callback,
        build_service(fav=FakeFavBackendClient()),
        polling,
        logger=SimpleNamespace(exception=lambda *args, **kwargs: None),
    )

    callback.answer.assert_awaited_once()
    assert callback.message.edit_text.await_count >= 2
    assert 'Status: succeeded' in callback.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_aninamer_scan_now_callback_creates_request_and_polls_until_terminal_state() -> None:
    callback = build_callback('run:aninamer:scan_now')
    aninamer = FakeAninamerClient()
    polling = PollingSettings(control_poll_interval_seconds=0.01, control_poll_timeout_seconds=1)

    await handle_aninamer_scan_now_callback(
        callback,
        build_service(aninamer=aninamer),
        polling,
        logger=SimpleNamespace(exception=lambda *args, **kwargs: None),
    )

    assert aninamer.created_requests == [('scan_now', None)]
    assert 'Action: scan_now' in callback.message.edit_text.await_args.args[0]
    assert 'Status: succeeded' in callback.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_aninamer_apply_page_lists_only_planned_jobs() -> None:
    callback = build_callback('run:aninamer:apply')

    await handle_aninamer_apply_page(
        callback,
        build_service(aninamer=FakeAninamerClient()),
        logger=SimpleNamespace(exception=lambda *args, **kwargs: None),
        page=0,
    )

    callback.message.edit_text.assert_awaited_once()
    assert 'Choose an Aninamer job to apply' in callback.message.edit_text.await_args.args[0]
    reply_markup = callback.message.edit_text.await_args.kwargs['reply_markup']
    button_texts = [button.text for row in reply_markup.inline_keyboard for button in row]
    assert button_texts == ['#101 ShowA']


@pytest.mark.asyncio
async def test_aninamer_apply_job_callback_creates_request_and_polls_until_terminal_state() -> None:
    callback = build_callback('run:aninamer:apply:job:101')
    aninamer = FakeAninamerClient()
    polling = PollingSettings(control_poll_interval_seconds=0.01, control_poll_timeout_seconds=1)

    await handle_aninamer_apply_job_callback(
        callback,
        build_service(aninamer=aninamer),
        polling,
        logger=SimpleNamespace(exception=lambda *args, **kwargs: None),
        job_id=101,
    )

    assert aninamer.created_requests == [('apply_job', 101)]
    assert 'Action: apply_job' in callback.message.edit_text.await_args.args[0]
    assert 'Job ID: 101' in callback.message.edit_text.await_args.args[0]
    assert 'Status: succeeded' in callback.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_hanime1_seed_list_renders_seed_data() -> None:
    message = build_message()
    backend_client = FakeFavBackendClient()

    await handle_hanime1_seed_list(message, backend_client, logger=SimpleNamespace(exception=lambda *args, **kwargs: None))

    message.answer.assert_awaited_once()
    assert '12488' in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_hanime1_seed_add_uses_raw_input_and_clears_state() -> None:
    message = build_message()
    message.text = '屈辱 {id-12488}'
    state = FakeState()
    state.value = 'waiting'
    backend_client = FakeFavBackendClient()

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
    backend_client = FakeFavBackendClient()

    await handle_hanime1_seed_delete(
        callback,
        backend_client,
        logger=SimpleNamespace(exception=lambda *args, **kwargs: None),
        video_id='12488',
    )

    assert backend_client.deleted_video_ids == ['12488']
    assert callback.message.answer.await_args.args[0] == 'Deleted Hanime1 seed: 12488'
