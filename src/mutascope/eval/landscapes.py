"""Full landscapes: masked marginals at every residue, not only where an assay measured.

The benchmark scores the positions an assay measures (ADR 0013). The website shows every
substitution of a protein, so every residue needs a score. This module fills the rest of the
same cache entries and says which landscapes exist (ADR 0018).

Which landscapes an assay has:

- its **benchmark region** (:mod:`mutascope.eval.region`): the full target for most assays, the
  MSA region for five (sequence-only models), the residues the structure models for three
  (structure-aware models);
- its **full target**, when the benchmark region is not already that. For a structure-aware
  model the residues outside the structure have the 3Di letter ``#``.

The two are separate cache entries with different region labels (``"msa:281-804"`` against
``"full"``), and their numbers differ: a residue is scored in a different context. A table row
says which one it is (``benchmark``, ``full_target``; both are true when they are the same
landscape).

Resuming: the missing residues of an entry are scored a slice at a time, and every slice is
one scorer call, which writes the cache entry before it returns. A run killed at any moment
therefore loses at most the slice in progress, also in the middle of a 3,000-residue protein.
Rows that are already in the cache are never computed again, so the benchmark's scores cannot
change.
"""

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from mutascope.eval.region import ScoringRegion
from mutascope.eval.schemas import LandscapeCoverageSchema, LandscapeRunSchema
from mutascope.scoring.base import FloatArray, Scorer
from mutascope.scoring.cache import CacheKey, LogProbCache
from mutascope.scoring.struct_tokens import AssayStructure
from mutascope.utils.logging import get_logger

log = get_logger(__name__)

SPECIAL_TOKENS = 2
"""``<cls>`` and ``<eos>``, which every model pass carries besides the residues."""


@dataclass(frozen=True)
class LandscapeTarget:
    """One landscape of one assay: a region of its target, and what the model is given for it."""

    assay_id: str
    region: ScoringRegion
    sequence: str
    """The region's residues."""
    struct_tokens: str | None
    """The region's structure tokens (structure-aware models), else None."""
    benchmark: bool
    """The region is the assay's benchmark scoring region."""

    @property
    def full_target(self) -> bool:
        return self.region.is_full

    @property
    def entry(self) -> tuple[str, str | None, str]:
        """What identifies the cache entry of one model: assays of one protein share it."""
        return (self.sequence, self.struct_tokens, self.region.label)


def landscape_targets(
    reference: pd.DataFrame,
    benchmark_regions: Mapping[str, ScoringRegion],
    structures: Mapping[str, AssayStructure] | None = None,
) -> list[LandscapeTarget]:
    """Every landscape of every assay that has a benchmark region, in reference order.

    Args:
        reference: ProteinGym reference table (``DMS_id``, ``target_seq``).
        benchmark_regions: The model's benchmark region per assay
            (:func:`~mutascope.eval.region.scoring_regions` or
            :func:`~mutascope.eval.region.structure_regions`). An assay without one (no
            structure, for a structure-aware model) has no landscape.
        structures: Structure tokens per assay, for a structure-aware model.

    Returns:
        The benchmark-region landscape of each assay, followed by its full-target landscape
        where the benchmark region is only part of the target.
    """
    targets: list[LandscapeTarget] = []
    for assay_id, target_seq in zip(
        reference["DMS_id"].astype(str), reference["target_seq"].astype(str), strict=True
    ):
        if assay_id not in benchmark_regions:
            continue
        tokens = None if structures is None else structures[assay_id].tokens
        benchmark = benchmark_regions[assay_id]
        regions = [benchmark]
        if not benchmark.is_full:
            regions.append(ScoringRegion(assay_id, 0, len(target_seq), len(target_seq)))
        for region in regions:
            targets.append(
                LandscapeTarget(
                    assay_id=assay_id,
                    region=region,
                    sequence=region.sequence(target_seq),
                    struct_tokens=None if tokens is None else tokens[region.start : region.end],
                    benchmark=region is benchmark,
                )
            )
    return targets


def pass_tokens(length: int, window_tokens: int | None) -> int:
    """Tokens in one masked pass over a ``length``-residue sequence: the unit of model work.

    The whole sequence with its two special tokens, or one window if the model is windowed and
    the sequence is longer. Model time per pass is close to proportional to this (0.24 to
    0.33 ms per token for ESM-2 650M on the benchmark run), which is what the time estimate
    relies on.
    """
    tokens = length + SPECIAL_TOKENS
    return tokens if window_tokens is None else min(tokens, window_tokens)


