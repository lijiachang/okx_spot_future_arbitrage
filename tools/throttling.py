import asyncio
import time
from collections import deque
from typing import Deque


class Throttling():
    def __init__(self, max_token=100, token_speed=0.2):
        """此类没有考虑线程安全的问题，简单实现"""
        self._max_token = max_token
        self._last_update = time.time()
        self._token_speed = token_speed
        self._current_token = self._max_token

    def __enter__(self):
        delta = (time.time() - self._last_update) * self._token_speed
        self._current_token = min(self._current_token + delta, self._max_token)
        got_token = False
        if self._current_token >= 1:
            self._current_token -= 1
            got_token = True

        self._last_update = time.time()
        return got_token

    def __exit__(self, *exc):
        pass


class AsyncThrottler:
    """
    https://github.com/hallazzang/asyncio-throttle
    """
    def __init__(self, rate_limit: int, period=1.0, retry_interval=0.01):
        self.rate_limit = rate_limit
        self.period = period
        self.retry_interval = retry_interval

        self._task_logs: Deque[float] = deque()

    def flush(self):
        now = time.monotonic()
        while self._task_logs:
            if now - self._task_logs[0] > self.period:
                self._task_logs.popleft()
            else:
                break

    async def acquire(self):
        while True:
            self.flush()
            if len(self._task_logs) < self.rate_limit:
                break
            await asyncio.sleep(self.retry_interval)

        self._task_logs.append(time.monotonic())

    async def __aenter__(self):
        await self.acquire()

    async def __aexit__(self, exc_type, exc, tb):
        pass
