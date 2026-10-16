"""Global feature importance from SHAP values (ADR 0022).

A feature's importance is the mean absolute SHAP value over the variants it is computed on.
A block's importance (all 1,280 columns of an embedding, the 20 log-probabilities, the three
structure columns...) is the mean absolute value of the *sum* of its columns' SHAP values: the
block's combined push on each prediction. Summing per-column importances instead would rank a
block by how many columns it has, since 1,280 small contributions of either sign add up in
absolute value even when they cancel.
"""

import re
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd

from mutascope.eval.schemas import ShapImportanceSchema
from mutascope.features.base import KEY_COLUMNS
from mutascope.features.matrix import FeatureMatrix, FloatMatrix
from mutascope.features.store import STRUCTURE_COLUMNS
from mutascope.models.cv import DesignBuilder, ModelFactory

_INDEXED = re.compile(r"_(?:pc)?\d+$")


@runtime_checkable
class Explainable(Protocol):
    """A fitted model that gives each row's SHAP values (LightGBM: TreeSHAP)."""

    def contributions(self, x: FeatureMatrix) -> tuple[FloatMatrix, tuple[str, ...]]: ...


def feature_block(name: str) -> str:
    """The feature-set block a column belongs to (``esm2_650m:embedding_17`` -> ``esm2_650m:
    embedding``; ``onehot_wt_A`` -> ``onehot``; ``plddt`` -> ``structure``)."""
    if ":" in name:
        model, _, kind = name.partition(":")
        return f"{model}:{_INDEXED.sub('', kind)}"
    if name.startswith("onehot_"):
        return "onehot"
    if name in STRUCTURE_COLUMNS:
        return "structure"
    return name


def shap_importance(
    values: FloatMatrix,
    names: Sequence[str],
    *,
    model: str,
    feature_set: str,
    assay_id: str,
) -> pd.DataFrame:
    """Feature- and block-level importance (:class:`ShapImportanceSchema`) of one fit.

    Args:
        values: SHAP values, one row per variant and one column per feature (no bias column).
        names: Column names, as the model saw them (principal components under PCA).
    """
    if values.ndim != 2 or values.shape[1] != len(names) or values.shape[0] == 0:
        raise ValueError(f"SHAP values of shape {values.shape} for {len(names)} names")
    blocks = [feature_block(n) for n in names]
    per_feature = np.abs(values).mean(axis=0)
    order = list(dict.fromkeys(blocks))
    per_block = np.array(
        [np.abs(values[:, [b == block for b in blocks]].sum(axis=1)).mean() for block in order]
    )
    frames = [
        pd.DataFrame(
            {"level": "feature", "name": list(names), "block": blocks, "value": per_feature}
        ),
        pd.DataFrame({"level": "block", "name": order, "block": order, "value": per_block}),
    ]
    for frame in frames:
        total = float(frame["value"].sum())
        frame["share"] = frame["value"] / total if total > 0 else 0.0
    table = pd.concat(frames, ignore_index=True)
    table.insert(0, "model", model)
    table.insert(1, "feature_set", feature_set)
    table.insert(2, "assay_id", assay_id)
    table.insert(3, "n_variants", int(values.shape[0]))
    return ShapImportanceSchema.validate(table, lazy=True)


def shap_assay(
    assay_id: str,
    assay: pd.DataFrame,
    design: DesignBuilder,
    make_model: ModelFactory,
    scheme: str,
    model: str,
    feature_set: str,
) -> pd.DataFrame:
    """Fit a model on all of one assay's variants and summarise its SHAP values.

    ``DMS_score`` is standardised over all variants (the transform the CV loop applies with
    training statistics). The model's own search runs over the folds of ``scheme`` (``groups``),
    then it is refit on every variant and explained on every variant.

    Raises:
        ValueError: The model gives no SHAP values (:class:`Explainable`), or the targets are
            constant.
    """
    x = design(assay_id, assay[list(KEY_COLUMNS)].reset_index(drop=True))
    raw = assay["DMS_score"].to_numpy(np.float64)
    if not np.std(raw) > 0:
        raise ValueError(f"{assay_id}: DMS_score is constant")
    y = (raw - raw.mean()) / raw.std()
    fitted = make_model()
    fitted.fit(x, y, assay[f"fold_{scheme}"].to_numpy(np.int64))
    if not isinstance(fitted, Explainable):
        raise ValueError(f"{model} gives no SHAP values")
    values, names = fitted.contributions(x)
    return shap_importance(values, names, model=model, feature_set=feature_set, assay_id=assay_id)
