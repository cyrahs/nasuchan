from __future__ import annotations

import asyncio

from .app import run_polling


def main_sync() -> None:
    asyncio.run(run_polling())


if __name__ == '__main__':
    main_sync()
