"""The model gate (ADR 0010): our zero-shot scores against ProteinGym's, on single mutants.

Two checks, both required:

- **Per variant.** In every assay, the Spearman between our scores and ProteinGym's precomputed
  scores for the same model, on the same variants, is at least ``min_assay_spearman``. This
  catches a wrong masking scheme, token offset or window, which an aggregate could hide.
- **Aggregate.** Our ProteinGym-style aggregate Spearman against ``DMS_score`` is within
  ``aggregate_tolerance`` of the aggregate the same harness computes from their scores on the
  same rows. It is never compared with the published leaderboard value, which includes
  multi-mutants.

An assay whose agreement is undefined (constant scores) fails: an unknown is not a pass.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from mutascope.eval.aggregate import AssayGroups, aggregate
from mutascope.eval.metrics import spearman
from mutascope.eval.schemas import AgreementSchema

OURS, THEIRS = "ours", "theirs"


@dataclass(frozen=True)
class GateThresholds:
    """``eval.model_gate`` in the config; changing either value needs a new ADR."""

    min_assay_spearman: float
    aggregate_tolerance: float


@dataclass(frozen=True)
class ModelGate:
    """Outcome of the model gate."""

    per_assay: pd.DataFrame
    """:class:`AgreementSchema`, one row per assay."""
    ours: float
    """Our aggregate Spearman against ``DMS_score`` (single mutants)."""
    theirs: float
    """The same aggregate from ProteinGym's precomputed scores on the same rows."""
    thresholds: GateThresholds

    @property
    def failing(self) -> pd.DataFrame:
        """Assays below the per-variant threshold (or undefined), worst first."""
        return self.per_assay[~self.per_assay["passed"]].sort_values("spearman")

    @property
    def aggregate_delta(self) -> float:
        return self.ours - self.theirs

    @property
    def passed(self) -> bool:
        within = abs(self.aggregate_delta) <= self.thresholds.aggregate_tolerance
        return self.failing.empty and within


def join_scores(ours: pd.DataFrame, theirs: pd.DataFrame, column: str) -> pd.DataFrame:
    """Pair our score with ProteinGym's for every variant.

    Args:
        ours: ``assay_id, mutant, score`` (:func:`~mutascope.eval.zero_shot.load_scores`).
        theirs: ``assay_id, mutant, DMS_score, DMS_score_bin`` and ``column``
            (:func:`~mutascope.eval.proteingym.load_precomputed_scores`, single mutants).
        column: ProteinGym's score column for the same model, e.g. ``ESM2_650M``.

    Returns:
        ``assay_id, mutant, DMS_score, DMS_score_bin, ours, theirs``, sorted.

    Raises:
        ValueError: The two tables do not hold exactly the same variants. Nothing is dropped
            to make them match.
    """
    mine = ours[["assay_id", "mutant", "score"]].rename(columns={"score": OURS})
    ref = theirs[["assay_id", "mutant", "DMS_score", "DMS_score_bin", column]].rename(
        columns={column: THEIRS}
    )
    joined = ref.merge(mine, on=["assay_id", "mutant"], how="outer", indicator=True)
    unmatched = joined[joined["_merge"] != "both"]
    if len(unmatched) or len(joined) != len(mine) or len(joined) != len(ref):
        sides = unmatched["_merge"].astype(str).value_counts().to_dict()
        raise ValueError(
            f"score tables differ: {len(mine)} of ours, {len(ref)} of ProteinGym's, "
            f"unmatched {sides} (left = ProteinGym only)"
        )
    columns = ["assay_id", "mutant", "DMS_score", "DMS_score_bin", OURS, THEIRS]
    return joined[columns].sort_values(["assay_id", "mutant"], ignore_index=True)


def model_gate(
    joined: pd.DataFrame, reference: pd.DataFrame, thresholds: GateThresholds
) -> ModelGate:
    """Run both checks on :func:`join_scores` output.

    Args:
        joined: Paired scores with the assay labels.
        reference: ProteinGym reference table (``DMS_id, UniProt_ID, coarse_selection_type``).
        thresholds: The gate's two limits.
    """
    records: list[dict[str, object]] = []
    for assay_id, rows in joined.groupby("assay_id", sort=True):
        ours = rows[OURS].to_numpy(np.float64)
        theirs = rows[THEIRS].to_numpy(np.float64)
        dms = rows["DMS_score"].to_numpy(np.float64)
        rho = spearman(theirs, ours)
        records.append(
            {
                "assay_id": str(assay_id),
                "variants": len(rows),
                "spearman": rho,
                "max_abs_diff": float(np.abs(ours - theirs).max()),
                "spearman_dms_ours": spearman(dms, ours),
                "spearman_dms_theirs": spearman(dms, theirs),
                "passed": bool(rho >= thresholds.min_assay_spearman),  # NaN compares False
            }
        )
    per_assay = AgreementSchema.validate(pd.DataFrame.from_records(records), lazy=True)
    groups = AssayGroups.from_reference(reference, per_assay["assay_id"].tolist())
    return ModelGate(
        per_assay=per_assay,
        ours=aggregate(per_assay["spearman_dms_ours"].to_numpy(np.float64), groups).overall,
        theirs=aggregate(per_assay["spearman_dms_theirs"].to_numpy(np.float64), groups).overall,
        thresholds=thresholds,
    )
