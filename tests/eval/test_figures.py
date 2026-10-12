"""Benchmark figures: drawn from results files only, and reproducible byte for byte."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from mutascope.cli import app
from mutascope.data.schemas import FUNCTION_TYPES
from mutascope.eval.benchmark import BootstrapParams, read_results, summarise, write_results
from mutascope.eval.compare import paired_bootstrap_diff, summarise_shared
from mutascope.eval.figures import (
    FigureContext,
    FigureDataError,
    difference_figure,
    function_type_figure,
    model_size_figure,
    save_figure,
)
from mutascope.eval.metrics import METRICS
from mutascope.eval.schemas import ComparisonSchema, SummarySchema
from mutascope.utils.config import load_config
from mutascope.utils.io import write_table

CONF = Path(__file__).parents[2] / "conf"
BOOTSTRAP = BootstrapParams(n_resamples=200, confidence=0.95)
ENSEMBLE = "ensemble_esm2_650m_saprot_650m_af2"
NO_STRUCTURE = ["A00", "A01"]


def _reference() -> pd.DataFrame:
    """20 assays of 10 proteins, two per protein, covering the five function types."""
    return pd.DataFrame(
        {
            "DMS_id": [f"A{i:02d}" for i in range(20)],
            "UniProt_ID": [f"P{i // 2}" for i in range(20)],
            "coarse_selection_type": [FUNCTION_TYPES[(i // 2) % 5] for i in range(20)],
        }
    )


def _results(model: str, reference: pd.DataFrame, values: np.ndarray) -> pd.DataFrame:
    frames = [
        pd.DataFrame(
            {"model": model, "assay_id": reference["DMS_id"], "metric": m, "value": values}
        )
        for m in METRICS
    ]
    table = pd.concat(frames, ignore_index=True)
    table.insert(2, "split", pd.Series(pd.NA, index=table.index, dtype="string"))
    table.insert(3, "fold", pd.Series(pd.NA, index=table.index, dtype="Int64"))
    return table


@pytest.fixture
def results_dir(tmp_path: Path) -> Path:
    """Results files for every score set and pair the real ``eval.figures`` config names.

    Built by the same functions the CLI uses; the structure-aware score sets lack two assays.
    """
    cfg = load_config(CONF).eval
    reference = _reference()
    rng = np.random.default_rng(0)
    base = rng.uniform(0.1, 0.6, size=len(reference))
    labels = sorted({str(label) for pair in cfg.comparisons for label in pair})
    per_assay: dict[str, pd.DataFrame] = {}
    for i, label in enumerate(labels):
        frame = _results(label, reference, base + 0.01 * i + rng.normal(scale=0.01, size=20))
        if label in (ENSEMBLE, "saprot_650m_af2"):
            frame = frame[~frame["assay_id"].isin(NO_STRUCTURE)]
        per_assay[label] = frame
        summary = summarise(frame, reference, BOOTSTRAP, np.random.default_rng(0))
        write_results(frame, summary, tmp_path, f"zero_shot_{label}_single")
    pairs = [
        paired_bootstrap_diff(
            per_assay[str(a)],
            per_assay[str(b)],
            reference,
            BOOTSTRAP,
            np.random.default_rng(0),
            may_be_absent=NO_STRUCTURE,
        )
        for a, b in cfg.comparisons
    ]
    write_table(
        pd.concat(pairs, ignore_index=True), ComparisonSchema, tmp_path / "comparisons.parquet"
    )
    shared = summarise_shared(
        {str(label): per_assay[str(label)] for label in cfg.shared_summary},
        reference,
        BOOTSTRAP,
        seed=0,
        may_be_absent=NO_STRUCTURE,
    )
    write_table(shared, SummarySchema, tmp_path / "shared_summary.parquet")
    return tmp_path


def _cli(results_dir: Path, out: Path, *overrides: str) -> list[str]:
    return [
        "eval", "figures", "--config-dir", str(CONF), "--results-dir", str(results_dir),
        "--comparisons", str(results_dir / "comparisons.parquet"),
        "--shared-summary", str(results_dir / "shared_summary.parquet"),
        "--out-dir", str(out), *overrides,
    ]  # fmt: skip


def test_cli_writes_every_figure_in_every_format_and_redraws_identically(
    results_dir: Path, tmp_path: Path
) -> None:
    first = CliRunner().invoke(app, _cli(results_dir, tmp_path / "one"))
    assert first.exit_code == 0, first.output
    names = ["model_size_vs_spearman", "function_type_spearman", "ensemble_gain", "fast_vs_masked"]
    expected = {f"{name}.{fmt}" for name in names for fmt in ("svg", "png")}
    assert {p.name for p in (tmp_path / "one").iterdir()} == expected
    second = CliRunner().invoke(app, _cli(results_dir, tmp_path / "two"))
    assert second.exit_code == 0, second.output
    for name in expected:
        assert (tmp_path / "one" / name).read_bytes() == (tmp_path / "two" / name).read_bytes()
    svg = (tmp_path / "one" / "ensemble_gain.svg").read_text()
    assert "<svg" in svg and "<dc:date>" not in svg
    # What the pre-commit hooks would change, they must find already done.
    assert svg.endswith(">\n") and not any(line != line.rstrip() for line in svg.splitlines())
    assert (tmp_path / "one" / "ensemble_gain.png").read_bytes().startswith(b"\x89PNG")


def test_cli_fails_when_a_figure_needs_a_pair_that_was_not_compared(
    results_dir: Path, tmp_path: Path
) -> None:
    """The figure code never computes a difference itself: no row, no figure."""
    table = read_results(results_dir / "comparisons.parquet")
    kept = table[~((table["model_a"] == "esm2_650m") & (table["model_b"] == "esm2_150m"))]
    write_table(kept, ComparisonSchema, results_dir / "comparisons.parquet")
    result = CliRunner().invoke(app, _cli(results_dir, tmp_path / "out"))
    assert result.exit_code == 1
    assert "no paired comparison esm2_650m - esm2_150m" in result.output
    assert "eval.comparisons" in result.output


def test_cli_checks_summaries_and_the_fast_pairs(results_dir: Path, tmp_path: Path) -> None:
    swapped = "eval.figures.fast_vs_masked=[[esm2_35m_wt,esm2_35m]]"
    result = CliRunner().invoke(app, _cli(results_dir, tmp_path / "out", swapped))
    assert result.exit_code == 1 and "not [masked, wild-type] pairs" in result.output
    (results_dir / "zero_shot_esm2_35m_single_summary.parquet").unlink()
    result = CliRunner().invoke(app, _cli(results_dir, tmp_path / "out"))
    assert result.exit_code == 1 and "no summary for ['esm2_35m']" in result.output


def _context() -> FigureContext:
    names = {"a": "Model A", "b": "Model B", "c": "Model C", "d": "Model D"}
    return FigureContext(metric="spearman", names=names, dataset="Toy v0", confidence=0.95)


def test_figures_state_the_assay_count_and_refuse_mixed_counts() -> None:
    reference = _reference()
    rng = np.random.default_rng(1)
    values = {m: rng.uniform(0.2, 0.6, size=20) for m in "abc"}
    results = {m: _results(m, reference, v) for m, v in values.items()}
    summaries = pd.concat(
        [summarise(r, reference, BOOTSTRAP, np.random.default_rng(0)) for r in results.values()],
        ignore_index=True,
    )
    pairs = pd.concat(
        [
            paired_bootstrap_diff(
                results[a], results[b], reference, BOOTSTRAP, np.random.default_rng(0)
            )
            for a, b in (("b", "a"), ("c", "b"))
        ],
        ignore_index=True,
    )
    context = _context()

    def texts(figure: object) -> str:
        from matplotlib.figure import Figure

        assert isinstance(figure, Figure)
        found = [t.get_text() for t in figure.texts]
        for ax in figure.axes:
            found += [t.get_text() for t in ax.texts]
            found += [t.get_text() for t in ax.get_xticklabels() + ax.get_yticklabels()]
        return "\n".join(found)

    size = model_size_figure(summaries, pairs, {"a": 1, "b": 10, "c": 100}, context)
    assert "Toy v0, 20 assays" in texts(size) and "Model B\n10M parameters" in texts(size)
    bars = function_type_figure(summaries, ["a", "b", "c"], context)
    assert "the 20 assays every score set has" in texts(bars) and "Stability\nn = 4" in texts(bars)
    gain = difference_figure(
        pairs, [("b", "a")], context, title="T", panel_titles=["B - A"], setting="single mutants"
    )
    assert "Overall (n = 20)" in texts(gain)

    # A score set summarised on fewer assays cannot share a figure with the others.
    fewer = summarise(
        results["c"][results["c"]["assay_id"] != "A00"],
        reference,
        BOOTSTRAP,
        np.random.default_rng(0),
    )
    mixed = pd.concat([summaries[summaries["model"] != "c"], fewer], ignore_index=True)
    with pytest.raises(FigureDataError, match="different assay counts"):
        function_type_figure(mixed, ["a", "b", "c"], context)
    with pytest.raises(FigureDataError, match="different assay counts"):
        model_size_figure(mixed, pairs, {"a": 1, "b": 10, "c": 100}, context)
    with pytest.raises(FigureDataError, match="no paired comparison a - b"):
        difference_figure(pairs, [("a", "b")], context, title="T", panel_titles=["x"], setting="s")
    with pytest.raises(FigureDataError, match="no display name for 'z'"):
        context.name("z")
    with pytest.raises(FigureDataError, match="at most 3"):
        function_type_figure(summaries, ["a", "b", "c", "d"], context)


def test_an_unknown_format_is_refused(tmp_path: Path) -> None:
    from matplotlib.figure import Figure

    with pytest.raises(FigureDataError, match="unsupported figure format 'pdf'"):
        save_figure(Figure(), tmp_path, "x", ["pdf"], dpi=50)
