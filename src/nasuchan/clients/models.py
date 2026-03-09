from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

JobRequestStatus = Literal['pending', 'running', 'succeeded', 'failed', 'rejected']


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
