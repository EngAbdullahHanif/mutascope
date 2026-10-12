"""``mutascope eval precomputed`` and ``compare-leaderboard`` end to end on the toy release."""

from pathlib import Path

from typer.testing import CliRunner

from mutascope.cli import app
from mutascope.eval.benchmark import read_results

CONF = Path(__file__).parents[2] / "conf"


def _overrides(release_dir: Path) -> list[str]:
    return [
        "eval.bootstrap.n_resamples=200",
        f"eval.published.summary={release_dir / 'summary.csv'}",
        f"eval.published.assay_level={release_dir / 'assay_level.csv'}",
    ]


def test_precomputed_then_compare(release_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "results"
    args = ["eval", "precomputed", "--config-dir", str(CONF)]
    args += ["--scores-dir", str(release_dir / "scores")]
    args += ["--reference", str(release_dir / "reference.csv"), "--rows", "all"]
    result = CliRunner().invoke(app, [*args, "--out-dir", str(out), *_overrides(release_dir)])
    assert result.exit_code == 0, result.output
    assert "3 assays, 93 rows, 2 models" in result.output

    per_assay = read_results(out / "proteingym_precomputed_all.parquet")
    assert set(per_assay["model"]) == {"proteingym:ESM2_650M", "proteingym:SaProt_650M_AF2"}
    assert (out / "proteingym_precomputed_all_summary.parquet").exists()

    compare = ["eval", "compare-leaderboard", "--config-dir", str(CONF)]
    compare += ["--results", str(out / "proteingym_precomputed_all.parquet")]
    result = CliRunner().invoke(app, [*compare, *_overrides(release_dir)])
    assert result.exit_code == 0, result.output
    assert "published 0.400" in result.output  # ESM2 overall from the toy summary
    assert "ESM2_650M: 3 assays, max |ours - published| per assay" in result.output


def test_unknown_row_set_is_rejected(release_dir: Path, tmp_path: Path) -> None:
    args = ["eval", "precomputed", "--config-dir", str(CONF)]
    args += ["--scores-dir", str(release_dir / "scores")]
    args += ["--reference", str(release_dir / "reference.csv"), "--rows", "singles"]
    result = CliRunner().invoke(app, [*args, "--out-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "--rows must be 'all' or 'single'" in result.output
