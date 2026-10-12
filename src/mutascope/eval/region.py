"""Which part of an assay's target sequence a model scores: the benchmark scoring region.

ProteinGym's published zero-shot scores for five assays were computed on the region their MSA
covers (``MSA_start``..``MSA_end`` in the reference file), not on the full target, although
the scoring script at the pinned commit crops only for MSA Transformer (ADR 0012). Scoring the
full target changes those scores (``KCNH2_HUMAN_Kozek_2020``: Spearman 0.36 against theirs), so
benchmark numbers would not be comparable with the leaderboard.

Two modes:

- ``benchmark`` (the model gate, zero-shot results, Phase 3 features): the MSA region wherever it
  is not the full target, the full target otherwise.
- ``landscape`` (website landscapes): the full target for every assay.

The set of MSA-region assays is derived from the reference file and must equal the list in
``eval.scoring_region.msa_region_assays``; any difference fails, the same pattern as
``data.structure_exceptions``. A reference update that changes the set therefore cannot change
benchmark numbers silently.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import pandas as pd

from mutascope.scoring.base import FloatArray, Scorer, score_mutants

Mode = Literal["benchmark", "landscape"]
MODES: tuple[Mode, ...] = ("benchmark", "landscape")


class ScoringRegionError(ValueError):
    """The derived MSA-region set differs from the config, or a region is malformed."""


@dataclass(frozen=True)
class ScoringRegion:
    """Residues ``[start, end)`` (0-based) of an assay's target sequence."""

    assay_id: str
    start: int
    end: int
    target_length: int

    def __post_init__(self) -> None:
        if not 0 <= self.start < self.end <= self.target_length:
            raise ScoringRegionError(
                f"{self.assay_id}: region [{self.start}, {self.end}) outside a "
                f"{self.target_length}-residue target"
            )

    @property
    def is_full(self) -> bool:
        return self.start == 0 and self.end == self.target_length

    @property
    def label(self) -> str:
        """``"full"`` or ``"msa:<first>-<last>"`` (1-based, inclusive, as in the reference)."""
        return "full" if self.is_full else f"msa:{self.start + 1}-{self.end}"

    def sequence(self, target_seq: str) -> str:
        """The part of ``target_seq`` to score."""
        if len(target_seq) != self.target_length:
            raise ScoringRegionError(
                f"{self.assay_id}: target has {len(target_seq)} residues, "
                f"region expects {self.target_length}"
            )
        return target_seq[self.start : self.end]


def derive_msa_regions(reference: pd.DataFrame) -> dict[str, ScoringRegion]:
    """Assays whose MSA does not cover the full target, with that MSA region.

    Args:
        reference: ProteinGym reference table (``DMS_id``, ``target_seq``, ``MSA_start``,
            ``MSA_end``; MSA bounds 1-based inclusive).
    """
    regions: dict[str, ScoringRegion] = {}
    for assay_id, length, first, last in zip(
        reference["DMS_id"].astype(str),
        reference["target_seq"].astype(str).str.len(),
        reference["MSA_start"].astype(int),
        reference["MSA_end"].astype(int),
        strict=True,
    ):
        region = ScoringRegion(assay_id, first - 1, last, length)
        if not region.is_full:
            regions[assay_id] = region
    return regions


def check_msa_regions(derived: Mapping[str, ScoringRegion], expected: Sequence[str]) -> None:
    """Fail unless the derived MSA-region assays are exactly the configured ones."""
    missing = sorted(set(derived) - set(expected))
    stale = sorted(set(expected) - set(derived))
    if missing or stale:
        problems: list[str] = []
        if missing:
            problems.append(f"MSA region but not in eval.scoring_region: {missing}")
        if stale:
            problems.append(f"in eval.scoring_region but MSA covers the full target: {stale}")
        raise ScoringRegionError("; ".join(problems))


def scoring_regions(
    reference: pd.DataFrame, mode: Mode, expected_msa_assays: Sequence[str]
) -> dict[str, ScoringRegion]:
    """The region to score for every assay in ``reference``, in ``mode``.

    The derived set is checked against the config in both modes, so a stale config fails even
    when only landscapes are being built.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    msa = derive_msa_regions(reference)
    check_msa_regions(msa, expected_msa_assays)
    regions: dict[str, ScoringRegion] = {}
    for assay_id, length in zip(
        reference["DMS_id"].astype(str),
        reference["target_seq"].astype(str).str.len(),
        strict=True,
    ):
        full = ScoringRegion(assay_id, 0, length, length)
        regions[assay_id] = msa[assay_id] if mode == "benchmark" and assay_id in msa else full
    return regions


def score_assay(
    scorer: Scorer,
    target_seq: str,
    region: ScoringRegion,
    mutants: Sequence[str],
    *,
    fast: bool = False,
) -> FloatArray:
    """Score an assay's single mutants (target coordinates) on ``region``.

    The landscape covers every position of the region; only ``mutants`` are looked up. A mutant
    outside the region fails loudly instead of being dropped.
    """
    seq = region.sequence(target_seq)
    landscape = scorer.landscape(seq, fast=fast, region=region.label)
    return score_mutants(landscape, seq, mutants, offset=region.start)
