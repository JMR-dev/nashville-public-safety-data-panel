"""One consistent read transaction per API request, kept off the event loop."""

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import Connection, Engine


class Snapshot:
    """Runs every query of one request in a single SQLite read transaction.

    The connection lives on one dedicated worker thread, so queries run one at a time, off the
    event loop. SQLite takes the WAL snapshot at the first read, and every later query in the
    request sees that same snapshot even while the ingestion worker commits.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="panel-snapshot")
        self._connection: Connection | None = None

    async def run[T](self, query: Callable[[Connection], T]) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._run, query)

    def _run[T](self, query: Callable[[Connection], T]) -> T:
        if self._connection is None:
            self._connection = self._engine.connect()
            self._connection.begin()
        return query(self._connection)

    async def close(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._close)
        self._executor.shutdown()

    def _close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
