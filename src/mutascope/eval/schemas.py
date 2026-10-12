"""pandera schemas for benchmark results (``results/*.parquet``) and zero-shot scores.

Per-assay results use the long format every later figure reads:
``model, assay_id, split, fold, metric, value``. Zero-shot results have no split or fold, so
both are null; supervised results (Phase 3) fill them. A NaN ``value`` is an undefined metric
(e.g. AUC when every label is 1), never a missing computation.
"""

import pandas as pd
import pandera.pandas as pa
from pandera.typing.pandas import Series

from mutascope.data.parse import SINGLE_MUTATION_PATTERN
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


class ComparisonSchema(pa.DataFrameModel):
    """Paired bootstrap comparison of two score sets on one aggregate (``A - B``; ADR 0014).

    ``value_a`` and ``value_b`` are the ProteinGym aggregates over the ``n_assays`` assays
    defined for both, ``diff`` their difference, ``ci_low`` / ``ci_high`` its percentile CI
    and ``share_a_better`` the share of resamples with ``A > B``.
    """

    model_a: Series[str]
    model_b: Series[str]
    split: Series[pd.StringDtype] = pa.Field(isin=list(SPLITS), nullable=True)
    metric: Series[str] = pa.Field(isin=list(METRICS))
    level: Series[str] = pa.Field(isin=list(LEVELS))
    group: Series[pd.StringDtype] = pa.Field(isin=list(FUNCTION_TYPES), nullable=True)
    value_a: Series[float] = pa.Field(nullable=True)
    value_b: Series[float] = pa.Field(nullable=True)
    diff: Series[float] = pa.Field(nullable=True)
    ci_low: Series[float] = pa.Field(nullable=True)
    ci_high: Series[float] = pa.Field(nullable=True)
    share_a_better: Series[float] = pa.Field(ge=0, le=1, nullable=True)
    n_assays: Series[int] = pa.Field(ge=0)

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True
        unique = ["model_a", "model_b", "split", "metric", "level", "group"]  # noqa: RUF012 — read by pandera


class ZeroShotScoreSchema(pa.DataFrameModel):
    """One model's zero-shot score of one single mutant (``processed/zero_shot/<model>/``).

    Higher = predicted fitter, the direction of ``DMS_score``. Every variant of an assay has a
    finite score: a variant the model could not score fails the run, it is never left null.
    """

    assay_id: Series[str]
    mutant: Series[str] = pa.Field(str_matches=rf"(?:{SINGLE_MUTATION_PATTERN})\Z")
    model: Series[str]
    score: Series[float]

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True
        unique = ["assay_id", "mutant"]  # noqa: RUF012 — read by pandera

    @pa.check("score")  # pyright: ignore[reportUnknownMemberType] — untyped in pandera
    @classmethod
    def score_is_finite(cls, score: "pd.Series[float]") -> "pd.Series[bool]":
        """No NaN (a position that was not scored) and no infinity."""
        return score.abs() < float("inf")


class ScoringRunSchema(pa.DataFrameModel):
    """What one ``mutascope score zero-shot`` invocation did for one assay.

    ``positions`` are the distinct positions the assay measures; ``positions_computed`` those
    that went through the model in this invocation (0 when the cache had them all, as in a
    resumed run). ``seconds`` covers this invocation only.
    """

    assay_id: Series[str] = pa.Field(unique=True)
    model: Series[str]
    method: Series[str] = pa.Field(isin=["masked", "wt"])
    region: Series[str]
    region_length: Series[int] = pa.Field(ge=1)
    variants: Series[int] = pa.Field(ge=1)
    positions: Series[int] = pa.Field(ge=1)
    positions_computed: Series[int] = pa.Field(ge=0)
    seconds: Series[float] = pa.Field(ge=0)

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True


class AgreementSchema(pa.DataFrameModel):
    """Model gate, one assay: our scores against ProteinGym's on the same single mutants.

    ``spearman`` and ``max_abs_diff`` compare the two score columns with each other;
    ``spearman_dms_*`` compare each with ``DMS_score`` (the inputs to the aggregate check).
    """

    assay_id: Series[str] = pa.Field(unique=True)
    variants: Series[int] = pa.Field(ge=1)
    spearman: Series[float] = pa.Field(nullable=True)
    max_abs_diff: Series[float] = pa.Field(ge=0)
    spearman_dms_ours: Series[float] = pa.Field(nullable=True)
    spearman_dms_theirs: Series[float] = pa.Field(nullable=True)
    passed: Series[bool]

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True
