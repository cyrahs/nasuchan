from __future__ import annotations

from nasuchan.clients import FavBackendClient, Hanime1VideoListResponse


class RuntimeApiService:
    def __init__(self, backend_client: FavBackendClient) -> None:
        self._backend_client = backend_client

    async def list_hanime1_videos(self) -> Hanime1VideoListResponse:
        return await self._backend_client.list_hanime1_videos()
