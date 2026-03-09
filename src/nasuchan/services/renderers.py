from __future__ import annotations

from math import ceil

from nasuchan.clients import Hanime1Seed, HealthStatus, JobRequest, JobSummary


def build_help_text() -> str:
    return (
        'Nasuchan admin commands:\n'
        '/start - show this help\n'
        '/health - check backend health\n'
        '/jobs - list available jobs\n'
        '/run - trigger a job\n'
        '/config - open runtime config actions\n'
        '/cancel - clear the active bot state'
    )


def format_health_message(status: HealthStatus) -> str:
    return f'Backend health: {status.status}\nGenerated at: {status.generated_at.isoformat()}'


def format_jobs_message(jobs: list[JobSummary]) -> str:
    if not jobs:
        return 'No jobs were returned by the backend.'
    lines = ['Available jobs:']
    for job in jobs:
        lines.append(f'- {job.name} (`{job.key}`) | enabled={_bool_label(job.enabled)} | run_on_start={_bool_label(job.run_on_start)}')
    return '\n'.join(lines)


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


def format_seed_page_message(seeds: list[Hanime1Seed], *, page: int, page_size: int) -> str:
    if not seeds:
        return 'No Hanime1 seeds are configured.'
    total_pages = ceil(len(seeds) / page_size)
    start = page * page_size
    end = start + page_size
    page_items = seeds[start:end]
    lines = [f'Hanime1 seeds (page {page + 1}/{total_pages}):']
    for seed in page_items:
        lines.append(f'- {seed.video_id} | {seed.label}')
    return '\n'.join(lines)


def format_seed_added_message(seed: Hanime1Seed) -> str:
    return f'Added Hanime1 seed: {seed.video_id} | {seed.label}'


def format_seed_deleted_message(video_id: str) -> str:
    return f'Deleted Hanime1 seed: {video_id}'


def _bool_label(value: bool) -> str:
    return 'yes' if value else 'no'
