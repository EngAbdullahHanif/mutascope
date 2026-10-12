"""Per-assay tables, summaries and the results files, on the toy release."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from structlog.testing import capture_logs

from mutascope.eval.benchmark import (
    BootstrapParams,
    assay_metrics_table,
    read_results,
    summarise,
    summary_metrics,
    write_results,
)
from mutascope.eval.metrics import METRICS
from mutascope.eval.proteingym import RowSet, load_precomputed_scores

MODELS = {"ESM2_650M": "proteingym:ESM2_650M", "SaProt_650M_AF2": "proteingym:SaProt_650M_AF2"}
BOOTSTRAP = BootstrapParams(n_resamples=500, confidence=0.95)


def _per_assay(release_dir: Path, rows: RowSet) -> pd.DataFrame:
    variants = load_precomputed_scores(release_dir / "scores", list(MODELS), rows)
    return assay_metrics_table(variants, MODELS, top_percent=10)


def test_long_format_has_one_row_per_model_assay_metric(release_dir: Path) -> None:
    table = _per_assay(release_dir, "all")
    assert list(table.columns) == ["model", "assay_id", "split", "fold", "metric", "value"]
    assert len(table) == 2 * 3 * len(METRICS)
    assert table["split"].isna().all() and table["fold"].isna().all()


def test_undefined_metrics_are_nan_and_logged(release_dir: Path) -> None:
    with capture_logs() as logs:
        table = _per_assay(release_dir, "single")
    undefined = table[table["value"].isna()]
    assert set(undefined["assay_id"]) == {"B1"}
    assert sorted(set(undefined["metric"])) == ["auc", "mcc"]
    warnings = [e for e in logs if e["event"] == "undefined metric"]
    assert len(warnings) == 4  # 2 models x {auc, mcc}
    assert all(w["count"] == 1 and w["assays"] == ["B1"] for w in warnings)
    total = next(e for e in logs if e["event"] == "undefined metric values")
    assert total["count"] == 4


def test_all_rows_include_the_multi_mutant(release_dir: Path) -> None:
    """B1's multi-mutant has label 0, so on all rows its AUC is defined."""
    table = _per_assay(release_dir, "all")
    assert not table["value"].isna().any()


def test_summary_counts_only_defined_assays(release_dir: Path) -> None:
    table = _per_assay(release_dir, "single")
    reference = pd.read_csv(release_dir / "reference.csv")
    summary = summarise(table, reference, BOOTSTRAP, np.random.default_rng(0))
    auc = summary[(summary["metric"] == "auc") & (summary["model"] == MODELS["ESM2_650M"])]
    overall = auc[auc["level"] == "overall"].iloc[0]
    binding = auc[auc["group"] == "Binding"].iloc[0]
    assert overall["n_assays"] == 2
    assert np.isnan(binding["value"]) and np.isnan(binding["ci_low"])
    stability = auc[auc["group"] == "Stability"].iloc[0]
    assert overall["value"] == pytest.approx(stability["value"])  # Binding is skipped


def test_summary_rejects_fold_level_rows(release_dir: Path) -> None:
    table = _per_assay(release_dir, "all")
    doubled = pd.concat([table, table], ignore_index=True)
    reference = pd.read_csv(release_dir / "reference.csv")
    with pytest.raises(ValueError, match="average folds"):
        summarise(doubled, reference, BOOTSTRAP, np.random.default_rng(0))


def test_results_round_trip(release_dir: Path, tmp_path: Path) -> None:
    table = _per_assay(release_dir, "single")
    reference = pd.read_csv(release_dir / "reference.csv")
    summary = summarise(table, reference, BOOTSTRAP, np.random.default_rng(0))
    per_assay_path, summary_path = write_results(table, summary, tmp_path / "out", "toy")
    assert per_assay_path.name == "toy.parquet"
    assert summary_path.name == "toy_summary.parquet"
    pd.testing.assert_frame_equal(read_results(per_assay_path), table)
    pd.testing.assert_frame_equal(read_results(summary_path), summary)


def test_missing_score_column_fails(release_dir: Path) -> None:
    variants = load_precomputed_scores(release_dir / "scores", ["ESM2_650M"], "all")
    with pytest.raises(KeyError, match="SaProt_650M_AF2"):
        assay_metrics_table(variants, MODELS, top_percent=10)


def test_summary_metrics_are_the_summary_rows_of_one_model(release_dir: Path) -> None:
    """Flat names for experiment tracking, values copied from the summary (NaN included)."""
    reference = pd.read_csv(release_dir / "reference.csv")
    summary = summarise(
        _per_assay(release_dir, "single"), reference, BOOTSTRAP, np.random.default_rng(0)
    )
    metrics = summary_metrics(summary, "proteingym:ESM2_650M")

    suffixes = ("", "_ci_low", "_ci_high", "/Binding", "/Stability")
    assert set(metrics) == {m + suffix for m in METRICS for suffix in suffixes}
    mine = summary[summary["model"] == "proteingym:ESM2_650M"]
    overall = mine[(mine["level"] == "overall") & (mine["metric"] == "spearman")]
    assert metrics["spearman"] == overall["value"].item()
    assert metrics["spearman_ci_low"] == overall["ci_low"].item()
    assert metrics["spearman_ci_low"] <= metrics["spearman"] <= metrics["spearman_ci_high"]
    stability = mine[(mine["group"] == "Stability") & (mine["metric"] == "ndcg")]
    assert metrics["ndcg/Stability"] == stability["value"].item()
    assert np.isnan(metrics["auc/Binding"])  # B1's single mutants all have label 1
    with pytest.raises(ValueError, match="no zero-shot summary rows"):
        summary_metrics(summary, "esm2_650m")
