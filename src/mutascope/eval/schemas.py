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
from mutascope.data.schemas import CV_SCHEMES, FUNCTION_TYPES
from mutascope.eval.metrics import METRICS, SUPERVISED_METRICS

SPLITS = CV_SCHEMES
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


class SupervisedMetricSchema(pa.DataFrameModel):
    """One metric of one supervised model on one assay under one CV scheme (ADR 0019).

    ``fold`` null is the metric over the pooled out-of-fold predictions of every fold: the
    number ProteinGym reports and the one to aggregate. ``fold`` k is the same metric on test
    fold k alone, a diagnostic of spread across folds.
    """

    model: Series[str]
    feature_set: Series[str]
    assay_id: Series[str]
    scheme: Series[str] = pa.Field(isin=list(CV_SCHEMES))
    fold: Series[pd.Int64Dtype] = pa.Field(ge=0, nullable=True)
    metric: Series[str] = pa.Field(isin=list(SUPERVISED_METRICS))
    value: Series[float] = pa.Field(nullable=True)

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True
        unique = ["model", "feature_set", "assay_id", "scheme", "fold", "metric"]  # noqa: RUF012 — read by pandera


class SelectedHyperparameterSchema(pa.DataFrameModel):
    """A hyperparameter a supervised model chose by inner CV in one outer fold (ADR 0021)."""

    model: Series[str]
    feature_set: Series[str]
    assay_id: Series[str]
    scheme: Series[str] = pa.Field(isin=list(CV_SCHEMES))
    fold: Series[int] = pa.Field(ge=0)
    name: Series[str]
    value: Series[float]

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True
        unique = ["model", "feature_set", "assay_id", "scheme", "fold", "name"]  # noqa: RUF012 — read by pandera


class SupervisedLeaderboardSchema(pa.DataFrameModel):
    """Our aggregate pooled Spearman next to a published supervised baseline (ADR 0021).

    A sanity range, not a gate. ``assay_spearman`` ranks our per-assay values against theirs
    over the ``n_shared`` assays both define.
    """

    run: Series[str]
    published_model: Series[str]
    scheme: Series[str] = pa.Field(isin=list(CV_SCHEMES))
    ours: Series[float]
    published: Series[float]
    diff: Series[float]
    n_assays: Series[int] = pa.Field(ge=0)
    n_shared: Series[int] = pa.Field(ge=0)
    assay_spearman: Series[float] = pa.Field(nullable=True)

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True
        unique = ["run", "published_model", "scheme"]  # noqa: RUF012 — read by pandera


class CanarySchema(pa.DataFrameModel):
    """Label-permutation canary of one model on one real assay and scheme (ADR 0019 / 0021).

    ``observed`` is the pooled Spearman with true labels; ``permuted_mean`` and
    ``standard_error`` summarise ``n_permutations`` runs with shuffled training labels, and
    ``z`` is their ratio. ``passed``: ``|z| <= max_standard_errors``. ``informative``: the
    model learns something (``observed >= min_observed``), without which passing proves
    nothing.
    """

    model: Series[str]
    feature_set: Series[str]
    assay_id: Series[str]
    scheme: Series[str] = pa.Field(isin=list(CV_SCHEMES))
    observed: Series[float] = pa.Field(nullable=True)
    permuted_mean: Series[float] = pa.Field(nullable=True)
    standard_error: Series[float] = pa.Field(nullable=True)
    z: Series[float] = pa.Field(nullable=True)
    n_permutations: Series[int] = pa.Field(ge=2)
    passed: Series[bool]
    informative: Series[bool]

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True
        unique = ["model", "feature_set", "assay_id", "scheme"]  # noqa: RUF012 — read by pandera


class ShapImportanceSchema(pa.DataFrameModel):
    """Global SHAP importance of one model fit on all of one assay's variants (ADR 0022).

    ``level = feature``: ``value`` is the mean over variants of ``|SHAP|`` of one column.
    ``level = block``: the mean over variants of ``|sum of SHAP over the block's columns|``,
    the block's combined contribution (a 1,280-column embedding is one block). ``share`` is
    ``value`` over the sum of ``value`` at that level, per assay.
    """

    model: Series[str]
    feature_set: Series[str]
    assay_id: Series[str]
    n_variants: Series[int] = pa.Field(ge=1)
    level: Series[str] = pa.Field(isin=["feature", "block"])
    name: Series[str]
    block: Series[str]
    value: Series[float] = pa.Field(ge=0)
    share: Series[float] = pa.Field(ge=0, le=1)

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True
        unique = ["model", "feature_set", "assay_id", "level", "name"]  # noqa: RUF012 — read by pandera


_MODES = ["random", "scheme"]
_INTERVALS = ["absolute", "normalized"]


class UncertaintyMetricSchema(pa.DataFrameModel):
    """Per-assay prediction-interval metrics on the test folds (ADR 0024).

    ``mode`` (calibration folds), ``interval`` (type) and ``level`` (nominal coverage) are null
    where a metric does not depend on them: ``n_variants`` and ``prediction_spearman`` have
    none, ``spread_error_spearman`` only a mode. Metrics: :data:`mutascope.eval.uncertainty.
    UNCERTAINTY_METRICS`.
    """

    model: Series[str]
    feature_set: Series[str]
    assay_id: Series[str]
    scheme: Series[str] = pa.Field(isin=list(CV_SCHEMES))
    mode: Series[str] = pa.Field(isin=_MODES, nullable=True)
    interval: Series[str] = pa.Field(isin=_INTERVALS, nullable=True)
    level: Series[float] = pa.Field(gt=0, lt=1, nullable=True)
    metric: Series[str]
    value: Series[float] = pa.Field(nullable=True)

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True


