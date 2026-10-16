"""Run independent tasks in worker processes, one numerical thread each (ADR 0022).

Supervised models are fit per assay, and assays share nothing, so the work is spread over
processes, one assay at a time per worker. Two rules keep that safe:

- **Spawned, single-threaded workers.** Each worker starts fresh (``spawn``) with OpenMP and
  the BLAS libraries capped at one thread (Accelerate on macOS reads ``VECLIB_MAXIMUM_THREADS``
  when it loads). Otherwise 13 workers that each also thread their linear algebra would
  oversubscribe the cores. Children inherit ``os.environ`` as it is when they start; the
  parent's already-loaded libraries are unaffected.
- **One thread in this process too.** ``jobs = 1`` runs the tasks here, where BLAS is already
  loaded, so environment variables are too late; ``threadpoolctl`` caps the loaded libraries
  instead. Without it the serial result depended on the machine: multi-threaded OpenBLAS sums
  in another order, and on a 4-core Linux runner the ridge CV test's pooled Spearman moved by
  up to 3.4e-3 against the single-threaded workers (bit-identical with the cap).
- **Results in task order.** Tasks may finish in any order; the list returned is in the order
  given, so the output never depends on the number of workers.

Memory, not cores, is what limits this on a 24 GB machine: one worker cross-validating ridge on
the largest assay (16,897 variants x 2,647 columns) peaks near 2 GB, and the 13 largest assays
started together needed about 26 GB and swapped (workers at 3-17% CPU). Tasks can therefore
carry a weight (variants) and share a budget.

Functions and arguments are pickled into the workers, so they must be module-level functions,
classes, ``functools.partial`` objects or dataclass instances, not closures.
"""

import os
from collections.abc import Callable, Generator, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from contextlib import contextmanager
from multiprocessing import get_context
from typing import Any, TypeVar

from threadpoolctl import threadpool_limits  # pyright: ignore[reportMissingTypeStubs]

THREAD_VARIABLES = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)
"""Environment variables that cap the threads of OpenMP and the BLAS libraries at load time."""

T = TypeVar("T")


def default_jobs() -> int:
    """All cores but one (at least 1)."""
    return max(1, (os.cpu_count() or 2) - 1)


@contextmanager
def single_threaded_children() -> Generator[None]:
    """Processes started inside the block use one thread per numerical library."""
    saved = {name: os.environ.get(name) for name in THREAD_VARIABLES}
    os.environ.update(dict.fromkeys(THREAD_VARIABLES, "1"))
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def run_parallel(
    function: Callable[..., T],
    tasks: Sequence[tuple[Any, ...]],
    *,
    jobs: int,
    weights: Sequence[float] | None = None,
    budget: float | None = None,
    on_done: Callable[[int, T], None] | None = None,
) -> list[T]:
    """``[function(*task) for task in tasks]``, in up to ``jobs`` worker processes.

    With ``weights`` and ``budget``, tasks also share a memory budget: a task starts only if
    the weights of the running tasks plus its own fit in ``budget`` (a task that fits nothing
    still starts when nothing else runs). Pending tasks are tried in the order given, so when
    the next large task does not fit, smaller ones behind it fill the free workers. Put the
    longest first.

    ``on_done(index, result)`` is called in this process as each task finishes. The first
    exception raised by a task cancels the tasks not yet started and is re-raised here.
    ``jobs = 1`` (or a single task) runs everything in this process, in order, with the loaded
    BLAS and OpenMP libraries capped at one thread like a worker's, so the result does not
    depend on ``jobs``.

    Raises:
        ValueError: ``jobs`` is below 1, or ``weights`` does not match ``tasks``.
    """
    if jobs < 1:
        raise ValueError(f"jobs must be at least 1, got {jobs}")
    sizes = [0.0] * len(tasks) if weights is None else [float(w) for w in weights]
    if len(sizes) != len(tasks):
        raise ValueError(f"{len(sizes)} weights for {len(tasks)} tasks")
    limit = float("inf") if budget is None else budget
    results: dict[int, T] = {}

    def finished(index: int, result: T) -> None:
        results[index] = result
        if on_done is not None:
            on_done(index, result)

    workers = min(jobs, len(tasks))
    if workers <= 1:
        with threadpool_limits(limits=1):
            for index, task in enumerate(tasks):
                finished(index, function(*task))
        return [results[index] for index in range(len(tasks))]
    pending = list(range(len(tasks)))
    running: dict[Future[T], int] = {}
    with (
        single_threaded_children(),
        ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn")) as pool,
    ):
        try:
            while pending or running:
                in_flight = sum(sizes[i] for i in running.values())
                for index in list(pending):
                    if len(running) >= workers:
                        break
                    if not running or in_flight + sizes[index] <= limit:
                        running[pool.submit(function, *tasks[index])] = index
                        in_flight += sizes[index]
                        pending.remove(index)
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    finished(running.pop(future), future.result())
        except BaseException:
            pool.shutdown(wait=True, cancel_futures=True)
            raise
    return [results[index] for index in range(len(tasks))]
