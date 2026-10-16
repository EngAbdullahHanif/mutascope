"""``train uncertainty``, ``train calibrate`` and ``train uncertainty-figures`` end to end on
synthetic assays (ADR 0024)."""

from pathlib import Path

import pandas as pd
import pytest
from tests.synthetic_assays import (
    synthetic_assay,
    write_feature_store,
    write_fold_csvs,
    write_variants,
)
from typer.testing import CliRunner

from mutascope.cli import app
from mutascope.eval.schemas import (
    CalibrationSchema,
    UncertaintyBinSchema,
    UncertaintyMetricSchema,
    UncertaintySummarySchema,
)

CONF = Path(__file__).parents[2] / "conf"
NO_TRACKING = "tracking.enabled=false"
SMALL = ("train.uncertainty.width_bins=4", "train.params.ridge.alphas.num=5")


@pytest.fixture
def inputs(tmp_path: Path) -> dict[str, Path]:
    assays = [synthetic_assay(f"{name}_TEST", length=40, seed=i) for i, name in enumerate("AB")]
    reference = tmp_path / "DMS_substitutions.csv"
    pd.DataFrame(
        {
            "DMS_id": ["A_TEST", "B_TEST"],
            "UniProt_ID": ["P1", "P2"],
            "coarse_selection_type": ["Stability", "Activity"],
        }
    ).to_csv(reference, index=False)
    paths = {
        "store": write_feature_store(
            assays, tmp_path / "store", with_structure=["A_TEST", "B_TEST"]
        ),
        "variants": write_variants(assays, tmp_path / "variants"),
        "cv": write_fold_csvs(assays, tmp_path / "cv"),
        "folds": tmp_path / "folds",
        "reference": reference,
        "results": tmp_path / "results",
        "figures": tmp_path / "figures",
        "calibration": tmp_path / "calibration",
    }
    assert run(
        "data", "folds",
        "--variants-dir", str(paths["variants"]),
        "--folds-dir", str(paths["cv"]),
        "--out-dir", str(paths["folds"]),
    )[0] == 0  # fmt: skip
    return paths


def run(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, [*args, "--config-dir", str(CONF)], catch_exceptions=False)
    return result.exit_code, result.output


def common(inputs: dict[str, Path]) -> list[str]:
    return [
        "--variants-dir", str(inputs["variants"]),
        "--folds-dir", str(inputs["folds"]),
        "--store-dir", str(inputs["store"]),
        "--jobs", "1",
    ]  # fmt: skip


def test_uncertainty_then_figures(inputs: dict[str, Path]) -> None:
    code, output = run(
        "train", "uncertainty", *common(inputs),
        "--reference", str(inputs["reference"]),
        "--out-dir", str(inputs["results"]),
        NO_TRACKING, *SMALL,
    )  # fmt: skip
    assert code == 0, output
    assert "ridge on seq_struct: 2 assays" in output
    assert "contiguous scheme absolute   90%: coverage mean" in output
    stem = inputs["results"] / "uncertainty_ridge_seq_struct"
    metrics = UncertaintyMetricSchema.validate(pd.read_parquet(f"{stem}.parquet"))
    # Per assay and scheme: n_variants, prediction_spearman, 2 spread rows, 2 x 2 x 2 x 3 metrics.
    assert len(metrics) == 2 * 3 * (2 + 2 + 24)
    coverage = metrics[metrics["metric"] == "coverage"]["value"]
    assert coverage.between(0, 1).all() and coverage.mean() > 0.6
    summary = UncertaintySummarySchema.validate(pd.read_parquet(f"{stem}_summary.parquet"))
    assert {"n_below", "aggregate", "mean"} <= set(summary["statistic"])
    bins = UncertaintyBinSchema.validate(pd.read_parquet(f"{stem}_bins.parquet"))
    assert set(bins["bin"]) == {0, 1, 2, 3}
    assert not (inputs["results"] / ".uncertainty_ridge_seq_struct_checkpoint").exists()

    code, output = run(
        "train", "uncertainty-figures",
        "--results-dir", str(inputs["results"]),
        "--out-dir", str(inputs["figures"]),
    )  # fmt: skip
    assert code == 0, output
    for name in ("calibration_ridge_seq_struct", "width_error_ridge_seq_struct"):
        assert (inputs["figures"] / f"{name}.svg").exists()
        assert (inputs["figures"] / f"{name}.png").exists()


def test_figures_need_the_results(inputs: dict[str, Path]) -> None:
    inputs["results"].mkdir()
    code, output = run(
        "train", "uncertainty-figures",
        "--results-dir", str(inputs["results"]),
        "--out-dir", str(inputs["figures"]),
    )  # fmt: skip
    assert code == 1
    assert "run `train uncertainty`" in output


def test_calibrate_writes_one_file_per_assay_and_resumes(inputs: dict[str, Path]) -> None:
    args = ["train", "calibrate", *common(inputs), "--out-dir", str(inputs["calibration"])]
    code, output = run(*args, NO_TRACKING, *SMALL)
    assert code == 0, output
    assert "2 computed now, 0 already written" in output
    for assay in ("A_TEST", "B_TEST"):
        rows = CalibrationSchema.validate(
            pd.read_parquet(inputs["calibration"] / "ridge_seq_struct" / f"{assay}.parquet")
        )
        assert set(rows["scheme"]) == {"random", "modulo", "contiguous"}
        assert set(rows["mode"]) == {"random", "scheme"}
        assert len(rows) == 6 * rows.groupby(["scheme", "mode"]).size().iloc[0]
    code, output = run(*args, NO_TRACKING, *SMALL)
    assert code == 0, output
    assert "0 computed now, 2 already written" in output
