from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

JobRequestStatus = Literal['pending', 'running', 'succeeded', 'failed', 'rejected']
AninamerJobStatus = Literal['pending', 'planning', 'planned', 'apply_requested', 'applying', 'succeeded', 'failed']
AninamerTrackedJobStatus = Literal['pending', 'planning', 'planned', 'apply_requested', 'applying', 'failed']
AninamerJobRequestAction = Literal['scan_now', 'apply_job']
AninamerJobRequestStatus = Literal['pending', 'running', 'succeeded', 'failed', 'rejected']


class HealthStatus(BaseModel):
    status: str
    generated_at: datetime


class JobSummary(BaseModel):
    key: str
    name: str
    enabled: bool
    run_on_start: bool
    cron: str


class JobRequest(BaseModel):
    id: int
    target: str
    status: JobRequestStatus
    requested_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: str = ''
    error: str = ''


class Hanime1Seed(BaseModel):
    video_id: str
    title: str
    label: str


class Hanime1Video(BaseModel):
    video_id: str
    title: str
    downloaded: bool
    uploader: str | None = None
    release_date: str | None = None
    plot: str | None = None
    watch_url: str


class Hanime1VideoListResponse(BaseModel):
    items: list[Hanime1Video]
    total: int


class AninamerHealthStatus(BaseModel):
    status: Literal['ok']


class AninamerRuntimeStatus(BaseModel):
    auto_apply: bool
    settle_seconds: int
    scan_interval_seconds: int
    watch_root_keys: list[str]
    last_scan_at: datetime | None = None


class AninamerJob(BaseModel):
    id: int
    series_name: str
    watch_root_key: str
    source_kind: Literal['monitor', 'api']
    status: AninamerJobStatus
    tmdb_id: int | None = None
    video_moves_count: int
    subtitle_moves_count: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_stage: str | None = None
    error_message: str | None = None


class AninamerJobListResponse(BaseModel):
    items: list[AninamerJob]
    total: int


class AninamerJobRequest(BaseModel):
    id: int
    action: AninamerJobRequestAction
    status: AninamerJobRequestStatus
    job_id: int | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: str | None = None


class AninamerStatusSummary(BaseModel):
    pending_count: int
    planning_count: int
    planned_count: int
    apply_requested_count: int
    applying_count: int
    failed_count: int


class AninamerStatusItem(BaseModel):
    job_id: int
    series_name: str
    watch_root_key: str
    status: AninamerTrackedJobStatus
    updated_at: datetime
    tmdb_id: int | None = None
    video_moves_count: int
    subtitle_moves_count: int
    error_stage: str | None = None
    error_message: str | None = None


class AninamerStatusResponse(BaseModel):
    summary: AninamerStatusSummary
    pending_items: list[AninamerStatusItem]
    failed_items: list[AninamerStatusItem]
