"""``train cv``, ``canary`` and ``compare`` with the MLP head and a minimum assay size (ADR 0023).

Kept out of ``test_cli_train.py``: that file also runs in the LightGBM process (``make test``),
and the MLP loads torch.
"""

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
from mutascope.eval.benchmark import read_results
from mutascope.eval.schemas import (
    CanarySchema,
    ComparisonSchema,
    SelectedHyperparameterSchema,
    SupervisedMetricSchema,
)

CONF = Path(__file__).parents[2] / "conf"
NO_TRACKING = "tracking.enabled=false"
SMALL_MLP = tuple(
    f"train.params.mlp_mse.{k}={v}"
    for k, v in {"hidden": "[16,8]", "max_epochs": 6, "patience": 2, "pca_components": 2}.items()
)


@pytest.fixture
def inputs(tmp_path: Path) -> dict[str, Path]:
    """A_TEST (480 variants) and SMALL_TEST (160) have structures; C_TEST (480) has none."""
    assays = [
        synthetic_assay("A_TEST", seed=0),
        synthetic_assay("SMALL_TEST", length=20, seed=1),
        synthetic_assay("C_TEST", seed=2),
    ]
    reference = tmp_path / "DMS_substitutions.csv"
    pd.DataFrame(
        {
            "DMS_id": ["A_TEST", "SMALL_TEST", "C_TEST"],
            "UniProt_ID": ["P1", "P2", "P3"],
            "coarse_selection_type": ["Stability", "Stability", "Activity"],
            "DMS_number_single_mutants": [len(a) for a in assays],
        }
    ).to_csv(reference, index=False)
    paths = {
        "store": write_feature_store(
            assays, tmp_path / "store", with_structure=["A_TEST", "SMALL_TEST"]
        ),
        "variants": write_variants(assays, tmp_path / "variants"),
        "cv": write_fold_csvs(assays, tmp_path / "cv"),
        "folds": tmp_path / "folds",
        "reference": reference,
        "results": tmp_path / "results",
    }
    code, output = run(
        "data", "folds",
        "--variants-dir", str(paths["variants"]),
        "--folds-dir", str(paths["cv"]),
        "--out-dir", str(paths["folds"]),
    )  # fmt: skip
    assert code == 0, output
    return paths


def run(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, [*args, "--config-dir", str(CONF)], catch_exceptions=False)
    return result.exit_code, result.output


def common(inputs: dict[str, Path]) -> list[str]:
    return [
        "--variants-dir", str(inputs["variants"]),
        "--folds-dir", str(inputs["folds"]),
        "--out-dir", str(inputs["results"]),
        "--store-dir", str(inputs["store"]),
        "--jobs", "1",
    ]  # fmt: skip


def train(inputs: dict[str, Path], *overrides: str) -> tuple[int, str]:
    return run(
        "train", "cv", *common(inputs), "--reference", str(inputs["reference"]),
        NO_TRACKING, "train.feature_set=seq_struct", *overrides,
    )  # fmt: skip


def test_mlp_skips_small_assays_and_is_compared_on_the_rest(inputs: dict[str, Path]) -> None:
    code, output = train(inputs, "train.model=mlp_mse", *SMALL_MLP)
    assert code == 0, output
    assert "skipped 1 of 2 assays with fewer than 300 variants" in output
    assert "SMALL_TEST" in output
    table = SupervisedMetricSchema.validate(
        read_results(inputs["results"] / "cv_mlp_mse_seq_struct.parquet")
    )
    assert set(table["assay_id"]) == {"A_TEST"}
    chosen = SelectedHyperparameterSchema.validate(
        pd.read_parquet(inputs["results"] / "cv_mlp_mse_seq_struct_hyperparameters.parquet")
    )
    assert len(chosen) == 3 * 5 * 2  # schemes x folds x (epochs, validation fold)

    code, output = train(inputs, "train.model=ridge")
    assert code == 0, output
    assert "skipped" not in output  # ridge has no minimum: both structure assays
    code, output = run(
        "train", "compare",
        "--results-dir", str(inputs["results"]),
        "--reference", str(inputs["reference"]),
        "--out-dir", str(inputs["results"]),
        NO_TRACKING, "train.comparisons=[[mlp_mse_seq_struct,ridge_seq_struct]]",
        "eval.bootstrap.n_resamples=50",
    )  # fmt: skip
    assert code == 0, output
    comparisons = ComparisonSchema.validate(
        read_results(inputs["results"] / "supervised_comparisons.parquet")
    )
    assert set(comparisons.loc[comparisons["level"] == "overall", "n_assays"]) == {1}


def test_compare_refuses_an_assay_missing_for_another_reason(inputs: dict[str, Path]) -> None:
    """Only assays below the minimum may be absent: without it, the gap is an error."""
    assert train(inputs, "train.model=mlp_mse", *SMALL_MLP)[0] == 0
    assert train(inputs, "train.model=ridge")[0] == 0
    code, output = run(
        "train", "compare",
        "--results-dir", str(inputs["results"]),
        "--reference", str(inputs["reference"]),
        "--out-dir", str(inputs["results"]),
        NO_TRACKING, "train.comparisons=[[mlp_mse_seq_struct,ridge_seq_struct]]",
        "eval.bootstrap.n_resamples=50", "train.min_variants.mlp_mse=0",
    )  # fmt: skip
    assert code == 1
    assert "do not cover the same" in output


def test_mlp_canary(inputs: dict[str, Path]) -> None:
    """With a training budget this short (6 epochs) the shuffled-label networks stay near their
    seeded initial weights, one fixed random function of features that carry signal, and the
    canary failed at z = +7.9 without any leak. It trains as configured here (ADR 0023)."""
    code, output = run(
        "train", "canary", *common(inputs), NO_TRACKING,
        "train.model=mlp_mse", "train.feature_set=seq_struct",
        "train.params.mlp_mse.hidden=[64,16]", "train.params.mlp_mse.pca_components=2",
        "train.canary.assays=[A_TEST]", "train.canary.n_permutations=5",
        "train.schemes=[random]",
    )  # fmt: skip
    assert code == 0, output
    table = CanarySchema.validate(
        pd.read_parquet(inputs["results"] / "canary_mlp_mse_seq_struct.parquet")
    )
    assert len(table) == 1


def test_canary_refuses_an_assay_below_the_minimum(inputs: dict[str, Path]) -> None:
    code, output = run(
        "train", "canary", *common(inputs), NO_TRACKING,
        "train.model=mlp_mse", "train.feature_set=seq_struct", *SMALL_MLP,
        "train.canary.assays=[SMALL_TEST]", "train.canary.n_permutations=2",
    )  # fmt: skip
    assert code == 1
    assert "below train.min_variants" in output
