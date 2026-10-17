"""Final deployment models: one ridge per eligible assay and its full landscape (ADR 0025).

For one assay, from all of its measured variants (``DMS_score`` standardised with their mean and
SD, as ``train calibrate`` did):

1. **CV+ is refit** on each landscape scheme's folds (``scheme`` mode, ADR 0024). Fits are
   deterministic, so its calibration rows must equal the ones ``train calibrate`` saved; a
   mismatch means the inputs changed, and is reported as ``calibration_max_abs_diff``.
2. **A final ridge per scheme**, fit on every measured variant, ``alpha`` chosen by inner CV
   over that scheme's folds. The ``random`` fit predicts variants at positions with measured
   variants (interpolation), the ``contiguous`` fit those at unmeasured positions
   (extrapolation): each is tuned for the task it does (``train.final.scheme_by_position``).
3. **Every one of the 19 x L substitutions** gets one scheme, by its position, and from it:

   - an unmeasured variant: the final ridge's prediction and the CV+ interval at it;
   - a measured variant: its **out-of-fold** prediction, ``M_k(x)`` from the CV+ model fit
     without its fold, never the final model (which trained on it), with the interval
     ``M_k(x) +/- q``. ``q`` is the CV+ order statistic of the other calibration rows' scores,
     the row's own left out: no model or residual that saw the variant enters its interval.

   "Has measured variants" means *other* measured variants for a measured one: a variant alone
   at its position is, without itself, at an unseen position, so it takes the unmeasured scheme.

Missing structure features (unmeasured residues outside a partial structure; ADR 0021) are
replaced by the column's mean over the measured variants, which is label-free; ridge standardises
with nearly that mean, so the feature then contributes almost nothing. Rows are flagged.

The out-of-fold predictions of every scheme also reproduce ``train cv``'s pooled Spearman once put
back on each test fold's training scale (``train cv`` standardises per fold): the same fits on a
differently standardised target, which ridge's affine equivariance makes equal.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from mutascope.data.schemas import CV_SCHEMES
from mutascope.eval.metrics import spearman
from mutascope.eval.schemas import LANDSCAPE_SCHEMES
from mutascope.features.matrix import FeatureMatrix, FloatArray, IntArray
from mutascope.features.schemas import SUBSTITUTIONS_PER_RESIDUE
from mutascope.features.sets import AssayMatrix
from mutascope.features.store import STRUCTURE_COLUMNS
from mutascope.models.conformal import INTERVALS, CVPlus, ModelFactory, order_ranks
from mutascope.models.cv import CrossValidationError
from mutascope.models.ridge import InnerCVRidge

LEVELS = (0.8, 0.9)
"""The interval levels the landscape schema has columns for."""

AssayDesign = Callable[[str], AssayMatrix]
"""``assay_id -> every substitution of the assay with its features`` (keys only, no labels)."""


@dataclass(frozen=True)
class FinalSettings:
    """``train.final`` (ADR 0025)."""

    levels: tuple[float, ...]
    interval: str
    epsilon: float
    measured_scheme: str
    """Scheme of a variant whose position has (other) measured variants."""
    unmeasured_scheme: str
    """Scheme of a variant at a position with no (other) measured variant."""
    buried_rel_sasa: float

    def __post_init__(self) -> None:
        if tuple(self.levels) != LEVELS:
            raise ValueError(f"the landscape stores levels {LEVELS}, got {self.levels}")
        if self.interval not in INTERVALS:
            raise ValueError(f"unknown interval {self.interval!r}; use one of {INTERVALS}")
        schemes = {self.measured_scheme, self.unmeasured_scheme}
        if not schemes <= set(LANDSCAPE_SCHEMES) or len(schemes) != 2:
            raise ValueError(f"schemes must be {LANDSCAPE_SCHEMES} once each, got {schemes}")

    @property
    def schemes(self) -> tuple[str, str]:
        return self.measured_scheme, self.unmeasured_scheme


@dataclass(frozen=True)
class FinalAssay:
    """One assay's outputs (version columns are added by the caller)."""

    landscape: pd.DataFrame
    """:class:`~mutascope.eval.schemas.FinalLandscapeSchema` columns up to ``structure_imputed``."""
    fits: pd.DataFrame
    """:class:`~mutascope.eval.schemas.RidgeFitSchema` rows."""
    coefficients: pd.DataFrame
    """:class:`~mutascope.eval.schemas.RidgeCoefficientSchema` rows."""
    check: dict[str, object]
    """:class:`~mutascope.eval.schemas.FinalCheckSchema` fields except ``cv_spearman_*``."""


