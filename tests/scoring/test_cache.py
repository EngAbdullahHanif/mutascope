"""The log-probability cache: keys, round trips and refusing bad entries."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from mutascope.scoring.cache import CacheError, CacheKey, LogProbCache

KEY = CacheKey("facebook/esm2_t6_8M_UR50D", "c" * 40, "MKV", None, "masked")


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
    ],
    ids=lambda c: next(iter(c)),
)
def test_every_field_changes_the_key(changed: dict[str, str]) -> None:
    assert replace(KEY, **changed).digest() != KEY.digest()  # type: ignore[arg-type]


def test_fields_cannot_run_together() -> None:
    """Plain concatenation would make these two keys equal."""
    a = CacheKey("model", "rev", "MK", None, "masked")
    b = CacheKey("model", "revM", "K", None, "masked")
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
