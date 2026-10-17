"""Final deployment models and landscapes (ADR 0025): complete, out-of-fold where measured,
reproducing the saved calibration and ``train cv``, and the publishable coefficients."""

from functools import partial

import numpy as np
import pandas as pd
import pytest
from tests.synthetic_assays import synthetic_assay

from mutascope.alphabet import AA_ORDER
from mutascope.eval.metrics import spearman
from mutascope.eval.schemas import FinalLandscapeSchema
from mutascope.features.base import KEY_COLUMNS
from mutascope.features.matrix import FeatureMatrix
from mutascope.features.sets import AssayMatrix, Float32Matrix
from mutascope.models.conformal import order_ranks
from mutascope.models.cv import cross_validate
from mutascope.models.final import (
    FinalAssay,
    FinalSettings,
    eligibility,
    final_failures,
    final_landscape,
    fold_scale_spearman,
    out_of_fold_bounds,
)
from mutascope.models.ridge import InnerCVRidge
from mutascope.models.uncertainty import UncertaintySettings, assay_calibration

LENGTH = 40
UNMEASURED = range(34, 40)
"""Positions with no measured variant; their structure feature is missing."""
ALONE = 20
"""A position with exactly one measured variant."""
SETTINGS = FinalSettings((0.8, 0.9), "absolute", 0.01, "random", "contiguous", 0.3)
make_ridge = partial(InnerCVRidge, [0.1, 1.0, 10.0, 100.0])
Result = tuple[FinalAssay, pd.DataFrame, pd.DataFrame]
"""The assay's outputs, its measured variants and its saved calibration rows."""


def _assay() -> tuple[pd.DataFrame, str]:
    full = synthetic_assay("A_TEST", length=LENGTH, per_position=6, seed=3)
    target = "".join(full.groupby("position")["wt_aa"].first())
    keep = ~full["position"].isin(UNMEASURED)
    alone = full.index[full["position"] == ALONE]
    keep[alone[1:]] = False
    return full[keep].reset_index(drop=True), target


ASSAY, TARGET = _assay()
NAMES = (
    [f"onehot_wt_{a}" for a in AA_ORDER]
    + [f"onehot_mt_{a}" for a in AA_ORDER]
    + ["rel_position", "rel_position_sq", "rel_sasa"]
)


def _features(keys: pd.DataFrame) -> Float32Matrix:
    aa = np.array(list(AA_ORDER))
    wt = (keys["wt_aa"].to_numpy()[:, None] == aa).astype(np.float32)
    mt = (keys["mt_aa"].to_numpy()[:, None] == aa).astype(np.float32)
    position = keys["position"].to_numpy(np.int64)
    rel = (position / LENGTH).astype(np.float32)[:, None]
    sasa = ((position % 7) / 7).astype(np.float32)[:, None]
    sasa[np.isin(position, list(UNMEASURED))] = np.nan
    return np.hstack([wt, mt, rel, rel**2, sasa]).astype(np.float32)


def all_substitutions(assay_id: str) -> AssayMatrix:
    del assay_id
    rows = [(p, wt, mt) for p, wt in enumerate(TARGET) for mt in AA_ORDER if mt != wt]
    keys = pd.DataFrame(rows, columns=["position", "wt_aa", "mt_aa"])
    return AssayMatrix(keys=keys, features=_features(keys), names=list(NAMES))


def measured_design(assay_id: str, keys: pd.DataFrame) -> FeatureMatrix:
    del assay_id
    assert list(keys.columns) == list(KEY_COLUMNS)
    return FeatureMatrix(dense=_features(keys).astype(np.float64), names=tuple(NAMES), site=None)


@pytest.fixture(scope="module")
def result() -> Result:
    calibration = UncertaintySettings((0.8, 0.9), ("scheme",), ("absolute",), 0.01, 0, 4)
    columns = [*KEY_COLUMNS, "DMS_score", "fold_random", "fold_modulo", "fold_contiguous"]
    measured = ASSAY[columns]
    saved = assay_calibration(
        "A_TEST",
        measured,
        measured_design,
        make_ridge,
        ["random", "modulo", "contiguous"],
        calibration,
    )
    out = final_landscape("A_TEST", measured, saved, all_substitutions, make_ridge, SETTINGS)
    return out, measured, saved