def fold_scale_spearman(
    y: FloatArray, folds: IntArray, prediction: FloatArray, mean: float, std: float
) -> float:
    """Pooled Spearman of all-data-standardised out-of-fold predictions, as ``train cv`` pools.

    ``train cv`` standardises each test fold with its training folds' mean and SD and pools those
    values (:func:`~mutascope.models.cv.cross_validate`); ``prediction`` is in units of the
    all-data standardisation (``mean``, ``std``). Each fold is mapped back to raw units and on to
    its own training scale before pooling.
    """
    target, mapped = np.empty_like(y), np.empty_like(y)
    raw = mean + std * prediction
    for fold in np.unique(folds):
        test = folds == fold
        fold_mean, fold_std = float(np.mean(y[~test])), float(np.std(y[~test]))
        target[test] = (y[test] - fold_mean) / fold_std
        mapped[test] = (raw[test] - fold_mean) / fold_std
    return spearman(target, mapped)


def out_of_fold_bounds(
    center: FloatArray,
    scores: FloatArray,
    rows: IntArray,
    levels: Sequence[float],
    scale: FloatArray | None = None,
) -> dict[float, tuple[FloatArray, FloatArray]]:
    """Intervals at calibration rows, each from the other rows' conformity scores.

    ``center[j]`` is row ``rows[j]``'s out-of-fold prediction. Its half-width is the CV+ upper
    order statistic (``ceil(level * n)``-th smallest) of the ``n = len(scores) - 1`` scores of the
    other rows, times ``scale[j]`` for a normalised score. Lower and upper are symmetric: with
    one centre, CV+'s lower order statistic is the same rank from the other end.
    """
    n = scores.shape[0]
    order = np.argsort(scores, kind="stable")
    ranked = scores[order]
    rank = np.empty(n, dtype=np.int64)
    rank[order] = np.arange(n)
    out: dict[float, tuple[FloatArray, FloatArray]] = {}
    for level in levels:
        _, k = order_ranks(n - 1, level)
        # The k-th smallest of the others: unchanged if the row itself sits above it.
        above: npt.NDArray[np.bool_] = rank[rows] > k - 1
        half: FloatArray = np.asarray(np.where(above, ranked[k - 1], ranked[k]), dtype=np.float64)
        if scale is not None:
            half = half * scale
        out[level] = (center - half, center + half)
    return out


def _locate(keys: pd.DataFrame, measured: pd.DataFrame, assay_id: str) -> npt.NDArray[np.intp]:
    """Row of ``keys`` (the store's substitutions) for each measured variant, wild type checked."""
    index = pd.MultiIndex.from_frame(keys[["position", "mt_aa"]].astype({"position": "int64"}))
    wanted = pd.MultiIndex.from_frame(measured[["position", "mt_aa"]].astype({"position": "int64"}))
    rows = index.get_indexer(wanted)
    if (rows < 0).any() or (keys["wt_aa"].to_numpy()[rows] != measured["wt_aa"].to_numpy()).any():
        raise CrossValidationError(f"{assay_id}: measured variants missing from the store")
    return rows


def _impute(
    matrix: AssayMatrix, measured_rows: npt.NDArray[np.intp], assay_id: str
) -> tuple[FeatureMatrix, npt.NDArray[np.bool_], dict[str, float]]:
    """Features with missing structure values set to their measured-variant mean.

    Returns the matrix, which rows had a value replaced, and the value per replaced column.

    Raises:
        CrossValidationError: A non-structure column has a missing value, or a measured
            variant does (training rows are never imputed).
    """
    x = matrix.feature_matrix()
    dense = x.dense
    missing: npt.NDArray[np.bool_] = ~np.isfinite(dense)
    columns = np.flatnonzero(missing.any(axis=0))
    impute: dict[str, float] = {}
    for j in columns:
        name = x.names[j]
        if name not in STRUCTURE_COLUMNS or missing[measured_rows, j].any():
            raise CrossValidationError(f"{assay_id}: {name} is missing where it may not be")
        column: FloatArray = dense[measured_rows, j]
        value = float(np.mean(column))
        dense[missing[:, j], j] = value
        impute[name] = value
    imputed: npt.NDArray[np.bool_] = np.asarray(missing.any(axis=1), dtype=np.bool_)
    return x, imputed, impute


