from __future__ import annotations

import asyncio

from aiohttp import web


class PublicApiServer:
    def __init__(self, app: web.Application, *, host: str, port: int) -> None:
        self._app = app
        self._host = host
        self._port = port
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._startup_ready = asyncio.Event()
        self._startup_future: asyncio.Future[None] | None = None

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        self._startup_future = loop.create_future()
        self._startup_ready.set()
        try:
            await self.start()
        except Exception as exc:
            if not self._startup_future.done():
                self._startup_future.set_exception(exc)
            raise
        else:
            if not self._startup_future.done():
                self._startup_future.set_result(None)

        try:
            await asyncio.Future()
        finally:
            await self.stop()

    async def wait_started(self) -> None:
        await self._startup_ready.wait()
        await self._startup_future

    async def start(self) -> None:
        if self._runner is not None:
            return
        runner = web.AppRunner(self._app)
        await runner.setup()
        try:
            site = web.TCPSite(runner, host=self._host, port=self._port)
            await site.start()
        except Exception:
            await runner.cleanup()
            raise
        self._runner = runner
        self._site = site

    async def stop(self) -> None:
        if self._runner is None:
            return
        runner = self._runner
        self._runner = None
        self._site = None
        await runner.cleanup()
