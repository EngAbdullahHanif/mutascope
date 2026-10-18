"""One executor for every live forward pass, with a bounded number of runs at a time.

At 5 users on Cloud Run, reads waited up to 10 s behind scoring (ADR 0035, item 1). Fast mode
and precise-mode batches each ran in ``asyncio.to_thread``, so as many ONNX runs as there were
requests competed for 2 vCPUs, each with its own intra-op threads, and everything else waited
for CPU. Here every forward call goes through :class:`Inference`:

- **Slots.** At most ``concurrency`` calls run at once, on a thread pool of that size (default
  1: one run already uses both vCPUs, ``onnx_threads``). A call takes a slot before it is handed
  to a thread, so the pool never holds a backlog.
- **Waiting.** A fast-mode request waits at most ``wait_seconds`` for a slot; then it is a 503
  with ``Retry-After`` (:func:`api.app.problems.inference_busy`). The precise-mode worker waits
  as long as it takes: a job is already queued, and its batches take turns with fast requests
  (asyncio's semaphore wakes waiters in order).
- **Release.** A slot is freed when the thread finishes, not when the awaiting request goes
  away: a forward pass cannot be interrupted, so freeing it earlier would let more runs than
  ``concurrency`` share the CPU. A pass still running when the server stops ends quietly.

Cache hits never take a slot. The calling task's context (its ``request_id``) is copied into the
thread, as ``asyncio.to_thread`` does, so the scorer's log lines keep it.
"""

import asyncio
import contextlib
import contextvars
import functools
import math
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, TypeVar

from api.app import problems

T = TypeVar("T")


class Inference:
    """The thread pool and slots of live forward passes (see the module docstring)."""

    def __init__(self, *, concurrency: int, wait_seconds: float) -> None:
        self.concurrency = concurrency
        self.wait_seconds = wait_seconds
        self._executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="onnx")
        self._slots = asyncio.Semaphore(concurrency)
        self._running = 0
        self._waiting = 0

    @property
    def running(self) -> int:
        """Forward passes running now."""
        return self._running

    @property
    def waiting(self) -> int:
        """Calls waiting for a slot."""
        return self._waiting

    async def run(self, fn: Callable[..., T], *args: Any, wait: bool = True, **kwargs: Any) -> T:
        """``fn(*args, **kwargs)`` on the pool once a slot is free.

        Args:
            fn: A blocking call (a forward pass).
            wait: True: wait for a slot for as long as it takes (precise mode). False: at most
                ``wait_seconds`` (fast mode).

        Raises:
            ApiError: 503 with ``Retry-After`` when ``wait`` is False and no slot freed in time.
        """
        self._waiting += 1
        try:
            if wait:
                await self._slots.acquire()
            else:
                try:
                    await asyncio.wait_for(self._slots.acquire(), self.wait_seconds)
                except TimeoutError:
                    raise problems.inference_busy(
                        self.wait_seconds, math.ceil(self.wait_seconds)
                    ) from None
        finally:
            self._waiting -= 1
        loop = asyncio.get_running_loop()
        call = functools.partial(contextvars.copy_context().run, fn, *args, **kwargs)
        self._running += 1
        try:
            future: Future[T] = self._executor.submit(call)
        except BaseException:
            self._release()
            raise
        future.add_done_callback(lambda _: self._release_from_thread(loop))
        return await asyncio.wrap_future(future)

    def _release_from_thread(self, loop: asyncio.AbstractEventLoop) -> None:
        # At shutdown the loop can close while a pass still runs; nothing waits for its slot.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(self._release)

    def _release(self) -> None:
        self._running -= 1
        self._slots.release()

    def close(self) -> None:
        """Stop the pool; a pass in flight finishes, nothing new starts."""
        self._executor.shutdown(wait=False, cancel_futures=True)