def format_duration(seconds: float) -> str:
    """``"2h 05m"``, ``"7m 30s"`` or ``"42s"``."""
    whole = round(seconds)
    hours, minutes, secs = whole // 3600, whole % 3600 // 60, whole % 60
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def fill_landscapes(
    scorer: Scorer,
    targets: Sequence[LandscapeTarget],
    *,
    window_tokens: int | None,
    checkpoint_tokens: int,
) -> pd.DataFrame:
    """Score every residue of every landscape in ``targets`` that the scorer's cache lacks.

    Progress is logged after every slice (``landscape_progress``): positions done, share of
    the work, elapsed time and the estimated time remaining. The estimate assumes model time
    proportional to the tokens run (:func:`pass_tokens`), measured on this invocation alone.

    Args:
        scorer: The model; its cache is where the landscapes live and what makes the run
            resumable (see the module docstring).
        targets: :func:`landscape_targets` output.
        window_tokens: The model's ``window_tokens`` (None: never windowed).
        checkpoint_tokens: Model work per slice, in tokens; the most an interruption can lose.

    Returns:
        One row per landscape (:class:`LandscapeRunSchema`): what this invocation computed.
        ``positions_computed`` is 0 for a landscape that was complete, or that shares its
        cache entry with an earlier row.

    Raises:
        RuntimeError: A landscape still has an unscored residue after its last slice.
    """
    if checkpoint_tokens < 1:
        raise ValueError(f"checkpoint_tokens must be at least 1, got {checkpoint_tokens}")
    pending: dict[tuple[str, str | None, str], list[int]] = {}
    for target in targets:
        if target.entry not in pending:
            pending[target.entry] = _unscored(scorer, target)
    work = {
        entry: len(missing) * pass_tokens(len(entry[0]), window_tokens)
        for entry, missing in pending.items()
    }
    total_positions = sum(len(missing) for missing in pending.values())
    total_work, done_positions, done_work = sum(work.values()), 0, 0
    log.info(
        "landscape_plan",
        model=scorer.model_id,
        landscapes=len(targets),
        cache_entries=len(pending),
        residues=sum(len(entry[0]) for entry in pending),
        positions_missing=total_positions,
    )
    started = time.monotonic()
    rows: list[dict[str, object]] = []
    for index, target in enumerate(targets, start=1):
        missing = pending.pop(target.entry, [])
        tokens = pass_tokens(len(target.sequence), window_tokens)
        before, target_started = scorer.positions_computed, time.monotonic()
        for chunk in _slices(missing, max(1, checkpoint_tokens // tokens)):
            _score(scorer, target, chunk)
            done_positions += len(chunk)
            done_work += len(chunk) * tokens
            elapsed = time.monotonic() - started
            remaining = elapsed * (total_work - done_work) / done_work
            log.info(
                "landscape_progress",
                model=scorer.model_id,
                assay_id=target.assay_id,
                region=target.region.label,
                landscape=f"{index}/{len(targets)}",
                positions=f"{done_positions}/{total_positions}",
                work_done=f"{done_work / total_work:.1%}",
                elapsed=format_duration(elapsed),
                remaining=format_duration(remaining),
            )
        if _unscored(scorer, target):
            raise RuntimeError(
                f"{target.assay_id} ({target.region.label}): {scorer.model_id} left residues "
                "unscored"
            )
        rows.append(
            _describe(target, scorer.model_id)
            | {
                "positions_computed": scorer.positions_computed - before,
                "seconds": time.monotonic() - target_started,
            }
        )
    return LandscapeRunSchema.validate(pd.DataFrame.from_records(rows), lazy=True)


def landscape_coverage(
    cache: LogProbCache,
    targets: Sequence[LandscapeTarget],
    *,
    model: str,
    hf_id: str,
    revision: str,
) -> pd.DataFrame:
    """How much of every landscape in ``targets`` the cache holds for one model. No model runs.

    Args:
        cache: The log-probability cache.
        targets: :func:`landscape_targets` output for this model.
        model: The model's name (the ``model`` column).
        hf_id: Its Hub id, as in its config; with ``revision`` it is the model part of the
            cache key.
        revision: Its pinned revision.

    Returns:
        One row per landscape (:class:`LandscapeCoverageSchema`). ``positions_scored`` counts
        the residues with all 20 values finite; ``complete`` means all of them. A missing
        cache entry is a row with 0 positions, not an error.

    Raises:
        CacheError: An entry does not have the ``(L, 20)`` shape of its sequence.
    """
    rows: list[dict[str, object]] = []
    for target in targets:
        key = CacheKey(
            model_id=hf_id,
            revision=revision,
            sequence=target.sequence,
            struct_tokens=target.struct_tokens,
            method="masked",
            region=target.region.label,
        )
        entry = cache.get(key)
        scored = 0 if entry is None else int(np.isfinite(entry).all(axis=1).sum())
        rows.append(
            _describe(target, model)
            | {"positions_scored": scored, "complete": scored == len(target.sequence)}
        )
    return LandscapeCoverageSchema.validate(pd.DataFrame.from_records(rows), lazy=True)


def _describe(target: LandscapeTarget, model: str) -> dict[str, object]:
    """The columns that identify a landscape in both tables."""
    return {
        "assay_id": target.assay_id,
        "model": model,
        "region": target.region.label,
        "region_length": len(target.sequence),
        "benchmark": target.benchmark,
        "full_target": target.full_target,
    }


def _score(scorer: Scorer, target: LandscapeTarget, positions: Sequence[int]) -> FloatArray:
    return scorer.landscape(
        target.sequence, target.struct_tokens, region=target.region.label, positions=positions
    )


def _unscored(scorer: Scorer, target: LandscapeTarget) -> list[int]:
    """Residues of ``target`` with no score yet. An empty request runs no model."""
    landscape = _score(scorer, target, [])
    return [int(p) for p in np.flatnonzero(~np.isfinite(landscape).all(axis=1))]


def _slices(items: Sequence[int], size: int) -> list[Sequence[int]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
