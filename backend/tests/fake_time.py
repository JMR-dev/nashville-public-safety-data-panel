"""One clock for the pacer (monotonic seconds), the writer (epoch milliseconds), and the
worker's schedule. Sleeping advances it immediately and yields, so tasks interleave without
waiting on real time."""

import asyncio


class FakeTime:
    def __init__(self, epoch_ms: int, monotonic: float = 0.0) -> None:
        self.epoch = epoch_ms
        self.mono = monotonic

    def monotonic(self) -> float:
        return self.mono

    def epoch_ms(self) -> int:
        return self.epoch

    def advance(self, seconds: float) -> None:
        self.mono += seconds
        self.epoch += round(seconds * 1000)

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds)
        await asyncio.sleep(0)
