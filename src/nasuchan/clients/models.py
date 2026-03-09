from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

JobRequestStatus = Literal['pending', 'running', 'succeeded', 'failed', 'rejected']
NotificationStatus = Literal['unread', 'read']


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


class NotificationRecord(BaseModel):
    model_config = ConfigDict(extra='ignore')

    id: int
    kind: str
    source: str
    title: str
    body: str
    link_url: str
    image_url: str
    payload: dict[str, Any]
    status: NotificationStatus
    created_at: datetime
    read_at: datetime | None = None
