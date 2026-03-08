from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

ControlRequestStatus = Literal['pending', 'running', 'succeeded', 'failed', 'rejected']
NotificationStatus = Literal['unread', 'read']


class HealthStatus(BaseModel):
    status: str
    generated_at: datetime


class JobSummary(BaseModel):
    key: str
    name: str
    enabled: bool
    run_on_start: bool


class ControlRequest(BaseModel):
    request_id: int
    kind: str
    target: str
    status: ControlRequestStatus
    requested_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: str = ''
    error: str = ''


class Hanime1Seed(BaseModel):
    video_id: str
    title: str
    label: str


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
