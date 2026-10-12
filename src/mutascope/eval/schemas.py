"""pandera schemas for benchmark results (``results/*.parquet``).

Per-assay results use the long format every later figure reads:
``model, assay_id, split, fold, metric, value``. Zero-shot results have no split or fold, so
both are null; supervised results (Phase 3) fill them. A NaN ``value`` is an undefined metric
(e.g. AUC when every label is 1), never a missing computation.
"""

import pandas as pd
import pandera.pandas as pa
from pandera.typing.pandas import Series

from mutascope.data.schemas import FUNCTION_TYPES
from mutascope.eval.metrics import METRICS

SPLITS = ("random", "modulo", "contiguous")
"""ProteinGym's supervised CV schemes (``fold_random_5``, ``fold_modulo_5``, ...)."""

LEVELS = ("overall", "function")


class AssayMetricSchema(pa.DataFrameModel):
    """One metric of one model on one assay (and, for supervised models, one split and fold)."""

    model: Series[str]
    assay_id: Series[str]
    split: Series[pd.StringDtype] = pa.Field(isin=list(SPLITS), nullable=True)
    fold: Series[pd.Int64Dtype] = pa.Field(ge=0, nullable=True)
    metric: Series[str] = pa.Field(isin=list(METRICS))
    value: Series[float] = pa.Field(nullable=True)

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True
        unique = ["model", "assay_id", "split", "fold", "metric"]  # noqa: RUF012 — read by pandera


class SummarySchema(pa.DataFrameModel):
    """A ProteinGym-style aggregate with its bootstrap CI, overall or for one function type.

    ``n_assays`` counts the assays with a defined value that went into it.
    """

    model: Series[str]
    split: Series[pd.StringDtype] = pa.Field(isin=list(SPLITS), nullable=True)
    metric: Series[str] = pa.Field(isin=list(METRICS))
    level: Series[str] = pa.Field(isin=list(LEVELS))
    group: Series[pd.StringDtype] = pa.Field(isin=list(FUNCTION_TYPES), nullable=True)
    """Function type for ``level == "function"``; null for ``"overall"``."""
    value: Series[float] = pa.Field(nullable=True)
    ci_low: Series[float] = pa.Field(nullable=True)
    ci_high: Series[float] = pa.Field(nullable=True)
    n_assays: Series[int] = pa.Field(ge=0)

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True
        unique = ["model", "split", "metric", "level", "group"]  # noqa: RUF012 — read by pandera

    @pa.dataframe_check  # pyright: ignore[reportUnknownMemberType] — untyped in pandera
    @classmethod
    def group_matches_level(cls, df: pd.DataFrame) -> "pd.Series[bool]":
        """Only function-level rows name a function type."""
        return ((df["level"] == "function") == df["group"].notna()).astype(bool)

    @pa.dataframe_check  # pyright: ignore[reportUnknownMemberType] — untyped in pandera
    @classmethod
    def ci_ordered(cls, df: pd.DataFrame) -> "pd.Series[bool]":
        """``ci_low <= ci_high``, and both are defined exactly when ``value`` is.

        A percentile interval need not contain the point estimate (it can miss by rounding
        when every resample gives the same value), so that is not checked.
        """
        defined = df["value"].notna()
        both = df["ci_low"].notna() & df["ci_high"].notna()
        none = df["ci_low"].isna() & df["ci_high"].isna()
        ordered = df["ci_low"] <= df["ci_high"]
        return ((defined & both & ordered) | (~defined & none)).astype(bool)
