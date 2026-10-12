"""Ensemble of zero-shot score sets: z-score within each assay, then the mean over members.

Two models' scores are not on one scale. Both are log-odds, but SaProt's spread within an
assay differs from ESM-2's, and each model's spread also changes from assay to assay. Averaging
raw scores would let the member with the wider spread decide the ranking. Standardising a
member's scores within an assay (subtract their mean, divide by their standard deviation)
keeps that member's ranking of the assay's variants and gives every member the same weight.

The ensemble works on scores that are already written (``processed/zero_shot/<label>/``); it
never runs a model, so it costs seconds. It exists only for the assays every member scored.
"""

from collections.abc import Mapping

import numpy as np
import pandas as pd

from mutascope.eval.schemas import ZeroShotScoreSchema

_KEYS = ["assay_id", "mutant"]


def zscore_mean(members: Mapping[str, pd.DataFrame], label: str) -> pd.DataFrame:
    """Mean over members of each member's within-assay z-score.

    The standard deviation is the population one (``ddof=0``) over the assay's variants. The
    choice of ``ddof`` rescales every member of an assay by the same factor, so it cannot
    change a ranking.

    Args:
        members: Score-set label -> its scores (:class:`ZeroShotScoreSchema`), all on exactly
            the same ``(assay_id, mutant)`` rows: restrict them to the assays all of them have
            (:func:`mutascope.eval.compare.shared_assays`) first.
        label: Name of the ensemble, written to the ``model`` column.

    Returns:
        :class:`ZeroShotScoreSchema` rows, sorted by assay and mutant.

    Raises:
        ValueError: Fewer than two members, members on different rows, or a member whose
            scores are constant within an assay (its z-score is undefined there).
    """
    if len(members) < 2:
        raise ValueError(f"an ensemble needs at least two members, got {sorted(members)}")
    columns: dict[str, pd.Series[float]] = {}
    index: pd.MultiIndex | None = None
    for member, scores in members.items():
        frame = scores.sort_values(_KEYS).set_index(_KEYS)
        if index is None:
            index = pd.MultiIndex.from_frame(frame.index.to_frame())
        elif not frame.index.equals(index):
            raise ValueError(
                f"{member} is not on the same variants as {next(iter(members))}: "
                f"{len(frame)} rows against {len(index)}"
            )
        by_assay = frame.groupby(level="assay_id", sort=False)["score"]
        std = by_assay.transform("std", ddof=0)
        constant = sorted(set(std.index[std == 0].get_level_values("assay_id")))
        if constant:
            raise ValueError(f"{member}: constant scores, no z-score, in {constant}")
        columns[member] = (frame["score"] - by_assay.transform("mean")) / std
    mean = pd.DataFrame(columns).mean(axis=1)
    out = mean.rename("score").reset_index().assign(model=label)
    out["score"] = out["score"].to_numpy(np.float64)
    return ZeroShotScoreSchema.validate(out[["assay_id", "mutant", "model", "score"]])