def final_landscape(
    assay_id: str,
    measured: pd.DataFrame,
    saved: pd.DataFrame,
    design: AssayDesign,
    make_model: ModelFactory,
    settings: FinalSettings,
) -> FinalAssay:
    """The final models and full landscape of one assay; see the module docstring.

    Args:
        assay_id: The assay.
        measured: Its measured variants in ``train calibrate``'s row order: ``position, wt_aa,
            mt_aa, DMS_score`` and ``fold_<scheme>`` for every CV scheme.
        saved: Its ``train calibrate`` rows in ``scheme`` mode (:class:`CalibrationSchema`).
        design: Builds every substitution's features.
        make_model: The final model (an :class:`InnerCVRidge` factory).
        settings: ``train.final``.

    Raises:
        CrossValidationError: The store and the measurements disagree, a feature is missing
            where it may not be, the model is not a ridge, or ``saved`` lacks a variant.
    """
    matrix = design(assay_id)
    keys = matrix.keys.reset_index(drop=True)
    length = int(keys["position"].max()) + 1
    if len(keys) != SUBSTITUTIONS_PER_RESIDUE * length:
        raise CrossValidationError(f"{assay_id}: {len(keys)} substitutions for {length} residues")
    rows = _locate(keys, measured, assay_id)
    x_all, imputed, impute = _impute(matrix, rows, assay_id)
    del matrix  # the float32 copy; x_all is the float64 one (1.4 GB for the largest target)
    x = x_all.take(rows)
    y: FloatArray = measured["DMS_score"].to_numpy(np.float64)
    mean, std = float(np.mean(y)), float(np.std(y))
    if not std > 0:
        raise CrossValidationError(f"{assay_id}: constant targets")
    z = (y - mean) / std

    cvplus: dict[str, CVPlus] = {}
    final: dict[str, InnerCVRidge] = {}
    max_diff = 0.0
    oof: dict[str, FloatArray] = {}
    for scheme in CV_SCHEMES:
        folds = measured[f"fold_{scheme}"].to_numpy(np.int64)
        reference = _saved_rows(saved, measured, scheme, assay_id)
        if scheme in settings.schemes:
            fitted = CVPlus.fit(x, z, folds, make_model)
            labels = np.asarray(fitted.groups, dtype=np.int64)[fitted.calibration.fold]
            if not np.array_equal(labels, reference["fold"].to_numpy(np.int64)):
                raise CrossValidationError(f"{assay_id}, {scheme}: calibration folds differ")
            for column in ("prediction", "spread"):
                ours = getattr(fitted.calibration, column)
                diff = np.abs(ours - reference[column].to_numpy(np.float64))
                max_diff = max(max_diff, float(diff.max()))
            model = make_model()
            if not isinstance(model, InnerCVRidge):
                raise CrossValidationError(f"the final model must be a ridge, got {model!r}")
            model.fit(x, z, folds)
            cvplus[scheme], final[scheme] = fitted, model
            oof[scheme] = fitted.calibration.prediction
        else:
            oof[scheme] = reference["prediction"].to_numpy(np.float64)

    # Each variant's scheme, from how many measured variants its position has (besides itself).
    position = keys["position"].to_numpy(np.int64)
    is_measured = np.zeros(len(keys), dtype=bool)
    is_measured[rows] = True
    count = np.bincount(position[rows], minlength=length)
    others = count[position] - is_measured
    scheme_of = np.where(others > 0, settings.measured_scheme, settings.unmeasured_scheme)
    status = np.where(
        count == SUBSTITUTIONS_PER_RESIDUE,
        "measured",
        np.where(count > 0, "partially_measured", "unmeasured"),
    )[position]

    prediction = np.full(len(keys), np.nan)
    bounds = {level: (np.full(len(keys), np.nan), np.full(len(keys), np.nan)) for level in LEVELS}
    measured_at = np.full(len(keys), -1, dtype=np.int64)
    measured_at[rows] = np.arange(len(rows))
    for scheme in settings.schemes:
        fitted = cvplus[scheme]
        new = np.flatnonzero(~is_measured & (scheme_of == scheme))
        if new.size:
            x_new = x_all.take(new)
            prediction[new] = final[scheme].predict(x_new)
            at = fitted.predict(x_new)
            found = fitted.intervals(at, settings.interval, LEVELS, settings.epsilon)
            for level, (lower, upper) in found.items():
                bounds[level][0][new], bounds[level][1][new] = lower, upper
        old = np.flatnonzero(is_measured & (scheme_of == scheme))
        if old.size:
            calibration = fitted.calibration
            j = measured_at[old]
            prediction[old] = calibration.prediction[j]
            scale = (
                None
                if settings.interval == "absolute"
                else calibration.spread[j] + settings.epsilon
            )
            found = out_of_fold_bounds(
                calibration.prediction[j],
                calibration.scores(settings.interval, settings.epsilon),
                j,
                LEVELS,
                scale,
            )
            for level, (lower, upper) in found.items():
                bounds[level][0][old], bounds[level][1][old] = lower, upper

    dms = np.full(len(keys), np.nan)
    dms[rows] = y
    landscape = pd.DataFrame(
        {
            "assay_id": assay_id,
            "position": position,
            "wt_aa": keys["wt_aa"].astype(str).to_numpy(),
            "mt_aa": keys["mt_aa"].astype(str).to_numpy(),
            "position_status": status,
            "measured": is_measured,
            "dms_score": dms,
            "prediction": mean + std * prediction,
            "prediction_source": np.where(is_measured, "out_of_fold", "final_model"),
            "calibration_scheme": scheme_of,
            **{
                f"{side}_{round(level * 100)}": mean + std * bounds[level][k]
                for level in LEVELS
                for k, side in enumerate(("lower", "upper"))
            },
            "structure_imputed": imputed,
        }
    )
    fits, coefficients = _published(assay_id, final, x.names, impute, mean, std, len(rows))
    check = _check(
        assay_id, landscape, x_all, y, measured, oof, mean, std, max_diff, final, settings
    )
    return FinalAssay(landscape=landscape, fits=fits, coefficients=coefficients, check=check)


