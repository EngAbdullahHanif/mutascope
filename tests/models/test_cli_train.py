"""``mutascope data folds`` then the ``train`` commands, end to end on synthetic assays."""

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
    ShapImportanceSchema,
    SummarySchema,
    SupervisedLeaderboardSchema,
    SupervisedMetricSchema,
)

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
        "store": write_feature_store(
            assays, tmp_path / "store", with_structure=["A_TEST", "B_TEST"]
        ),
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


def train(
    inputs: dict[str, Path], *overrides: str, store: bool = False, jobs: int = 1
) -> tuple[int, str]:
    return run(
        "train", "cv",
        "--variants-dir", str(inputs["variants"]),
        "--folds-dir", str(inputs["folds"]),
        "--reference", str(inputs["reference"]),
        "--out-dir", str(inputs["results"]),
        "--jobs", str(jobs),
        *(["--store-dir", str(inputs["store"])] if store else []),
        NO_TRACKING, *overrides,
    )  # fmt: skip


SCHEMES = ("random", "modulo", "contiguous")
RUNS = [
    ("ridge", "site_onehot"),
    ("augmented_ridge", "site_onehot_esm2"),
    ("ridge", "seq"),
    ("ridge", "seq_struct"),
    ("zero_shot", "esm2_score"),
    ("zero_shot", "saprot_score"),
]


def train_all(inputs: dict[str, Path]) -> dict[str, str]:
    assert folds(inputs)[0] == 0
    outputs: dict[str, str] = {}
    for model, feature_set in RUNS:
        code, output = train(
            inputs, f"train.model={model}", f"train.feature_set={feature_set}", store=True
        )
        assert code == 0, output
        outputs[f"{model}_{feature_set}"] = output
    return outputs


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


def test_store_feature_sets_one_assay_at_a_time(inputs: dict[str, Path]) -> None:
    outputs = train_all(inputs)
    results = inputs["results"]
    for model, feature_set in RUNS:
        label = f"{model}_{feature_set}"
        table = SupervisedMetricSchema.validate(read_results(results / f"cv_{label}.parquet"))
        expected = {"A_TEST", "B_TEST"} if feature_set.endswith(("struct", "saprot_score")) else {
            "A_TEST", "B_TEST", "C_TEST"
        }  # fmt: skip
        assert set(table["assay_id"]) == expected, label
        # Pooled values are defined. Per fold, the one-hot ridge predicts one constant for
        # positions it never saw (modulo, contiguous), so that fold's Spearman is undefined.
        assert table.loc[table["fold"].isna(), "value"].notna().all(), label
        tuned = results / f"cv_{label}_hyperparameters.parquet"
        assert tuned.exists() == (model != "zero_shot"), label
    chosen = SelectedHyperparameterSchema.validate(
        pd.read_parquet(results / "cv_ridge_seq_hyperparameters.parquet")
    )
    assert len(chosen) == 3 * 3 * 5  # assays x schemes x folds
    assert set(chosen["name"]) == {"alpha"}
    assert "alpha: median 10^" in outputs["ridge_seq"]
    # Position memorisation cannot reach unseen positions; the unpenalised score can.
    pooled = read_results(results / "cv_augmented_ridge_site_onehot_esm2.parquet")
    pooled = pooled[pooled["fold"].isna() & (pooled["metric"] == "spearman")]
    assert (pooled["value"] > 0.4).all()


def test_jobs_do_not_change_the_results(inputs: dict[str, Path]) -> None:
    """Assays run in worker processes give the same files as one process, row for row."""
    assert folds(inputs)[0] == 0
    tables: dict[int, list[pd.DataFrame]] = {}
    for jobs in (1, 2):
        code, output = train(
            inputs, "train.model=ridge", "train.feature_set=seq", store=True, jobs=jobs
        )
        assert code == 0, output
        assert f"with {jobs} job(s)" in output
        tables[jobs] = [
            pd.read_parquet(inputs["results"] / f"cv_ridge_seq{suffix}.parquet")
            for suffix in ("", "_hyperparameters")
        ]
    for serial, parallel in zip(tables[1], tables[2], strict=True):
        pd.testing.assert_frame_equal(serial, parallel)
    # The checkpoint of finished assays is removed once the results are written.
    assert not (inputs["results"] / ".cv_ridge_seq_checkpoint").exists()


