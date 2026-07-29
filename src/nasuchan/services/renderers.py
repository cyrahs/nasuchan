from __future__ import annotations

from collections.abc import Callable
from math import ceil

from nasuchan.clients import (
    AninamerJobRequest,
    AninamerStatusItem,
    AninamerStatusResponse,
    Hanime1Seed,
    HealthStatus,
    JobRequest,
    JobSummary,
    ReadinessStatus,
)

from .backends import AggregatedJobsSnapshot, AggregatedStatusSnapshot, BackendHealthSnapshot


def build_help_text() -> str:
    return (
        'Nasuchan admin commands:\n'
        '/start - show this help\n'
        '/status - show backend runtime status across configured backends\n'
        '/jobs - list backend jobs and status\n'
        '/run - trigger a backend action\n'
        '/config - open runtime config actions\n'
        '/cancel - clear the active bot state'
    )


def format_health_message(status: HealthStatus) -> str:
    return f'Backend status: {status.status}\nGenerated at: {status.generated_at.isoformat()}'


def format_aggregated_health_message(
    snapshots: list[BackendHealthSnapshot],
    *,
    error_lookup: Callable[[Exception], str] | None = None,
) -> str:
    if not snapshots:
        return 'No backends are currently configured.'
    lines = ['Backend status:']
    for snapshot in snapshots:
        title = snapshot.backend.upper()
        if snapshot.error is not None:
            error_text = error_lookup(snapshot.error) if error_lookup is not None else str(snapshot.error)
            lines.append(f'- {title}: error | {error_text}')
            continue
        lines.append(f'- {title}: {snapshot.status}')
        if snapshot.generated_at is not None:
            lines.append(f'  Generated at: {snapshot.generated_at.isoformat()}')
    return '\n'.join(lines)


def format_jobs_message(jobs: list[JobSummary]) -> str:
    if not jobs:
        return 'No jobs were returned by the backend.'
    lines = ['Available jobs:']
    for job in jobs:
        lines.append(f'- {job.name} (`{job.key}`) | enabled={_bool_label(job.enabled)} | run_on_start={_bool_label(job.run_on_start)}')
    return '\n'.join(lines)


def format_aggregated_jobs_message(
    snapshot: AggregatedJobsSnapshot,
    *,
    error_lookup: Callable[[Exception], str] | None = None,
) -> str:
    if snapshot.fav_jobs is None and snapshot.aninamer_status is None and not snapshot.section_errors:
        return 'No backends are currently configured.'

    sections: list[str] = []
    if snapshot.fav_jobs is not None:
        sections.append('FAV\n' + format_jobs_message(snapshot.fav_jobs))
    elif 'fav' in snapshot.section_errors:
        sections.append(_format_error_section('FAV', snapshot.section_errors['fav'], error_lookup=error_lookup))

    if snapshot.aninamer_status is not None:
        sections.append(format_aninamer_status_message(snapshot.aninamer_status))
    elif 'aninamer' in snapshot.section_errors:
        sections.append(_format_error_section('ANINAMER', snapshot.section_errors['aninamer'], error_lookup=error_lookup))

    return '\n\n'.join(sections)


def format_aggregated_status_message(
    snapshot: AggregatedStatusSnapshot,
    *,
    error_lookup: Callable[[Exception], str] | None = None,
) -> str:
    if (
        snapshot.fav_job_count is None
        and snapshot.fav_readiness is None
        and snapshot.aninamer_status is None
        and not snapshot.section_errors
    ):
        return 'No backends are currently configured.'

    sections: list[str] = []
    if (
        snapshot.fav_job_count is not None
        or snapshot.fav_readiness is not None
        or {'fav', 'fav_readiness'} & snapshot.section_errors.keys()
    ):
        sections.append(
            format_fav_runtime_status_message(
                snapshot.fav_job_count,
                readiness=snapshot.fav_readiness,
                jobs_error=snapshot.section_errors.get('fav'),
                readiness_error=snapshot.section_errors.get('fav_readiness'),
                error_lookup=error_lookup,
            )
        )

    if snapshot.aninamer_status is not None:
        sections.append(format_aninamer_runtime_status_message(snapshot.aninamer_status))
    elif 'aninamer' in snapshot.section_errors:
        sections.append(_format_error_section('ANINAMER', snapshot.section_errors['aninamer'], error_lookup=error_lookup))

    return '\n\n'.join(sections)


def format_job_request_message(request: JobRequest, *, timed_out: bool = False) -> str:
    lines = [
        f'Job request #{request.id}',
        f'Target: {request.target}',
        f'Status: {request.status}',
    ]
    if request.started_at is not None:
        lines.append(f'Started at: {request.started_at.isoformat()}')
    if request.finished_at is not None:
        lines.append(f'Finished at: {request.finished_at.isoformat()}')
    if request.result:
        lines.append(f'Result: {request.result}')
    if request.error:
        lines.append(f'Error: {request.error}')
    if timed_out:
        lines.append('Status tracking timed out. The request still exists in the backend.')
    return '\n'.join(lines)


