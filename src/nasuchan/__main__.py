from __future__ import annotations

import asyncio

from .combined import run_combined


def main_sync() -> None:
    asyncio.run(run_combined())


if __name__ == '__main__':
    main_sync()
