"""Structure report: cross-checks between the outputs fail loudly; counts are right."""

import shutil
from collections.abc import Callable
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from typer.testing import CliRunner

from mutascope.cli import app
from mutascope.data.structure_features import FeatureParams, build_structure_features
from mutascope.data.structure_report import (
    StructureReportError,
    render_markdown,
    structure_report,
)
from mutascope.data.structures import StructureCheck
from mutascope.data.three_di import (
    RUN_FILE,
    FoldseekBinary,
    FoldseekConfig,
    build_three_di,
    check_foldseek,
)

CONF = Path(__file__).parents[2] / "conf"
PINNED = "941cd33ff0771cd2e3f144e3293e22a2b87e9fda"
PARAMS = FeatureParams(8.0, 1.4, 1000, 70.0)


def write_variants(root: Path, positions: dict[str, list[int]]) -> Path:
    """Variant partitions with only the columns the report reads."""
    out = root / "variants"
    for assay_id, pos in positions.items():
        part = out / f"assay_id={assay_id}"
        part.mkdir(parents=True)
        frame = pd.DataFrame({"position": pos})
        duckdb.from_df(frame).write_parquet(str(part / "data.parquet"))
    return out


def fixture_rows(seq: str) -> str:
    """Fake Foldseek output for the fixture: its real sequence, a constant 3Di."""
    return f"SR43C_ARATH.pdb_A\\t{seq}\\t{'D' * len(seq)}\\t0\\n"


@pytest.fixture
def outputs(
    tmp_path: Path,
    check: StructureCheck,
    fixture_seq: str,
    fake_foldseek: Callable[[str, str], Path],
) -> dict[str, Path]:
    features = tmp_path / "features"
    three_di = tmp_path / "three_di"
    build_structure_features(check, features, PARAMS)
    script = fake_foldseek(PINNED, fixture_rows(fixture_seq))
    found = check_foldseek(FoldseekConfig(str(script), PINNED, 1))
    build_three_di(check, three_di, found, threads=1)
    # WINDOW's target has 3 residues before and 2 after the structure: positions 0 and 52 have
    # no structure, 3 does.
    variants = write_variants(
        tmp_path, {"FULL": [0, 1, 47], "WINDOW": [0, 3, 52], "BROKEN": [5, 6]}
    )
    return {"features": features, "three_di": three_di, "variants": variants}


def build(check: StructureCheck, outputs: dict[str, Path]) -> pd.DataFrame:
    return structure_report(
        check,
        target_lengths={"FULL": 48, "WINDOW": 53, "BROKEN": 48},
        variants_dir=outputs["variants"],
        features_dir=outputs["features"],
        three_di_dir=outputs["three_di"],
        low_plddt_threshold=70.0,
    )


def test_report_counts(check: StructureCheck, outputs: dict[str, Path]) -> None:
    report = build(check, outputs).set_index("assay_id")

    assert report.loc["FULL", "status"] == "features"
    assert report.loc["FULL", "residues"] == 48
    assert (report.loc["FULL", "variants"], report.loc["FULL", "variants_with_structure"]) == (3, 3)
    assert (report.loc["WINDOW", "range_start"], report.loc["WINDOW", "range_end"]) == (4, 51)
    assert (report.loc["WINDOW", "variants"], report.loc["WINDOW", "variants_with_structure"]) == (
        3,
        1,
    )
    broken = report.loc["BROKEN"].to_dict()
    assert broken["status"] == "exception"
    assert pd.isna(broken["residues"])
    assert broken["variants_with_structure"] == 0
    assert broken["documented_reason"] == "documented natural variant"
    assert "differs from target_seq" in broken["observed_failure"]

    foldseek = FoldseekBinary.from_json((outputs["three_di"] / RUN_FILE).read_text())
    markdown = render_markdown(report.reset_index(), low_plddt_threshold=70.0, foldseek=foldseek)
    assert "| documented exceptions (no structure) | 1 |" in markdown
    assert "| WINDOW | SR43C_ARATH.pdb | 4-51 |" in markdown
    assert f"| sha256 | `{foldseek.sha256}` |" in markdown
    assert foldseek.path not in markdown  # no machine-specific paths in the report
    assert "helix" not in markdown  # no secondary structure (ADR 0006)


