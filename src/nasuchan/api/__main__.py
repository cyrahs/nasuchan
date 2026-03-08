from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from nasuchan.config import AppConfig, load_config

from .app import create_app

_DEFAULT_CONFIG_PATH = Path('./config.toml')


def configure_logging(config: AppConfig) -> None:
    logging.basicConfig(
        level=getattr(logging, config.logging.level, logging.INFO),
        format='%(asctime)s %(levelname)s [%(name)s] %(message)s',
    )


def main_sync() -> None:
    config = load_config(_DEFAULT_CONFIG_PATH)
    configure_logging(config)
    if config.public_api is None:
        msg = 'public_api section is required to run nasuchan.api'
        raise ValueError(msg)
    app = create_app(config)
    web.run_app(
        app,
        host=config.public_api.bind,
        port=config.public_api.port,
        print=None,
    )


if __name__ == '__main__':
    main_sync()
