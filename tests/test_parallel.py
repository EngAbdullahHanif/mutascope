"""Worker processes for independent tasks: order, memory budget and errors (ADR 0022)."""

import os
import sys
import time
from pathlib import Path

import pytest

from mutascope.utils.parallel import THREAD_VARIABLES, run_parallel


def interval(seconds: float, tag: int) -> tuple[int, float, float, str | None]:
    """Sleeps; returns when it ran and the BLAS thread cap it saw. Module-level: picklable."""
    start = time.monotonic()
    time.sleep(seconds)
    return tag, start, time.monotonic(), os.environ.get("VECLIB_MAXIMUM_THREADS")


def fail_on(tag: int) -> int:
    if tag == 2:
        raise ValueError(f"task {tag} refused")
    return tag


@pytest.fixture(autouse=True)
def importable_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    # Workers unpickle this module's functions; spawned children copy the parent's sys.path.
    monkeypatch.setattr(sys, "path", [str(Path(__file__).parents[1]), *sys.path])


def test_results_come_back_in_task_order_from_single_threaded_workers() -> None:
    # The first task is the slowest, so tasks finish out of order.
    tasks = [(0.6, 0), (0.05, 1), (0.05, 2), (0.05, 3)]
    finished: list[int] = []
    before = {v: os.environ.get(v) for v in THREAD_VARIABLES}
    results = run_parallel(interval, tasks, jobs=3, on_done=lambda i, _: finished.append(i))
    assert [r[0] for r in results] == [0, 1, 2, 3]
    assert finished[-1] == 0
    assert {r[3] for r in results} == {"1"}
    # The cap is set for the children only; the parent's environment is restored.
    assert {v: os.environ.get(v) for v in THREAD_VARIABLES} == before


def test_tasks_over_budget_never_run_together() -> None:
    """Two weight-3 tasks under a budget of 4 run one after the other; small ones fill in."""
    tasks = [(0.4, 0), (0.4, 1), (0.1, 2), (0.1, 3)]
    results = run_parallel(interval, tasks, jobs=4, weights=[3, 3, 1, 1], budget=4)
    (_, start0, end0, _), (_, start1, end1, _) = results[0], results[1]
    assert start1 >= end0 or start0 >= end1
    # A small task started while the first large one ran (it fits beside it).
    assert any(start < end0 for _, start, _, _ in results[2:])


def test_a_task_larger_than_the_budget_still_runs_alone() -> None:
    results = run_parallel(interval, [(0.01, 0), (0.01, 1)], jobs=2, weights=[9, 1], budget=4)
    assert [r[0] for r in results] == [0, 1]


@pytest.mark.parametrize("jobs", [1, 2])
def test_a_failing_task_fails_the_run(jobs: int) -> None:
    with pytest.raises(ValueError, match="task 2 refused"):
        run_parallel(fail_on, [(t,) for t in range(4)], jobs=jobs)


def test_bad_arguments_fail() -> None:
    with pytest.raises(ValueError, match="jobs must be at least 1"):
        run_parallel(fail_on, [(0,)], jobs=0)
    with pytest.raises(ValueError, match="1 weights for 2 tasks"):
        run_parallel(fail_on, [(0,), (1,)], jobs=2, weights=[1])
