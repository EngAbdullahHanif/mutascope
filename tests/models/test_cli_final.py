"""``train final`` end to end on synthetic assays (ADR 0025): eligibility, a complete stamped
bundle that reproduces ``train cv``, and a failed check that leaves no bundle."""

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
    FinalCheckSchema,
    FinalLandscapeSchema,
    FinalManifestSchema,
    RidgeCoefficientSchema,
    RidgeFitSchema,
)

CONF = Path(__file__).parents[2] / "conf"
NO_TRACKING = "tracking.enabled=false"
SMALL = "train.params.ridge.alphas.num=5"


def run(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, [*args, "--config-dir", str(CONF)], catch_exceptions=False)
    return result.exit_code, result.output


@pytest.fixture
def inputs(tmp_path: Path) -> dict[str, Path]:
    # A: 40 x 8 = 320 variants (eligible); B: 40 x 6 = 240 (below the minimum of 300).
    assays = [
        synthetic_assay("A_TEST", length=40, per_position=8, seed=0),
        synthetic_assay("B_TEST", length=40, per_position=6, seed=1),
    ]
    reference = tmp_path / "DMS_substitutions.csv"
    pd.DataFrame(
        {
            "DMS_id": ["A_TEST", "B_TEST"],
            "UniProt_ID": ["P1", "P2"],
            "coarse_selection_type": ["Stability", "Activity"],
            "DMS_number_single_mutants": [len(a) for a in assays],
            "target_seq": ["".join(a.groupby("position")["wt_aa"].first()) for a in assays],
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
        "calibration": tmp_path / "calibration",
        "bundle": tmp_path / "bundle",
    }
    data = [
        "--variants-dir", str(paths["variants"]),
        "--folds-dir", str(paths["folds"]),
        "--store-dir", str(paths["store"]),
        "--jobs", "1",
    ]  # fmt: skip
    steps = [
        ["data", "folds", "--variants-dir", str(paths["variants"]),
         "--folds-dir", str(paths["cv"]), "--out-dir", str(paths["folds"])],
        ["train", "cv", *data, "--reference", str(reference), "--out-dir", str(paths["results"]),
         "train.model=ridge", "train.feature_set=seq_struct", NO_TRACKING, SMALL],
        ["train", "calibrate", *data, "--out-dir", str(paths["calibration"]), NO_TRACKING, SMALL],
    ]  # fmt: skip
    for step in steps:
        code, output = run(*step)
        assert code == 0, output
    return paths


def final(inputs: dict[str, Path], *extra: str) -> tuple[int, str]:
    return run(
        "train", "final",
        "--variants-dir", str(inputs["variants"]),
        "--folds-dir", str(inputs["folds"]),
        "--store-dir", str(inputs["store"]),
        "--reference", str(inputs["reference"]),
        "--results-dir", str(inputs["results"]),
        "--out-dir", str(inputs["bundle"]),
        "--jobs", "1",
        f"train.uncertainty.calibration_dir={inputs['calibration']}",
        NO_TRACKING, SMALL, *extra,
    )  # fmt: skip


def test_final_writes_a_complete_stamped_bundle(inputs: dict[str, Path]) -> None:
    code, output = final(inputs)
    assert code == 0, output
    assert "1 eligible assays, 760 substitutions, 1 assays default to zero-shot" in output
    bundle = inputs["bundle"] / "ridge_seq_struct"
    manifest = FinalManifestSchema.validate(pd.read_parquet(bundle / "manifest.parquet"))
    by_assay = manifest.set_index("assay_id")
    assert by_assay.loc["B_TEST", "reason"] == "fewer than 300 measured variants (240)"
    assert by_assay.loc["B_TEST", "default_model"] == "saprot_650m_af2"
    landscape = pd.read_parquet(bundle / "assay_id=A_TEST" / "data.parquet")
    landscape.insert(0, "assay_id", "A_TEST")
    FinalLandscapeSchema.validate(landscape)
    assert len(landscape) == 19 * 40 and landscape["measured"].sum() == 320
    assert landscape["code_version"].nunique() == 1
    assert (landscape["config_sha256"] == by_assay.loc["A_TEST", "config_sha256"]).all()
    RidgeFitSchema.validate(pd.read_parquet(bundle / "fits.parquet"))
    RidgeCoefficientSchema.validate(pd.read_parquet(bundle / "coefficients.parquet"))
    checks = FinalCheckSchema.validate(pd.read_parquet(inputs["results"] / "final_checks.parquet"))
    for scheme in ("random", "modulo", "contiguous"):
        diff = (checks[f"oof_spearman_{scheme}"] - checks[f"cv_spearman_{scheme}"]).abs()
        assert float(diff.max()) < 1e-9
    assert float(checks["calibration_max_abs_diff"].max()) == 0.0


def test_a_failed_check_leaves_no_bundle(inputs: dict[str, Path]) -> None:
    # Calibration rows from another penalty grid: the refit no longer reproduces them.
    code, output = final(inputs, "train.params.ridge.alphas.num=7")
    assert code == 1
    assert "CV+ refit differs from the saved calibration" in output
    assert "was not replaced" in output
    assert not (inputs["bundle"] / "ridge_seq_struct").exists()
