"""``mutascope data folds`` then ``mutascope train cv``, end to end on synthetic assays."""

from pathlib import Path

import pandas as pd
import pytest
from tests.synthetic_assays import synthetic_assay, write_fold_csvs, write_variants
from typer.testing import CliRunner

from mutascope.cli import app
from mutascope.eval.benchmark import read_results
from mutascope.eval.schemas import SupervisedMetricSchema

CONF = Path(__file__).parents[2] / "conf"
NO_TRACKING = "tracking.enabled=false"


@pytest.fixture
def inputs(tmp_path: Path) -> dict[str, Path]:
    assays = [synthetic_assay(f"{name}_TEST", seed=i) for i, name in enumerate("ABC")]
    reference = tmp_path / "DMS_substitutions.csv"
    pd.DataFrame(
        {
            "DMS_id": ["A_TEST", "B_TEST", "C_TEST"],
            "UniProt_ID": ["P1", "P1", "P2"],
            "coarse_selection_type": ["Stability", "Stability", "Activity"],
        }
    ).to_csv(reference, index=False)
    return {
        "variants": write_variants(assays, tmp_path / "variants"),
        "cv": write_fold_csvs(assays, tmp_path / "cv"),
        "folds": tmp_path / "folds",
        "reference": reference,
        "results": tmp_path / "results",
    }


def run(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, [*args, "--config-dir", str(CONF)], catch_exceptions=False)
    return result.exit_code, result.output


def folds(inputs: dict[str, Path]) -> tuple[int, str]:
    return run(
        "data", "folds",
        "--variants-dir", str(inputs["variants"]),
        "--folds-dir", str(inputs["cv"]),
        "--out-dir", str(inputs["folds"]),
    )  # fmt: skip


def train(inputs: dict[str, Path], *overrides: str) -> tuple[int, str]:
    return run(
        "train", "cv",
        "--variants-dir", str(inputs["variants"]),
        "--folds-dir", str(inputs["folds"]),
        "--reference", str(inputs["reference"]),
        "--out-dir", str(inputs["results"]),
        NO_TRACKING, *overrides,
    )  # fmt: skip


def test_folds_then_cv_writes_long_results(inputs: dict[str, Path]) -> None:
    code, output = folds(inputs)
    assert code == 0, output
    assert "3 assays" in output
    assert "random: 3 assays with 5 folds" in output

    code, output = train(inputs)
    assert code == 0, output
    results = read_results(inputs["results"] / "cv_training_mean_none.parquet")
    SupervisedMetricSchema.validate(results)
    # 3 assays x 3 schemes x (pooled + 5 folds) x 2 metrics
    assert len(results) == 3 * 3 * 6 * 2
    assert results.loc[results["metric"] == "spearman", "value"].isna().all()
    assert results.loc[results["metric"] == "mse", "value"].notna().all()
    assert "spearman nan (0 assays defined)" in output


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ("train.model=nope", "unknown model 'nope'"),
        ("train.feature_set=nope", "unknown feature set 'nope'"),
        ("train.schemes=[random,blocks]", "unknown ['blocks']"),
    ],
)
def test_bad_train_config_fails(inputs: dict[str, Path], override: str, message: str) -> None:
    assert folds(inputs)[0] == 0
    code, output = train(inputs, override)
    assert code == 1
    assert message in output
    assert not inputs["results"].exists()
