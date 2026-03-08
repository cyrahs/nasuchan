from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

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


class Hanime1DownloadedIdsPayload(BaseModel):
    ids: list[str]
    count: int
    generated_at: datetime

    @model_validator(mode='after')
    def validate_count(self) -> Hanime1DownloadedIdsPayload:
        if self.count != len(self.ids):
            msg = 'count must match the number of ids'
            raise ValueError(msg)
        return self


class Hanime1DownloadedIdsResponse(BaseModel):
    not_modified: bool = False
    etag: str | None = None
    cache_control: str | None = None
    payload: Hanime1DownloadedIdsPayload | None = None

    @model_validator(mode='after')
    def validate_payload(self) -> Hanime1DownloadedIdsResponse:
        if self.not_modified and self.payload is not None:
            msg = 'payload must be omitted when not_modified is true'
            raise ValueError(msg)
        if not self.not_modified and self.payload is None:
            msg = 'payload is required when not_modified is false'
            raise ValueError(msg)
        return self


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