def test_exception_partition_fails(check: StructureCheck, outputs: dict[str, Path]) -> None:
    shutil.copytree(outputs["features"] / "assay_id=FULL", outputs["features"] / "assay_id=BROKEN")
    with pytest.raises(StructureReportError, match="structure exceptions"):
        build(check, outputs)


def test_missing_three_di_assay_fails(check: StructureCheck, outputs: dict[str, Path]) -> None:
    shutil.rmtree(outputs["three_di"] / "assay_id=WINDOW")
    with pytest.raises(StructureReportError, match=r"three_di: missing assays \['WINDOW'\]"):
        build(check, outputs)


def test_residue_disagreement_fails(check: StructureCheck, outputs: dict[str, Path]) -> None:
    part = outputs["three_di"] / "assay_id=FULL" / "data.parquet"
    frame = duckdb.read_parquet(str(part)).df()
    frame.loc[0, "residue"] = "W"
    duckdb.from_df(frame).write_parquet(str(part))
    with pytest.raises(StructureReportError, match=r"1 .* keys differ"):
        build(check, outputs)


def test_cli_round_trip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reference: Path,
    structures_dir: Path,
    fixture_seq: str,
    fake_foldseek: Callable[[str, str], Path],
) -> None:
    runner = CliRunner()
    exceptions = ["~data.structure_exceptions", "+data.structure_exceptions={BROKEN:'variant'}"]
    common = ["--reference", str(reference), "--structures-dir", str(structures_dir),
              "--config-dir", str(CONF)]  # fmt: skip
    features, three_di = tmp_path / "f", tmp_path / "t"

    result = runner.invoke(
        app, ["data", "structure-features", *common, "--out-dir", str(features), *exceptions]
    )
    assert result.exit_code == 0, result.output
    assert "2 assays, 96 residues" in result.stdout
    assert "1 documented exceptions skipped" in result.stdout

    # Foldseek is checked before anything else: with no binary, the run fails on that alone,
    # even though this config would also fail the structure mapping.
    monkeypatch.delenv("FOLDSEEK_BIN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    result = runner.invoke(
        app,
        ["data", "foldseek-3di", *common, "--out-dir", str(three_di),
         "data.structure_exceptions=null"],
    )  # fmt: skip
    assert result.exit_code == 1
    assert "FOLDSEEK_BIN is not set and `foldseek` is not on PATH" in result.stderr
    assert "BROKEN" not in result.stderr

    script = fake_foldseek(PINNED, fixture_rows(fixture_seq))
    monkeypatch.setenv("FOLDSEEK_BIN", str(script))
    result = runner.invoke(
        app, ["data", "foldseek-3di", *common, "--out-dir", str(three_di), *exceptions]
    )
    assert result.exit_code == 0, result.output
    assert f"foldseek: {script.resolve()} (via FOLDSEEK_BIN" in result.stdout

    variants = write_variants(tmp_path, {"FULL": [0], "WINDOW": [3], "BROKEN": [5]})
    result = runner.invoke(
        app,
        ["data", "structure-report", *common, "--variants-dir", str(variants),
         "--features-dir", str(features), "--three-di-dir", str(three_di),
         "--report-dir", str(tmp_path / "report"), *exceptions],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    report_md = (tmp_path / "report" / "structure_report.md").read_text()
    assert f"| version | `{PINNED}` |" in report_md
    assert str(tmp_path) not in report_md  # the binary's absolute path stays out
    assert "| found via | FOLDSEEK_BIN |" in report_md

    # Without the documented exception, the BROKEN assay fails the mapping and nothing runs.
    result = runner.invoke(
        app,
        ["data", "structure-features", *common, "--out-dir", str(tmp_path / "g"),
         "data.structure_exceptions=null"],
    )  # fmt: skip
    assert result.exit_code == 1
    assert "BROKEN" in result.stderr