def _saved_rows(
    saved: pd.DataFrame, measured: pd.DataFrame, scheme: str, assay_id: str
) -> pd.DataFrame:
    """``saved``'s rows of ``scheme`` in ``measured``'s order."""
    rows = saved[(saved["scheme"] == scheme) & (saved["mode"] == "scheme")]
    joined = measured[["position", "mt_aa"]].merge(
        rows, on=["position", "mt_aa"], how="left", validate="one_to_one"
    )
    if joined["prediction"].isna().any() or len(rows) != len(measured):
        raise CrossValidationError(
            f"{assay_id}, {scheme}: the calibration rows do not match the measured variants"
        )
    return joined


def _published(
    assay_id: str,
    final: dict[str, InnerCVRidge],
    names: Sequence[str],
    impute: dict[str, float],
    mean: float,
    std: float,
    n_train: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Each final fit in ``DMS_score`` units: ``mean + std * (b + x w)`` = ``b' + x w'``."""
    fits: list[dict[str, object]] = []
    coefficients: list[pd.DataFrame] = []
    for scheme, model in final.items():
        if model.fit_ is None or model.alpha_ is None or model.fit_.coef_site is not None:
            raise CrossValidationError(f"{assay_id}: the final ridge is unfitted or has a site")
        fits.append(
            {
                "assay_id": assay_id,
                "fit": scheme,
                "alpha": model.alpha_,
                "intercept": mean + std * model.fit_.intercept,
                "target_mean": mean,
                "target_sd": std,
                "n_train": n_train,
            }
        )
        coefficients.append(
            pd.DataFrame(
                {
                    "assay_id": assay_id,
                    "fit": scheme,
                    "feature": list(names),
                    "coefficient": std * model.fit_.coef_dense,
                    "impute": [impute.get(name, np.nan) for name in names],
                }
            )
        )
    return pd.DataFrame.from_records(fits), pd.concat(coefficients, ignore_index=True)


def _check(
    assay_id: str,
    landscape: pd.DataFrame,
    x_all: FeatureMatrix,
    y: FloatArray,
    measured: pd.DataFrame,
    oof: dict[str, FloatArray],
    mean: float,
    std: float,
    max_diff: float,
    final: dict[str, InnerCVRidge],
    settings: FinalSettings,
) -> dict[str, object]:
    """The per-assay checks (``train final`` fails on the hard ones; ADR 0025)."""
    values = landscape[["prediction", "lower_80", "upper_80", "lower_90", "upper_90"]]
    measured_rows = landscape[landscape["measured"]]
    coverage = {
        level: float(
            (
                (measured_rows["dms_score"] >= measured_rows[f"lower_{level}"])
                & (measured_rows["dms_score"] <= measured_rows[f"upper_{level}"])
            ).mean()
        )
        for level in (80, 90)
    }
    # Relative SASA as stored (missing outside a partial structure: left out of the check).
    raw_sasa = x_all.dense[:, x_all.column("rel_sasa")].copy()
    raw_sasa[landscape["structure_imputed"].to_numpy()] = np.nan
    buried = raw_sasa < settings.buried_rel_sasa
    exposed = raw_sasa >= settings.buried_rel_sasa
    predicted = landscape["prediction"].to_numpy(np.float64)
    buried_median = float(np.median(predicted[buried])) if buried.any() else np.nan
    exposed_median = float(np.median(predicted[exposed])) if exposed.any() else np.nan
    both = bool(buried.any() and exposed.any())
    alpha = {scheme: model.alpha_ for scheme, model in final.items()}
    return {
        "assay_id": assay_id,
        "n_measured": len(measured),
        "n_variants": len(landscape),
        "alpha_random": alpha["random"],
        "alpha_contiguous": alpha["contiguous"],
        "calibration_max_abs_diff": max_diff,
        **{
            f"oof_spearman_{scheme}": fold_scale_spearman(
                y, measured[f"fold_{scheme}"].to_numpy(np.int64), oof[scheme], mean, std
            )
            for scheme in CV_SCHEMES
        },
        "n_nan": int(values.isna().to_numpy().sum()),
        "n_bad_interval": int(
            (
                (values["lower_80"] >= values["upper_80"])
                | (values["lower_90"] >= values["upper_90"])
            )
            .to_numpy()
            .sum()
        ),
        "n_structure_imputed": int(landscape["structure_imputed"].sum()),
        "oof_coverage_80": coverage[80],
        "oof_coverage_90": coverage[90],
        "n_buried": int(buried.sum()),
        "n_exposed": int(exposed.sum()),
        "buried_median": buried_median,
        "exposed_median": exposed_median,
        "buried_lower": (buried_median < exposed_median) if both else None,
    }


def eligibility(
    reference: pd.DataFrame,
    structured: set[str],
    min_variants: int,
    label: str,
    zero_shot_default: dict[str, str],
) -> pd.DataFrame:
    """Every assay's entry in the bundle: eligible for a supervised landscape, or why not.

    Eligible: a structure (the feature set needs one) and at least ``min_variants`` measured
    variants. Otherwise the website default is zero-shot, SaProt with a structure and ESM-2
    650M without (``zero_shot_default["structure"]`` / ``["sequence"]``), and ``reason`` says why.

    Returns:
        ``assay_id, eligible, reason, default_model, has_structure, n_measured,
        target_length``, sorted by assay.
    """
    records: list[dict[str, object]] = []
    for row in reference.sort_values("DMS_id").itertuples(index=False):
        assay_id = str(row.DMS_id)
        n = int(str(row.DMS_number_single_mutants))
        has_structure = assay_id in structured
        reason: str | None = None
        if not has_structure:
            reason = "no structure"
        elif n < min_variants:
            reason = f"fewer than {min_variants} measured variants ({n})"
        default = zero_shot_default["structure" if has_structure else "sequence"]
        records.append(
            {
                "assay_id": assay_id,
                "eligible": reason is None,
                "reason": reason,
                "default_model": label if reason is None else default,
                "has_structure": has_structure,
                "n_measured": n,
                "target_length": len(str(row.target_seq)),
            }
        )
    return pd.DataFrame.from_records(records)


def final_failures(
    checks: pd.DataFrame, manifest: pd.DataFrame, calibration_tolerance: float, oof_tolerance: float
) -> list[str]:
    """What fails ``train final``: one line per broken check (ADR 0025).

    Every eligible assay needs a complete ``19 x L`` landscape with no NaN and ``lower < upper``
    everywhere, a CV+ refit equal to the saved calibration, and out-of-fold pooled Spearman
    equal to ``train cv``'s in every scheme. The buried/exposed check is reported, never failed.
    """
    failures: list[str] = []
    eligible = manifest[manifest["eligible"]].set_index("assay_id")
    absent = sorted(set(eligible.index) - set(checks["assay_id"]))
    if absent:
        failures.append(f"no landscape for {absent}")
    lengths = {str(k): int(v) for k, v in eligible["target_length"].items()}
    for row in checks.to_dict("records"):
        a = str(row["assay_id"])
        expected = SUBSTITUTIONS_PER_RESIDUE * lengths[a]
        if int(row["n_variants"]) != expected:
            failures.append(f"{a}: {row['n_variants']} landscape rows, expected {expected}")
        if int(row["n_nan"]):
            failures.append(f"{a}: {row['n_nan']} NaN values")
        if int(row["n_bad_interval"]):
            failures.append(f"{a}: {row['n_bad_interval']} intervals with lower >= upper")
        diff = float(row["calibration_max_abs_diff"])
        if not diff <= calibration_tolerance:
            failures.append(f"{a}: CV+ refit differs from the saved calibration by {diff:.3g}")
        for scheme in CV_SCHEMES:
            ours, task4 = float(row[f"oof_spearman_{scheme}"]), float(row[f"cv_spearman_{scheme}"])
            if not abs(ours - task4) <= oof_tolerance:
                failures.append(
                    f"{a}, {scheme}: out-of-fold Spearman {ours:.6f} vs train cv {task4:.6f}"
                )
    return failures