def test_landscape_is_complete_and_valid(result: Result) -> None:
    out, measured, _ = result
    landscape = out.landscape.assign(
        code_version="abc", config_sha256="0" * 64, manifest_sha256="1" * 64
    )
    FinalLandscapeSchema.validate(landscape, lazy=True)
    assert len(landscape) == 19 * LENGTH
    assert int(landscape["measured"].sum()) == len(measured)
    assert out.check["n_nan"] == 0 and out.check["n_bad_interval"] == 0
    assert out.check["calibration_max_abs_diff"] == 0.0


def test_out_of_fold_spearman_reproduces_train_cv(result: Result) -> None:
    out, measured, _ = result
    x = measured_design("A_TEST", measured[list(KEY_COLUMNS)])
    y = measured["DMS_score"].to_numpy(np.float64)
    for scheme in ("random", "modulo", "contiguous"):
        oof = cross_validate(x, y, measured[f"fold_{scheme}"].to_numpy(np.int64), make_ridge)
        assert out.check[f"oof_spearman_{scheme}"] == pytest.approx(
            spearman(oof.target, oof.prediction), abs=1e-9
        )


def test_fold_scale_spearman_undoes_the_all_data_standardisation() -> None:
    rng = np.random.default_rng(0)
    y = rng.normal(size=60)
    folds = np.repeat(np.arange(3), 20)
    prediction = y + rng.normal(size=60)
    mean, std = float(y.mean()), float(y.std())
    target, mapped = np.empty(60), np.empty(60)
    for f in range(3):
        test = folds == f
        m, s = float(y[~test].mean()), float(y[~test].std())
        target[test], mapped[test] = (y[test] - m) / s, (prediction[test] - m) / s
    got = fold_scale_spearman(y, folds, (prediction - mean) / std, mean, std)
    assert got == pytest.approx(spearman(target, mapped))


def test_scheme_and_source_by_position(result: Result) -> None:
    out, _, saved = result
    landscape = out.landscape
    unmeasured = landscape["position"].isin(list(UNMEASURED))
    assert (landscape.loc[unmeasured, "position_status"] == "unmeasured").all()
    assert (landscape.loc[unmeasured, "calibration_scheme"] == "contiguous").all()
    alone = landscape[landscape["position"] == ALONE]
    # The one measured variant has no other measured variant: it takes the unmeasured scheme;
    # the unmeasured ones beside it have a measured neighbour: the measured scheme.
    assert alone.loc[alone["measured"], "calibration_scheme"].tolist() == ["contiguous"]
    assert (alone.loc[~alone["measured"], "calibration_scheme"] == "random").all()
    assert (alone["position_status"] == "partially_measured").all()
    # A measured variant's prediction is its out-of-fold value from the calibration rows.
    fits = out.fits.set_index("fit")
    mean, sd = float(fits["target_mean"].iloc[0]), float(fits["target_sd"].iloc[0])
    measured = landscape[landscape["measured"]].merge(
        saved[saved["mode"] == "scheme"],
        left_on=["position", "mt_aa", "calibration_scheme"],
        right_on=["position", "mt_aa", "scheme"],
    )
    np.testing.assert_allclose(
        measured["prediction_x"], mean + sd * measured["prediction_y"], rtol=0, atol=1e-12
    )


def test_coefficients_reproduce_final_predictions(result: Result) -> None:
    out, _, _ = result
    matrix = all_substitutions("A_TEST")
    x = matrix.features.astype(np.float64)
    coefficients = out.coefficients
    for scheme in ("random", "contiguous"):
        rows = coefficients[coefficients["fit"] == scheme]
        impute = rows["impute"].to_numpy(np.float64)
        filled = np.where(np.isnan(x), impute[None, :], x)
        intercept = float(str(out.fits.set_index("fit").loc[scheme, "intercept"]))
        predicted = intercept + filled @ rows["coefficient"].to_numpy(np.float64)
        landscape = out.landscape
        use = (~landscape["measured"]) & (landscape["calibration_scheme"] == scheme)
        np.testing.assert_allclose(predicted[use.to_numpy()], landscape.loc[use, "prediction"])


