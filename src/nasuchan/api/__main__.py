from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from nasuchan.config import AppConfig, PublicApiSettings, load_config

from .app import create_app
from .server import PublicApiServer

_DEFAULT_CONFIG_PATH = Path('./config.toml')


def configure_logging(config: AppConfig) -> None:
    logging.basicConfig(
        level=getattr(logging, config.logging.level, logging.INFO),
        format='%(asctime)s %(levelname)s [%(name)s] %(message)s',
    )


def main_sync() -> None:
    asyncio.run(run_public_api())


async def run_public_api(config_path: Path = _DEFAULT_CONFIG_PATH) -> None:
    config = load_config(config_path)
    configure_logging(config)
    public_api = _require_public_api_config(config)
    app = create_app(config)
    server = PublicApiServer(
        app,
        host=public_api.bind,
        port=public_api.port,
    )
    await server.run()


def _require_public_api_config(config: AppConfig) -> PublicApiSettings:
    if config.public_api is None:
        msg = 'public_api section is required to run nasuchan.api'
        raise ValueError(msg)
    return config.public_api


if __name__ == '__main__':
    main_sync()