class UncertaintySummarySchema(pa.DataFrameModel):
    """Statistics over assays of one per-assay interval metric (``train uncertainty``).

    ``size_bin`` is ``all`` or an assay-size bin (variants). ``statistic``: mean, median,
    quantiles, ``n_assays``, ``share_positive``; coverage adds ``n_below`` (assays more than
    ``train.uncertainty.below_points`` under the level) and ``aggregate`` (ProteinGym-style).
    """

    model: Series[str]
    feature_set: Series[str]
    scheme: Series[str] = pa.Field(isin=list(CV_SCHEMES))
    mode: Series[str] = pa.Field(isin=_MODES, nullable=True)
    interval: Series[str] = pa.Field(isin=_INTERVALS, nullable=True)
    level: Series[float] = pa.Field(gt=0, lt=1, nullable=True)
    size_bin: Series[str]
    metric: Series[str]
    statistic: Series[str]
    value: Series[float] = pa.Field(nullable=True)

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True


class UncertaintyBinSchema(pa.DataFrameModel):
    """Width deciles averaged over assays: mean width, absolute error (assay-SD units) and
    coverage per decile of interval width within each assay (``train uncertainty``)."""

    model: Series[str]
    feature_set: Series[str]
    scheme: Series[str] = pa.Field(isin=list(CV_SCHEMES))
    mode: Series[str] = pa.Field(isin=_MODES)
    interval: Series[str] = pa.Field(isin=_INTERVALS)
    level: Series[float] = pa.Field(gt=0, lt=1)
    bin: Series[int] = pa.Field(ge=0)
    width_sd: Series[float] = pa.Field(ge=0)
    abs_error_sd: Series[float] = pa.Field(ge=0)
    coverage: Series[float] = pa.Field(ge=0, le=1)
    n_assays: Series[int] = pa.Field(ge=1)

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True


class CalibrationSchema(pa.DataFrameModel):
    """CV+ calibration rows of an all-data model (``train calibrate``; Task 8, ADR 0024).

    One row per variant, scheme and mode: the calibration ``fold`` that held it out, the
    prediction and fold-ensemble spread of the model fit without that fold, and the target
    standardised with all variants' ``target_mean`` and ``target_sd``.
    """

    assay_id: Series[str]
    position: Series[int] = pa.Field(ge=0)
    wt_aa: Series[str]
    mt_aa: Series[str]
    scheme: Series[str] = pa.Field(isin=list(CV_SCHEMES))
    mode: Series[str] = pa.Field(isin=_MODES)
    fold: Series[int] = pa.Field(ge=0)
    target: Series[float]
    prediction: Series[float]
    spread: Series[float] = pa.Field(ge=0)
    target_mean: Series[float]
    target_sd: Series[float] = pa.Field(gt=0)

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True
        unique = ["assay_id", "scheme", "mode", "position", "mt_aa"]  # noqa: RUF012 — read by pandera


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


class LandscapeRunSchema(pa.DataFrameModel):
    """What one ``mutascope score landscapes`` invocation did for one landscape of one assay.

    ``region`` is the label of the scored region; ``benchmark`` and ``full_target`` say which
    landscape of the assay it is (both true when the benchmark region is the full target).
    ``positions_computed`` are the residues that went through the model in this invocation: 0
    when the cache had them all, or when an earlier row filled the same cache entry.
    """

    assay_id: Series[str]
    model: Series[str]
    region: Series[str]
    region_length: Series[int] = pa.Field(ge=1)
    benchmark: Series[bool]
    full_target: Series[bool]
    positions_computed: Series[int] = pa.Field(ge=0)
    seconds: Series[float] = pa.Field(ge=0)

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True
        unique = ["assay_id", "region"]  # noqa: RUF012 — read by pandera


class LandscapeCoverageSchema(pa.DataFrameModel):
    """How much of one landscape of one assay the cache holds for one model.

    ``positions_scored`` are the residues with all 20 values; ``complete`` means every residue
    of the region (``mutascope score check-landscapes``).
    """

    assay_id: Series[str]
    model: Series[str]
    region: Series[str]
    region_length: Series[int] = pa.Field(ge=1)
    benchmark: Series[bool]
    full_target: Series[bool]
    positions_scored: Series[int] = pa.Field(ge=0)
    complete: Series[bool]

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True
        unique = ["assay_id", "model", "region"]  # noqa: RUF012 — read by pandera


class AgreementSchema(pa.DataFrameModel):
    """Model gate, one assay: our scores against ProteinGym's on the same single mutants.

    ``spearman`` and ``max_abs_diff`` compare the two score columns with each other;
    ``spearman_dms_*`` compare each with ``DMS_score`` (the inputs to the aggregate check).
    ``passed`` is the per-variant threshold alone. ``exception`` marks an assay whose
    ProteinGym scores are documented as wrong (``eval.model_gate.exceptions``, ADR 0015): it
    is left out of both checks, and it is an error for it to have ``passed``.
    """

    assay_id: Series[str] = pa.Field(unique=True)
    variants: Series[int] = pa.Field(ge=1)
    spearman: Series[float] = pa.Field(nullable=True)
    max_abs_diff: Series[float] = pa.Field(ge=0)
    spearman_dms_ours: Series[float] = pa.Field(nullable=True)
    spearman_dms_theirs: Series[float] = pa.Field(nullable=True)
    passed: Series[bool]
    exception: Series[bool]

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True
