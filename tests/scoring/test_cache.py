"""The log-probability cache: keys, round trips and refusing bad entries."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from mutascope.scoring.cache import CacheError, CacheKey, LogProbCache

KEY = CacheKey("facebook/esm2_t6_8M_UR50D", "c" * 40, "MKV", None, "masked", "full")


def test_round_trip(tmp_path: Path) -> None:
    cache = LogProbCache(tmp_path)
    array = np.arange(60, dtype=np.float32).reshape(3, 20)
    assert cache.get(KEY) is None
    cache.put(KEY, array)
    hit = cache.get(KEY)
    assert hit is not None
    np.testing.assert_array_equal(hit, array)
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.parametrize(
    "changed",
    [
        {"model_id": "facebook/esm2_t12_35M_UR50D"},
        {"revision": "d" * 40},
        {"sequence": "MKA"},
        {"struct_tokens": "abc"},
        {"method": "wt"},
        {"region": "msa:2-3"},
    ],
    ids=lambda c: next(iter(c)),
)
def test_every_field_changes_the_key(changed: dict[str, str]) -> None:
    assert replace(KEY, **changed).digest() != KEY.digest()  # type: ignore[arg-type]


def test_fields_cannot_run_together() -> None:
    """Plain concatenation would make these two keys equal."""
    a = CacheKey("model", "rev", "MK", None, "masked", "full")
    b = CacheKey("model", "revM", "K", None, "masked", "full")
    assert a.digest() != b.digest()


def test_wrong_shape_is_refused(tmp_path: Path) -> None:
    cache = LogProbCache(tmp_path)
    with pytest.raises(CacheError):
        cache.put(KEY, np.zeros((4, 20), dtype=np.float32))
    path = cache.path(KEY)
    path.parent.mkdir(parents=True)
    np.save(path, np.zeros((4, 20), dtype=np.float32))
    with pytest.raises(CacheError, match="expected float32"):
        cache.get(KEY)


def test_a_partly_scored_entry_round_trips_with_its_nan_rows(tmp_path: Path) -> None:
    cache = LogProbCache(tmp_path)
    array = np.full((3, 20), np.nan, dtype=np.float32)
    array[1] = 0.5
    cache.put(KEY, array)
    hit = cache.get(KEY)
    assert hit is not None
    np.testing.assert_array_equal(hit, array)  # NaN == NaN here


def test_computation_log_accumulates_across_cache_objects(tmp_path: Path) -> None:
    """The log is what lets a resumed run report the time an interrupted run already spent."""
    assert LogProbCache(tmp_path).computations() == []
    LogProbCache(tmp_path).record(KEY, positions=2, seconds=1.25)
    LogProbCache(tmp_path).record(replace(KEY, region="msa:2-3"), positions=1, seconds=0.5)
    first, second = LogProbCache(tmp_path).computations()
    assert (first["digest"], first["positions"], first["seconds"]) == (KEY.digest(), 2, 1.25)
    assert (first["model_id"], first["revision"], first["length"]) == (KEY.model_id, "c" * 40, 3)
    assert (second["region"], second["method"]) == ("msa:2-3", "masked")
