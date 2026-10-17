"""Proof that the bundle's rounding changes no reported number beyond a stated tolerance.

For every protein file as written, the numbers the project reports are recomputed twice: from
the unrounded values the file was encoded from, and from the decoded file. Per assay:

- Spearman of each zero-shot landscape and of the supervised prediction (out-of-fold at the
  measured variants) against the measured ``DMS_score``;
- coverage: the share of measured variants inside their 80% and 90% intervals (inclusive
  bounds, as ``train final`` counts them).

Where a results table reports the same quantity from the same numbers, the decoded value is
also compared with it: zero-shot Spearman from ``results/zero_shot_<model>_single.parquet`` on
the assays whose benchmark region is the full target, and coverage from ``final_checks``. The
supervised CV Spearman is pooled per fold on each fold's training scale and per scheme, so it
is not recomputable from one landscape; there the comparison is with the source only.

Aggregates are compared too: the ProteinGym aggregate of the per-assay Spearman values
(:func:`mutascope.eval.aggregate.aggregate`), and the mean coverage over assays, which is what
``benchmarks.json`` reports.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from mutascope.eval.aggregate import AssayGroups, aggregate
from mutascope.eval.metrics import spearman
from mutascope.export.format import FloatArray
from mutascope.export.schemas import BundleFidelitySchema

LEVELS = (80, 90)


@dataclass(frozen=True)
class Tolerances:
    """Largest allowed change from rounding (``export.fidelity``)."""

    spearman: float
    coverage: float

    def of(self, metric: str) -> float:
        return self.spearman if metric == "spearman" else self.coverage


class FidelityError(ValueError):
    """A recomputed number moved by more than its tolerance."""


def assay_values(
    grids: Mapping[str, FloatArray], *, zero_shot: Sequence[str], supervised: str | None
) -> dict[tuple[str, str], float]:
    """``(model, metric) -> value`` for one assay's grids (source or decoded).

    Args:
        grids: ``measured``, ``zero_shot.<model>``, and with a supervised landscape
            ``prediction``, ``lower_<level>``, ``upper_<level>``.
        zero_shot: Zero-shot model names to score (those present in ``grids``).
        supervised: The supervised model's name, or None without a landscape.
    """
    measured = grids["measured"]
    mask = np.isfinite(measured)
    y = measured[mask]
    out: dict[tuple[str, str], float] = {}
    for model in zero_shot:
        key = f"zero_shot.{model}"
        if key in grids:
            out[(model, "spearman")] = spearman(y, grids[key][mask])
    if supervised is not None:
        out[(supervised, "spearman")] = spearman(y, grids["prediction"][mask])
        for level in LEVELS:
            lower, upper = grids[f"lower_{level}"][mask], grids[f"upper_{level}"][mask]
            out[(supervised, f"coverage_{level}")] = float(((y >= lower) & (y <= upper)).mean())
    return out


def fidelity_table(
    source: Mapping[str, Mapping[tuple[str, str], float]],
    encoded: Mapping[str, Mapping[tuple[str, str], float]],
    reported: Mapping[tuple[str, str, str], float],
    *,
    reference: pd.DataFrame,
    tolerances: Tolerances,
) -> pd.DataFrame:
    """Per-assay and aggregate rows of :class:`BundleFidelitySchema`.

    Args:
        source: Assay -> :func:`assay_values` of the unrounded grids.
        encoded: Assay -> :func:`assay_values` of the decoded protein file.
        reported: ``(assay_id, model, metric)`` -> the results table's value, where comparable.
        reference: ProteinGym reference (``DMS_id``, ``UniProt_ID``, ``coarse_selection_type``),
            for the Spearman aggregate.
        tolerances: Allowed differences.

    Raises:
        ValueError: ``source`` and ``encoded`` do not cover the same assays and quantities.
    """
    if {a: set(v) for a, v in source.items()} != {a: set(v) for a, v in encoded.items()}:
        raise ValueError("source and encoded values cover different assays or quantities")
    rows: list[dict[str, object]] = []
    for assay_id in sorted(source):
        for (model, metric), value in sorted(source[assay_id].items()):
            rows.append(
                _row(
                    assay_id,
                    model,
                    metric,
                    1,
                    value,
                    encoded[assay_id][(model, metric)],
                    reported.get((assay_id, model, metric)),
                    tolerances.of(metric),
                )
            )
    per_assay = pd.DataFrame.from_records(rows)
    for (model, metric), group in per_assay.groupby(["model", "metric"], sort=True):
        assays = group["assay_id"].astype(str).tolist()
        complete = bool(group["reported"].notna().all())
        if metric == "spearman":
            groups = AssayGroups.from_reference(reference, assays)
            values = [
                aggregate(group[c].to_numpy(np.float64), groups).overall
                for c in ("source", "encoded", "reported")
            ]
        else:
            values = [float(group[c].mean()) for c in ("source", "encoded", "reported")]
        rows.append(
            _row(
                None,
                str(model),
                str(metric),
                len(assays),
                values[0],
                values[1],
                values[2] if complete else None,
                tolerances.of(str(metric)),
                level="aggregate",
            )
        )
    table = pd.DataFrame.from_records(rows)
    # All-null columns (no comparable reported value) would otherwise be object dtype.
    numbers = ("source", "encoded", "reported", "diff_source", "diff_reported")
    table = table.astype(dict.fromkeys(numbers, "float64"))
    return BundleFidelitySchema.validate(table, lazy=True)


def _row(
    assay_id: str | None,
    model: str,
    metric: str,
    n_assays: int,
    source: float,
    encoded: float,
    reported: float | None,
    tolerance: float,
    *,
    level: str = "assay",
) -> dict[str, object]:
    diff_source = _diff(encoded, source)
    diff_reported = None if reported is None else _diff(encoded, reported)
    passed = diff_source <= tolerance and (diff_reported is None or diff_reported <= tolerance)
    return {
        "assay_id": assay_id,
        "model": model,
        "metric": metric,
        "level": level,
        "n_assays": n_assays,
        "source": source,
        "encoded": encoded,
        "reported": reported,
        "diff_source": diff_source,
        "diff_reported": diff_reported,
        "tolerance": tolerance,
        "passed": passed,
    }


def _diff(a: float, b: float) -> float:
    """``|a - b|``; 0 when both are NaN (undefined both ways), infinite when one is."""
    if math.isnan(a) and math.isnan(b):
        return 0.0
    if math.isnan(a) or math.isnan(b):
        return math.inf
    return abs(a - b)


def check_fidelity(table: pd.DataFrame) -> None:
    """Raise unless every row passed.

    Raises:
        FidelityError: Lists the rows that did not.
    """
    failed = table[~table["passed"]]
    if len(failed):
        listed = "; ".join(
            f"{r.assay_id or 'aggregate'} {r.model} {r.metric}: encoded {r.encoded:.6f}, "
            f"source {r.source:.6f}, reported {r.reported}"
            for r in failed.head(10).itertuples()
        )
        raise FidelityError(f"{len(failed)} numbers moved beyond tolerance: {listed}")
