from __future__ import annotations

from nasuchan.clients import FavBackendClient, Hanime1DownloadedIdsResponse


class RuntimeApiService:
    def __init__(self, backend_client: FavBackendClient) -> None:
        self._backend_client = backend_client

    async def get_hanime1_downloaded_ids(self, *, if_none_match: str | None = None) -> Hanime1DownloadedIdsResponse:
        return await self._backend_client.get_hanime1_downloaded_ids(if_none_match=if_none_match)
