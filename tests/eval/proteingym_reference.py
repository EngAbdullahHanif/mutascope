"""ProteinGym's own metric and aggregation code, used as a test oracle.

Copied from ``proteingym/performance_DMS_benchmarks.py`` at the pinned commit
``1f8de974dead8ff7501eff087b725d14a965e9f9`` (MIT licence, Copyright (c) 2023 OATML-Markslab).
``calc_ndcg`` and ``calc_toprecall`` are verbatim apart from typing, formatting and the unused
``quantile=False`` branch; ``proteingym_aggregate`` is their group-by chain (lines 297-309)
reduced to one metric column. Nothing here is imported by ``src/``.
"""

from typing import Any

import numpy as np
import pandas as pd


def minmax(x: Any) -> Any:
    return (x - np.min(x)) / (np.max(x) - np.min(x))


def calc_ndcg(y_true: Any, y_score: Any, top: float = 10) -> float:
    k = np.floor(y_true.shape[0] * (top / 100)).astype(int)
    if isinstance(y_true, pd.Series):
        y_true = y_true.values
    if isinstance(y_score, pd.Series):
        y_score = y_score.values
    gains = minmax(y_true)
    ranks = np.argsort(np.argsort(-y_score)) + 1

    ranks_k = ranks[ranks <= k]
    gains_k = gains[ranks <= k]
    ranks_fil = ranks_k[gains_k != 0]
    gains_fil = gains_k[gains_k != 0]

    if len(ranks_fil) == 0:
        return 0

    dcg = np.sum([g / np.log2(r + 1) for r, g in zip(ranks_fil, gains_fil, strict=True)])

    ideal_ranks = np.argsort(np.argsort(-gains)) + 1
    ideal_ranks_k = ideal_ranks[ideal_ranks <= k]
    ideal_gains_k = gains[ideal_ranks <= k]
    ideal_ranks_fil = ideal_ranks_k[ideal_gains_k != 0]
    ideal_gains_fil = ideal_gains_k[ideal_gains_k != 0]
    idcg = np.sum(
        [g / np.log2(r + 1) for r, g in zip(ideal_ranks_fil, ideal_gains_fil, strict=True)]
    )
    return float(dcg / idcg)


def calc_toprecall(
    true_scores: Any, model_scores: Any, top_true: float = 10, top_model: float = 10
) -> float:
    top_true_mask = true_scores >= np.percentile(true_scores, 100 - top_true)
    top_model_mask = model_scores >= np.percentile(model_scores, 100 - top_model)
    tp = top_true_mask & top_model_mask
    return float(tp.sum() / top_true_mask.sum()) if top_true_mask.sum() > 0 else 0


def proteingym_aggregate(per_assay: pd.DataFrame) -> tuple[float, dict[str, float]]:
    """Overall and per-function value from columns ``UniProt_ID, Selection Type, value``."""
    by_uniprot_function = per_assay.groupby(["UniProt_ID", "Selection Type"]).mean(
        numeric_only=True
    )
    by_function = by_uniprot_function.groupby("Selection Type").mean(numeric_only=True)
    overall = by_function.reset_index().mean(numeric_only=True)
    return float(overall["value"]), {str(k): float(v) for k, v in by_function["value"].items()}
