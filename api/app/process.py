"""One server process: an exclusive lock held for the server's lifetime.

The job queue, the rate limiter and the open-stream counts live in one process's memory
(ADR 0031, 0032). With two worker processes each would have its own queue and its own counts,
so every limit would silently double and two precise jobs could run at once on 2 vCPUs. The
start command runs one worker (:mod:`api.app.serve`); this lock makes any other launch that
would start a second process on the host (``uvicorn --workers 4``, gunicorn) fail at startup.
"""

import fcntl
import os
from pathlib import Path


class SecondProcessError(RuntimeError):
    """Another API process holds the lock."""


class ProcessLock:
    """``flock`` on ``path``, released when the process exits even if it crashes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> None:
        """Raises: SecondProcessError: The lock is held by another process (or open file)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise SecondProcessError(
                f"another MutaScope API process holds {self.path}; the API must run as one "
                "process (its job queue and rate limits are in memory, ADR 0032)"
            ) from None
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