def test_an_assay_subset_runs_only_those_assays(inputs: dict[str, Path]) -> None:
    assert folds(inputs)[0] == 0
    code, output = train(inputs, "train.assays=[A_TEST,C_TEST]")
    assert code == 0, output
    table = read_results(inputs["results"] / "cv_training_mean_none.parquet")
    assert set(table["assay_id"]) == {"A_TEST", "C_TEST"}
    code, output = train(inputs, "train.assays=[A_TEST,NOPE]")
    assert code == 1
    assert "assays without variants: ['NOPE']" in output
    code, output = train(
        inputs,
        "train.model=ridge",
        "train.feature_set=seq_struct",
        "train.assays=[C_TEST]",
        store=True,
    )
    assert code == 1
    assert "does not cover train.assays ['C_TEST']" in output


def test_store_set_needs_the_store(inputs: dict[str, Path]) -> None:
    assert folds(inputs)[0] == 0
    code, output = train(inputs, "train.model=ridge", "train.feature_set=seq")
    assert code == 1
    assert "pass --store-dir" in output


def test_canary_on_a_real_model(inputs: dict[str, Path]) -> None:
    assert folds(inputs)[0] == 0
    tables: list[pd.DataFrame] = []
    for jobs in ("1", "2"):
        code, output = run(
            "train", "canary",
            "--variants-dir", str(inputs["variants"]),
            "--folds-dir", str(inputs["folds"]),
            "--out-dir", str(inputs["results"]),
            "--store-dir", str(inputs["store"]),
            "--jobs", jobs,
            NO_TRACKING, "train.model=augmented_ridge", "train.feature_set=site_onehot_esm2",
            "train.canary.assays=[A_TEST,C_TEST]", "train.canary.n_permutations=6",
        )  # fmt: skip
        assert code == 0, output
        tables.append(
            CanarySchema.validate(
                pd.read_parquet(
                    inputs["results"] / "canary_augmented_ridge_site_onehot_esm2.parquet"
                )
            )
        )
    table = tables[0]
    assert len(table) == 2 * 3
    assert table["passed"].all() and table["informative"].all()
    assert (table["n_permutations"] == 6).all()
    # Each case has its own seeded generator: workers and order do not change the result.
    pd.testing.assert_frame_equal(tables[0], tables[1])


def test_compare_and_leaderboard(inputs: dict[str, Path], tmp_path: Path) -> None:
    train_all(inputs)
    pairs = (
        "[[augmented_ridge_site_onehot_esm2,zero_shot_esm2_score],"
        "[ridge_seq_struct,zero_shot_esm2_score]]"
    )
    code, output = run(
        "train", "compare",
        "--results-dir", str(inputs["results"]),
        "--reference", str(inputs["reference"]),
        "--out-dir", str(inputs["results"]),
        NO_TRACKING, f"train.comparisons={pairs}", "eval.bootstrap.n_resamples=200",
        "+data.structure_exceptions.C_TEST=no structure",
    )  # fmt: skip
    assert code == 0, output
    comparisons = ComparisonSchema.validate(
        read_results(inputs["results"] / "supervised_comparisons.parquet")
    )
    overall = comparisons[(comparisons["level"] == "overall")]
    structured = overall[overall["model_a"] == "ridge_seq_struct"]
    assert set(structured["n_assays"]) == {2}  # C_TEST has no structure: shared assays only
    assert set(overall["split"]) == {"random", "modulo", "contiguous"}
    summary = SummarySchema.validate(read_results(inputs["results"] / "supervised_summary.parquet"))
    assert set(summary["model"]) == {
        "augmented_ridge_site_onehot_esm2", "ridge_seq_struct", "zero_shot_esm2_score"
    }  # fmt: skip

    published = tmp_path / "published"
    published.mkdir()
    names = ["One-Hot Encodings", "ESM-1v + One-Hot Encodings"]
    pd.DataFrame(
        {
            "Model_name": names,
            **{
                f"Average_Spearman_fold_{s}_5": [0.5, 0.4]
                for s in ("random", "modulo", "contiguous")
            },
        }
    ).to_csv(published / "summary.csv", index=False)
    for scheme in ("random", "modulo", "contiguous"):
        pd.DataFrame(
            {
                "DMS_id": ["A_TEST", "B_TEST", "C_TEST"],
                names[0]: [0.1, 0.2, 0.3],
                names[1]: [0.3, 0.2, 0.1],
            }
        ).to_csv(published / f"level_{scheme}.csv", index=False)
    code, output = run(
        "train", "leaderboard",
        "--results-dir", str(inputs["results"]),
        "--reference", str(inputs["reference"]),
        "--out", str(inputs["results"] / "leaderboard.parquet"),
        f"tracking.dir={tmp_path / 'mlruns'}",
        f"train.leaderboard.summary={published / 'summary.csv'}",
        *(f"train.leaderboard.assay_level.{s}={published}/level_{s}.csv" for s in SCHEMES),
        "train.leaderboard.baselines={ridge_site_onehot:['One-Hot Encodings'],"
        "augmented_ridge_site_onehot_esm2:['ESM-1v + One-Hot Encodings']}",
    )  # fmt: skip
    assert code == 0, output
    board = SupervisedLeaderboardSchema.validate(
        pd.read_parquet(inputs["results"] / "leaderboard.parquet")
    )
    assert len(board) == 2 * 3
    assert (board["published"].isin([0.5, 0.4])).all()
    assert (board["n_shared"] == 3).all()


