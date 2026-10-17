"""Release check: a rerun of the deployed model's steps must reproduce the released files.

``train final`` already blocks a bundle whose refit disagrees with ``train calibrate`` or ``train
cv`` (ADR 0025), but both of those are rerun by the same pipeline, so its checks only show that
one run is consistent with itself. This module compares the rerun with what was released: every
file in the same place, every table with the same columns, dtypes and rows, and every value equal
to the bit (numbers compared as raw bytes, so ``-0.0`` differs from ``0.0`` and NaNs must have the
same payload).

Columns and JSON keys named in ``ignore`` are left out of the comparison: they are provenance
stamps (the git commit and the hash of the resolved config, which includes absolute paths), so a
rerun from another commit or directory always changes them although no result does. They must
still be present on both sides.
"""

import json
import math
from collections.abc import Collection
from pathlib import Path

import numpy as np
import pandas as pd

from mutascope.utils.config import as_str_dict

NUMERIC_KINDS = "biuf"
"""numpy dtype kinds compared as raw bytes: bool, signed and unsigned int, float. Pandas'
nullable dtypes (``boolean``, ``Float64``) are not numpy dtypes and are compared by value."""


def _missing(x: object) -> bool:
    return x is None or x is pd.NA or (isinstance(x, float) and math.isnan(x))


def _same(x: object, y: object) -> bool:
    """Scalar equality in which two missing values (None, NaN, pd.NA) are equal."""
    x_missing, y_missing = _missing(x), _missing(y)
    if x_missing or y_missing:
        return x_missing and y_missing
    return bool(x == y)


def compare_tables(actual: pd.DataFrame, expected: pd.DataFrame, ignore: Collection[str]) -> str:
    """Why ``actual`` differs from ``expected`` outside the ``ignore`` columns ("" if it does not).

    Args:
        actual: The rerun's table.
        expected: The released table.
        ignore: Provenance columns, skipped if present in both and reported if in only one.
    """
    if list(actual.columns) != list(expected.columns):
        return f"columns {list(actual.columns)} != {list(expected.columns)}"
    if len(actual) != len(expected):
        return f"{len(actual):,} rows != {len(expected):,}"
    for column in actual.columns:
        if column in ignore:
            continue
        a, e = actual[column], expected[column]
        if a.dtype != e.dtype:
            return f"{column}: dtype {a.dtype} != {e.dtype}"
        if isinstance(a.dtype, np.dtype) and a.dtype.kind in NUMERIC_KINDS:
            values_a, values_e = a.to_numpy(), e.to_numpy()
            if values_a.tobytes() == values_e.tobytes():
                continue
            differ = values_a.view(np.uint8).reshape(len(a), -1) != values_e.view(np.uint8).reshape(
                len(e), -1
            )
            rows = int(differ.any(axis=1).sum())
            detail = ""
            if a.dtype.kind == "f":
                delta = np.abs(values_a - values_e)
                detail = f", max |diff| {np.nanmax(delta) if np.isfinite(delta).any() else 'nan'}"
            return f"{column}: {rows:,} of {len(a):,} values differ{detail}"
        if not a.equals(e):
            rows = sum(not _same(x, y) for x, y in zip(a.tolist(), e.tolist(), strict=True))
            return f"{column}: {rows:,} of {len(a):,} values differ"
    return ""


def compare_json(actual: object, expected: object, ignore: Collection[str]) -> str:
    """Why two JSON objects differ outside the top-level ``ignore`` keys ("" if they do not)."""
    try:
        a, e = as_str_dict(actual, "rerun JSON"), as_str_dict(expected, "released JSON")
    except TypeError:
        return "" if actual == expected else "values differ"
    if sorted(a) != sorted(e):
        return f"keys {sorted(a)} != {sorted(e)}"
    differ = sorted(k for k in a if k not in ignore and a[k] != e[k])
    return f"keys differ: {differ}" if differ else ""


def compare_file(actual: Path, expected: Path, ignore: Collection[str]) -> str:
    """Why file ``actual`` differs from ``expected`` ("" if it does not): Parquet as tables,
    JSON as objects, anything else byte for byte."""
    if actual.suffix == ".parquet":
        return compare_tables(pd.read_parquet(actual), pd.read_parquet(expected), ignore)
    if actual.suffix == ".json":
        return compare_json(
            json.loads(actual.read_text()), json.loads(expected.read_text()), ignore
        )
    return "" if actual.read_bytes() == expected.read_bytes() else "bytes differ"


def compare_trees(actual: Path, expected: Path, ignore: Collection[str]) -> list[str]:
    """Every difference between two directories of released files, one line each.

    Both must hold the same relative file paths; hidden files (``.DS_Store``) are skipped.
    """
    if not expected.is_dir():
        return [f"{expected}: no released directory to compare with"]
    if not actual.is_dir():
        return [f"{actual}: not a directory"]

    def files(root: Path) -> set[str]:
        return {
            str(p.relative_to(root))
            for p in root.rglob("*")
            if p.is_file() and not any(part.startswith(".") for part in p.relative_to(root).parts)
        }

    found, released = files(actual), files(expected)
    lines = [f"{expected / name}: missing from the rerun" for name in sorted(released - found)]
    lines += [f"{actual / name}: not in the release" for name in sorted(found - released)]
    for name in sorted(found & released):
        reason = compare_file(actual / name, expected / name, ignore)
        if reason:
            lines.append(f"{name}: {reason}")
    return lines
