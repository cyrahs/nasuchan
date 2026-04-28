from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from nasuchan.clients import (
    AninamerClient,
    AninamerJobRequest,
    AninamerStatusItem,
    AninamerStatusResponse,
    BackendApiError,
    FavBackendClient,
    Hanime1Seed,
    JobRequest,
    JobSummary,
)


@dataclass(frozen=True, slots=True)
class BackendHealthSnapshot:
    backend: str
    status: str | None = None
    generated_at: datetime | None = None
    error: BackendApiError | None = None


@dataclass(frozen=True, slots=True)
class AggregatedJobsSnapshot:
    fav_jobs: list[JobSummary] | None = None
    aninamer_status: AninamerStatusResponse | None = None
    section_errors: dict[str, BackendApiError] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AggregatedStatusSnapshot:
    fav_job_count: int | None = None
    aninamer_status: AninamerStatusResponse | None = None
    section_errors: dict[str, BackendApiError] = field(default_factory=dict)


@dataclass(slots=True)
class BackendCommandService:
    fav_client: FavBackendClient | None = None
    aninamer_client: AninamerClient | None = None

    def configured_backend_names(self) -> list[str]:
        backends: list[str] = []
        if self.fav_client is not None:
            backends.append('fav')
        if self.aninamer_client is not None:
            backends.append('aninamer')
        return backends

    def available_run_backends(self) -> list[str]:
        return self.configured_backend_names()

    @property
    def has_fav(self) -> bool:
        return self.fav_client is not None

    @property
    def has_aninamer(self) -> bool:
        return self.aninamer_client is not None

    async def collect_health(self) -> list[BackendHealthSnapshot]:
        snapshots: list[BackendHealthSnapshot] = []
        if self.fav_client is not None:
            snapshots.append(await self._fav_health_snapshot())
        if self.aninamer_client is not None:
            snapshots.append(await self._aninamer_health_snapshot())
        return snapshots

    async def collect_jobs(self) -> AggregatedJobsSnapshot:
        section_errors: dict[str, BackendApiError] = {}
        fav_jobs: list[JobSummary] | None = None
        aninamer_status: AninamerStatusResponse | None = None

        if self.fav_client is not None:
            try:
                fav_jobs = await self.fav_client.list_jobs()
            except BackendApiError as exc:
                section_errors['fav'] = exc

        if self.aninamer_client is not None:
            try:
                aninamer_status = await self.aninamer_client.get_status()
            except BackendApiError as exc:
                section_errors['aninamer'] = exc

        return AggregatedJobsSnapshot(
            fav_jobs=fav_jobs,
            aninamer_status=aninamer_status,
            section_errors=section_errors,
        )

    async def collect_status(self) -> AggregatedStatusSnapshot:
        section_errors: dict[str, BackendApiError] = {}
        fav_job_count: int | None = None
        aninamer_status: AninamerStatusResponse | None = None

        if self.fav_client is not None:
            try:
                fav_job_count = len(await self.fav_client.list_jobs())
            except BackendApiError as exc:
                section_errors['fav'] = exc

        if self.aninamer_client is not None:
            try:
                aninamer_status = await self.aninamer_client.get_status()
            except BackendApiError as exc:
                section_errors['aninamer'] = exc

        return AggregatedStatusSnapshot(
            fav_job_count=fav_job_count,
            aninamer_status=aninamer_status,
            section_errors=section_errors,
        )

    async def list_fav_jobs(self) -> list[JobSummary]:
        if self.fav_client is None:
            msg = 'Fav backend is not configured.'
            raise RuntimeError(msg)
        return await self.fav_client.list_jobs()

    async def create_fav_job_request(self, target: str) -> JobRequest:
        if self.fav_client is None:
            msg = 'Fav backend is not configured.'
            raise RuntimeError(msg)
        return await self.fav_client.create_job_request(target)

    async def add_hanime1_scan_target(self, raw_target: str) -> Hanime1Seed:
        if self.fav_client is None:
            msg = 'Fav backend is not configured.'
            raise RuntimeError(msg)
        return await self.fav_client.add_hanime1_seed(raw_target)

    async def create_aninamer_scan_now_request(self) -> AninamerJobRequest:
        if self.aninamer_client is None:
            msg = 'Aninamer backend is not configured.'
            raise RuntimeError(msg)
        return await self.aninamer_client.create_job_request('scan_now')

    async def create_aninamer_apply_job_request(self, job_id: int) -> AninamerJobRequest:
        if self.aninamer_client is None:
            msg = 'Aninamer backend is not configured.'
            raise RuntimeError(msg)
        return await self.aninamer_client.create_job_request('apply_job', job_id=job_id)

    async def list_aninamer_planned_jobs(self) -> list[AninamerStatusItem]:
        if self.aninamer_client is None:
            msg = 'Aninamer backend is not configured.'
            raise RuntimeError(msg)
        status = await self.aninamer_client.get_status()
        return [item for item in status.pending_items if item.status == 'planned']

    async def _fav_health_snapshot(self) -> BackendHealthSnapshot:
        assert self.fav_client is not None
        try:
            status = await self.fav_client.health()
        except BackendApiError as exc:
            return BackendHealthSnapshot(backend='fav', error=exc)
        return BackendHealthSnapshot(
            backend='fav',
            status=status.status,
            generated_at=status.generated_at,
        )

    async def _aninamer_health_snapshot(self) -> BackendHealthSnapshot:
        assert self.aninamer_client is not None
        try:
            status = await self.aninamer_client.health()
        except BackendApiError as exc:
            return BackendHealthSnapshot(backend='aninamer', error=exc)
        return BackendHealthSnapshot(backend='aninamer', status=status.status)