SMALL_LGBM = (
    "train.params.lgbm.n_trials=2",
    "train.params.lgbm.max_rounds=20",
    "train.params.lgbm.patience=5",
)


@pytest.mark.lightgbm
@pytest.mark.parametrize("model", ["lgbm", "lgbm_pca"])
def test_lightgbm_cross_validates_store_sets(inputs: dict[str, Path], model: str) -> None:
    assert folds(inputs)[0] == 0
    code, output = train(
        inputs, f"train.model={model}", "train.feature_set=seq_struct", *SMALL_LGBM, store=True
    )
    assert code == 0, output
    label = f"{model}_seq_struct"
    table = SupervisedMetricSchema.validate(read_results(inputs["results"] / f"cv_{label}.parquet"))
    assert set(table["assay_id"]) == {"A_TEST", "B_TEST"}
    chosen = SelectedHyperparameterSchema.validate(
        pd.read_parquet(inputs["results"] / f"cv_{label}_hyperparameters.parquet")
    )
    assert len(chosen) == 2 * 3 * 5 * 7  # assays x schemes x folds x (6 searched + rounds)


@pytest.mark.lightgbm
def test_shap_then_figure(inputs: dict[str, Path], tmp_path: Path) -> None:
    assert folds(inputs)[0] == 0
    code, output = run(
        "train", "shap",
        "--variants-dir", str(inputs["variants"]),
        "--folds-dir", str(inputs["folds"]),
        "--out-dir", str(inputs["results"]),
        "--store-dir", str(inputs["store"]),
        "--jobs", "1",
        NO_TRACKING, *SMALL_LGBM, "train.shap.assays=[A_TEST,B_TEST]",
    )  # fmt: skip
    assert code == 0, output
    table = ShapImportanceSchema.validate(
        pd.read_parquet(inputs["results"] / "shap_lgbm_seq_struct.parquet")
    )
    blocks = table[table["level"] == "block"]
    assert set(blocks["assay_id"]) == {"A_TEST", "B_TEST"}
    assert "esm2_650m:embedding" in set(blocks["name"]) and "structure" in set(blocks["name"])
    shares = blocks.groupby("assay_id")["share"].sum()
    assert ((shares - 1).abs() < 1e-9).all()

    code, output = run(
        "train", "figures",
        "--results-dir", str(inputs["results"]),
        "--out-dir", str(tmp_path / "figures"),
        "--reference", str(inputs["reference"]),
        NO_TRACKING, "train.shap.assays=[A_TEST,B_TEST]",
    )  # fmt: skip
    assert code == 0, output
    assert (tmp_path / "figures" / "shap_lgbm_seq_struct.svg").exists()
