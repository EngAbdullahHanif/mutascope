"""Release check: bit-identical comparison of a rerun with the released files."""

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from mutascope.cli import app
from mutascope.models.release import compare_json, compare_tables, compare_trees

STAMPS = ("code_version", "config_sha256")
LABEL = "ridge_seq_struct"  # train.final in conf/train/default.yaml


def table() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "position": np.array([0, 0, 1], dtype=np.int64),
            "mt_aa": ["A", "C", "A"],
            "prediction": [0.5, -1.25, np.nan],
            "measured": [True, False, True],
            "buried_lower": pd.array([True, None, False], dtype="boolean"),
            "code_version": ["abc", "abc", "abc"],
            "config_sha256": ["1", "1", "1"],
        }
    )


def test_identical_tables_and_changed_stamps_pass() -> None:
    other = table().assign(code_version="def", config_sha256="2")
    assert compare_tables(table(), table(), STAMPS) == ""
    assert compare_tables(other, table(), STAMPS) == ""


def test_one_ulp_is_a_difference() -> None:
    other = table()
    other.loc[1, "prediction"] = np.nextafter(-1.25, 0.0)
    reason = compare_tables(other, table(), STAMPS)
    assert reason.startswith("prediction: 1 of 3 values differ")
    assert "max |diff|" in reason


def test_negative_zero_differs_from_zero() -> None:
    a, b = table(), table()
    a.loc[0, "prediction"], b.loc[0, "prediction"] = 0.0, -0.0
    assert compare_tables(a, b, STAMPS).startswith("prediction:")


def _dtype(t: pd.DataFrame) -> pd.DataFrame:
    return t.astype({"position": np.int32})


def _order(t: pd.DataFrame) -> pd.DataFrame:
    return t[list(reversed(t.columns))]


def _rows(t: pd.DataFrame) -> pd.DataFrame:
    return t.iloc[:2]


def _string(t: pd.DataFrame) -> pd.DataFrame:
    return t.assign(mt_aa=["A", "D", "A"])


def _nullable(t: pd.DataFrame) -> pd.DataFrame:
    return t.assign(buried_lower=pd.array([True, True, False], dtype="boolean"))


def _bool(t: pd.DataFrame) -> pd.DataFrame:
    return t.assign(measured=[True, True, True])


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (_dtype, "position: dtype"),
        (_order, "columns"),
        (_rows, "2 rows != 3"),
        (_string, "mt_aa: 1 of 3 values differ"),
        (_nullable, "buried_lower: 1 of 3 values differ"),
        (_bool, "measured: 1 of 3 values differ"),
    ],
)
def test_differences_are_named(
    change: Callable[[pd.DataFrame], pd.DataFrame], expected: str
) -> None:
    assert expected in compare_tables(change(table()), table(), STAMPS)


def test_json_ignores_only_stamp_keys() -> None:
    released = {"code_version": "a", "config_sha256": "b", "assays": 198}
    assert compare_json(released | {"code_version": "z"}, released, STAMPS) == ""
    assert "assays" in compare_json(released | {"assays": 197}, released, STAMPS)
    assert "keys" in compare_json({"assays": 198}, released, STAMPS)


def write_bundle(root: Path, frame: pd.DataFrame) -> None:
    (root / "assay_id=A").mkdir(parents=True)
    frame.to_parquet(root / "assay_id=A" / "data.parquet")
    (root / "version.json").write_text(json.dumps({"code_version": "abc", "assays": 1}))


def test_trees_report_missing_extra_and_changed_files(tmp_path: Path) -> None:
    released, rerun = tmp_path / "released", tmp_path / "rerun"
    write_bundle(released, table())
    write_bundle(rerun, table().assign(code_version="def"))
    (rerun / ".DS_Store").write_text("")
    assert compare_trees(rerun, released, STAMPS) == []

    (rerun / "extra.parquet").write_bytes(b"")
    (released / "version.json").rename(released / "old.json")
    lines = compare_trees(rerun, released, STAMPS)
    assert any("old.json: missing from the rerun" in line for line in lines)
    assert any("extra.parquet: not in the release" in line for line in lines)
    assert compare_trees(rerun, tmp_path / "nothing", STAMPS)[0].endswith(
        "no released directory to compare with"
    )


def release_layout(root: Path) -> dict[str, Path]:
    """Results, calibration and bundle directories as the pipeline and the release lay them out."""
    dirs = {name: root / name for name in ("results", "calibration", "bundle")}
    dirs["results"].mkdir(parents=True)
    for name in (
        f"cv_{LABEL}.parquet",
        f"cv_{LABEL}_hyperparameters.parquet",
        "final_checks.parquet",
        "final_manifest.parquet",
    ):
        table().to_parquet(dirs["results"] / name)
    (dirs["calibration"] / LABEL).mkdir(parents=True)
    table().to_parquet(dirs["calibration"] / LABEL / "A.parquet")
    write_bundle(dirs["bundle"] / LABEL, table())
    return dirs


def check(rerun: dict[str, Path], released: dict[str, Path]) -> list[str]:
    return [
        "train",
        "check-release",
        "--bundle-dir",
        str(rerun["bundle"]),
        "--results-dir",
        str(rerun["results"]),
        "--calibration-dir",
        str(rerun["calibration"]),
        "--released-bundle-dir",
        str(released["bundle"]),
        "--released-results-dir",
        str(released["results"]),
        "--released-calibration-dir",
        str(released["calibration"]),
    ]


def test_cli_passes_a_rerun_that_differs_only_in_stamps(tmp_path: Path) -> None:
    released = release_layout(tmp_path / "released")
    rerun = release_layout(tmp_path / "rerun")
    restamped = table().assign(code_version="def", config_sha256="2")
    restamped.to_parquet(rerun["results"] / "final_manifest.parquet")
    restamped.to_parquet(rerun["bundle"] / LABEL / "assay_id=A" / "data.parquet")

    result = CliRunner().invoke(app, check(rerun, released))
    assert result.exit_code == 0, result.output
    assert "PASS: ridge_seq_struct reproduces the release bit for bit" in result.stdout
    assert "4 results files, 1 calibration files, 2 bundle files" in result.stdout


def test_cli_fails_on_one_changed_value_or_a_missing_file(tmp_path: Path) -> None:
    released = release_layout(tmp_path / "released")
    rerun = release_layout(tmp_path / "rerun")
    changed = table()
    changed.loc[0, "prediction"] = 0.5000000001
    changed.to_parquet(rerun["bundle"] / LABEL / "assay_id=A" / "data.parquet")
    (rerun["results"] / "final_checks.parquet").unlink()
    shutil.rmtree(rerun["calibration"] / LABEL)
    (rerun["calibration"] / LABEL).mkdir()

    result = CliRunner().invoke(app, check(rerun, released))
    assert result.exit_code == 1
    assert "bundle: assay_id=A/data.parquet: prediction: 1 of 3 values differ" in result.stderr
    assert "final_checks.parquet: missing" in result.stderr
    assert "calibration:" in result.stderr and "A.parquet: missing from the rerun" in result.stderr
    assert "FAIL: 3 differences from the release" in result.stderr