def test_missing_structure_is_imputed_with_the_measured_mean(result: Result) -> None:
    out, measured, _ = result
    landscape = out.landscape
    flagged = landscape["structure_imputed"].to_numpy()
    assert np.array_equal(flagged, landscape["position"].isin(list(UNMEASURED)).to_numpy())
    sasa = out.coefficients.set_index(["fit", "feature"]).loc[("random", "rel_sasa"), "impute"]
    expected = np.mean(_features(measured)[:, -1].astype(np.float64))
    assert float(str(sasa)) == pytest.approx(expected)
    never_missing = out.coefficients[out.coefficients["feature"] != "rel_sasa"]
    assert never_missing["impute"].isna().all()


def test_out_of_fold_bounds_leave_the_row_out() -> None:
    rng = np.random.default_rng(1)
    scores = rng.exponential(size=37)
    rows = np.arange(37)
    center = rng.normal(size=37)
    found = out_of_fold_bounds(center, scores, rows, (0.8, 0.9))
    for level, (lower, upper) in found.items():
        _, k = order_ranks(36, level)
        for i in rows:
            q = np.sort(np.delete(scores, i))[k - 1]
            assert lower[i] == pytest.approx(center[i] - q)
            assert upper[i] == pytest.approx(center[i] + q)


def test_eligibility_needs_structure_and_size() -> None:
    reference = pd.DataFrame(
        {
            "DMS_id": ["B", "A", "C"],
            "DMS_number_single_mutants": [500, 299, 900],
            "target_seq": ["MK", "MKV", "M"],
        }
    )
    table = eligibility(
        reference, {"A", "B"}, 300, "ridge_seq_struct", {"structure": "saprot", "sequence": "esm2"}
    ).set_index("assay_id")
    assert table.loc["B", "eligible"] and table.loc["B", "default_model"] == "ridge_seq_struct"
    assert table.loc["A", "reason"] == "fewer than 300 measured variants (299)"
    assert table.loc["A", "default_model"] == "saprot"
    assert table.loc["C", "reason"] == "no structure"
    assert table.loc["C", "default_model"] == "esm2"
    assert list(table.index) == ["A", "B", "C"]


def test_failures_name_every_broken_check() -> None:
    manifest = pd.DataFrame(
        {"assay_id": ["A", "B"], "eligible": [True, True], "target_length": [2, 2]}
    )
    check = {
        "assay_id": "A",
        "n_variants": 38,
        "n_nan": 1,
        "n_bad_interval": 2,
        "calibration_max_abs_diff": 1e-6,
        **{f"oof_spearman_{s}": 0.5 for s in ("random", "modulo", "contiguous")},
        **{f"cv_spearman_{s}": 0.5 for s in ("random", "modulo", "contiguous")},
    }
    assert final_failures(pd.DataFrame([check]), manifest, 1e-9, 1e-6) == [
        "no landscape for ['B']",
        "A: 1 NaN values",
        "A: 2 intervals with lower >= upper",
        "A: CV+ refit differs from the saved calibration by 1e-06",
    ]
    check |= {"n_nan": 0, "n_bad_interval": 0, "calibration_max_abs_diff": 0.0}
    check |= {"cv_spearman_modulo": 0.51}
    failures = final_failures(pd.DataFrame([check]), manifest.iloc[:1], 1e-9, 1e-6)
    assert failures == ["A, modulo: out-of-fold Spearman 0.500000 vs train cv 0.510000"]


def test_settings_refuse_other_levels() -> None:
    with pytest.raises(ValueError, match="levels"):
        FinalSettings((0.9,), "absolute", 0.01, "random", "contiguous", 0.2)
    with pytest.raises(ValueError, match="schemes"):
        FinalSettings((0.8, 0.9), "absolute", 0.01, "random", "random", 0.2)