def format_aninamer_job_request_message(request: AninamerJobRequest, *, timed_out: bool = False) -> str:
    lines = [
        f'Aninamer request #{request.id}',
        f'Action: {request.action}',
        f'Status: {request.status}',
        f'Created at: {request.created_at.isoformat()}',
    ]
    if request.job_id is not None:
        lines.append(f'Job ID: {request.job_id}')
    if request.started_at is not None:
        lines.append(f'Started at: {request.started_at.isoformat()}')
    if request.finished_at is not None:
        lines.append(f'Finished at: {request.finished_at.isoformat()}')
    if request.error_message:
        lines.append(f'Error: {request.error_message}')
    if timed_out:
        lines.append('Status tracking timed out. The request still exists in the backend.')
    return '\n'.join(lines)


def format_aninamer_status_message(status: AninamerStatusResponse) -> str:
    lines = [
        'ANINAMER',
        (
            'Summary: '
            f'pending={status.summary.pending_count} '
            f'planning={status.summary.planning_count} '
            f'planned={status.summary.planned_count} '
            f'apply_requested={status.summary.apply_requested_count} '
            f'applying={status.summary.applying_count} '
            f'failed={status.summary.failed_count}'
        ),
    ]
    lines.extend(_format_status_items('Pending items', status.pending_items))
    lines.extend(_format_status_items('Failed items', status.failed_items))
    return '\n'.join(lines)


def format_fav_runtime_status_message(
    job_count: int | None,
    *,
    readiness: ReadinessStatus | None = None,
    jobs_error: Exception | None = None,
    readiness_error: Exception | None = None,
    error_lookup: Callable[[Exception], str] | None = None,
) -> str:
    lines = ['FAV']
    if readiness is not None:
        lines.append(f'Readiness: {readiness.status}')
        for name, check in sorted(readiness.checks.items()):
            lines.append(f'- {name}: {check.status} ({check.code}) | {check.message}')
    elif readiness_error is not None:
        error_text = error_lookup(readiness_error) if error_lookup is not None else str(readiness_error)
        lines.append(f'Readiness: error | {error_text}')

    if job_count is not None:
        lines.append(f'Configured jobs: {job_count}')
        lines.append('Running jobs: unavailable from current Fav API.')
    elif jobs_error is not None:
        error_text = error_lookup(jobs_error) if error_lookup is not None else str(jobs_error)
        lines.append(f'Jobs: error | {error_text}')
    return '\n'.join(lines)


def format_aninamer_runtime_status_message(status: AninamerStatusResponse) -> str:
    lines = [
        'ANINAMER',
        (
            'Summary: '
            f'pending={status.summary.pending_count} '
            f'planning={status.summary.planning_count} '
            f'planned={status.summary.planned_count} '
            f'apply_requested={status.summary.apply_requested_count} '
            f'applying={status.summary.applying_count} '
            f'failed={status.summary.failed_count}'
        ),
    ]
    active_items = [item for item in status.pending_items if item.status in {'pending', 'planning', 'apply_requested', 'applying'}]
    lines.extend(_format_status_items('Running items', active_items))
    lines.extend(_format_status_items('Failed items', status.failed_items))
    return '\n'.join(lines)


def format_aninamer_apply_page(items: list[AninamerStatusItem], *, page: int, page_size: int) -> str:
    if not items:
        return 'No Aninamer jobs are currently in planned state.'
    total_pages = max(ceil(len(items) / page_size), 1)
    start = page * page_size
    end = start + page_size
    page_items = items[start:end]
    lines = [f'Choose an Aninamer job to apply (page {page + 1}/{total_pages}):']
    for item in page_items:
        lines.append(f'- #{item.job_id} {item.series_name} | {item.watch_root_key} | updated_at={item.updated_at.isoformat()}')
    return '\n'.join(lines)


def format_seed_added_message(seed: Hanime1Seed) -> str:
    return f'Added Hanime1 scan target: {seed.video_id} | {seed.label}'


def _format_error_section(
    title: str,
    error: Exception,
    *,
    error_lookup: Callable[[Exception], str] | None = None,
) -> str:
    error_text = error_lookup(error) if error_lookup is not None else str(error)
    return f'{title}\nError: {error_text}'


def _format_status_items(title: str, items: list[AninamerStatusItem]) -> list[str]:
    if not items:
        return [f'{title}: none']
    lines = [f'{title}:']
    for item in items:
        lines.append(
            f'- #{item.job_id} {item.series_name} | {item.status} | {item.watch_root_key} | updated_at={item.updated_at.isoformat()}'
        )
    return lines


def _bool_label(value: bool) -> str:
    return 'yes' if value else 'no'
