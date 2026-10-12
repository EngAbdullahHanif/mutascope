"""Scoring every assay: what is requested, what is written, and resuming from the cache.

The runner is tested with a small scorer that keeps its "cache" in a dictionary shared between
instances, like the disk cache is shared between processes. The CLI test runs the real
``esm2_t6_8M`` checkpoint twice against one cache directory.
"""

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from mlflow.tracking import MlflowClient
from typer.testing import CliRunner

from mutascope.alphabet import AA_ORDER
from mutascope.cli import app
from mutascope.eval.benchmark import read_results
from mutascope.eval.region import scoring_regions
from mutascope.eval.zero_shot import load_scores, run_label, score_assays, with_labels
from mutascope.scoring.base import FloatArray, landscape_from_log_probs, resolve_positions
from mutascope.utils.tracking import TrackingSettings

CONF = Path(__file__).parents[2] / "conf"
TARGET = "MKVAR"


class _CachingScorer:
    """Row ``p`` of a sequence is ``p`` in every column but alanine's, which is ``p + 0.5``."""

    def __init__(
        self, cache: dict[tuple[str, str], FloatArray], dies_after: int | None = None
    ) -> None:
        self.cache = cache
        self.dies_after = dies_after
        """Number of computations after which the next call "crashes"."""
        self.computed: list[tuple[str, str, list[int]]] = []

    @property
    def model_id(self) -> str:
        return "toy"

    @property
    def positions_computed(self) -> int:
        return sum(len(positions) for _, _, positions in self.computed)

    def landscape(
        self,
        seq: str,
        struct_tokens: str | None = None,
        *,
        fast: bool = False,
        region: str = "full",
        positions: Sequence[int] | None = None,
    ) -> FloatArray:
        if self.dies_after is not None and len(self.computed) >= self.dies_after:
            raise KeyboardInterrupt
        entry = self.cache.setdefault(
            (seq, region), np.full((len(seq), 20), np.nan, dtype=np.float32)
        )
        missing = [p for p in resolve_positions(positions, len(seq)) if np.isnan(entry[p, 0])]
        if missing:
            self.computed.append((seq, region, missing))
            for p in missing:
                entry[p] = p
                entry[p, AA_ORDER.index("A")] = p + 0.5
        return landscape_from_log_probs(entry, seq)


@pytest.fixture
def reference(processed_root: Path) -> pd.DataFrame:
    """The toy reference (assays A and B, both with target MKVAR), MSA covering the target."""
    table = pd.read_csv(processed_root / "DMS_substitutions.csv")
    table["MSA_start"], table["MSA_end"] = 1, table["target_seq"].str.len()
    return table


def test_scores_only_measured_positions_and_writes_every_variant(
    processed_root: Path, reference: pd.DataFrame, tmp_path: Path
) -> None:
    """A measures positions 0-3 of MKVAR, B positions 0 and 4. They share one target, so B
    finds position 0 in the cache and only position 4 is new."""
    scorer = _CachingScorer({})
    out = tmp_path / "scores"
    regions = scoring_regions(reference, "benchmark", [])
    report = score_assays(
        scorer, reference, processed_root / "processed" / "variants", regions, out
    )

    assert scorer.computed == [(TARGET, "full", [0, 1, 2, 3]), (TARGET, "full", [4])]
    assert report["assay_id"].tolist() == ["A", "B"]
    assert report["variants"].tolist() == [4, 2]
    assert report["positions"].tolist() == [4, 2]
    assert report["positions_computed"].tolist() == [4, 1]
    assert (report["region"] == "full").all() and (report["region_length"] == 5).all()

    scores = load_scores(out)
    assert len(scores) == 6 and set(scores["model"]) == {"toy"}
    by_key = scores.set_index(["assay_id", "mutant"])["score"]
    # log P(mt) - log P(wt) at the mutated position: +0.5 for X -> A, -0.5 for A -> X, else 0.
    assert by_key[("A", "M1A")] == 0.5 and by_key[("A", "A4C")] == -0.5
    assert by_key[("A", "K2C")] == 0.0 and by_key[("B", "R5W")] == 0.0
    assert not (tmp_path / "scores.tmp").exists()


def test_a_rerun_after_a_crash_reads_finished_assays_from_the_cache(
    processed_root: Path, reference: pd.DataFrame, tmp_path: Path
) -> None:
    """The first run dies while scoring B (after A). Its output directory was never created,
    but the cache holds A. The rerun computes only B's missing position and writes both."""
    variants = processed_root / "processed" / "variants"
    cache: dict[tuple[str, str], FloatArray] = {}
    out = tmp_path / "scores"
    regions = scoring_regions(reference, "benchmark", [])

    with pytest.raises(KeyboardInterrupt):
        score_assays(_CachingScorer(cache, dies_after=1), reference, variants, regions, out)
    assert not out.exists()

    resumed = _CachingScorer(cache)
    report = score_assays(resumed, reference, variants, regions, out)
    assert resumed.computed == [(TARGET, "full", [4])]
    assert report["positions_computed"].tolist() == [0, 1]
    assert len(load_scores(out)) == 6

    again = _CachingScorer(cache)
    report = score_assays(again, reference, variants, regions, out)
    assert again.computed == [] and report["positions_computed"].tolist() == [0, 0]


def test_an_assay_without_variants_fails(
    processed_root: Path, reference: pd.DataFrame, tmp_path: Path
) -> None:
    extra = reference.iloc[[0]].assign(DMS_id="MISSING")
    table = pd.concat([reference, extra], ignore_index=True)
    regions = scoring_regions(table, "benchmark", [])
    variants = processed_root / "processed" / "variants"
    with pytest.raises(ValueError, match="MISSING: no variants"):
        score_assays(_CachingScorer({}), table, variants, regions, tmp_path / "scores")
    assert not (tmp_path / "scores").exists()


def test_the_two_methods_of_a_model_have_different_labels(
    processed_root: Path, reference: pd.DataFrame, tmp_path: Path
) -> None:
    """Results tables have no method column, so the label carries the method."""
    assert run_label("esm2_650m", fast=False) == "esm2_650m"
    assert run_label("esm2_650m", fast=True) == "esm2_650m_wt"
    regions = scoring_regions(reference, "benchmark", [])
    variants = processed_root / "processed" / "variants"
    report = score_assays(_CachingScorer({}), reference, variants, regions, tmp_path, fast=True)
    assert set(load_scores(tmp_path)["model"]) == {"toy_wt"}
    assert set(report["model"]) == {"toy"} and set(report["method"]) == {"wt"}


def test_scores_are_paired_with_labels_and_must_cover_the_same_variants(
    processed_root: Path, reference: pd.DataFrame, tmp_path: Path
) -> None:
    variants = processed_root / "processed" / "variants"
    regions = scoring_regions(reference, "benchmark", [])
    score_assays(_CachingScorer({}), reference, variants, regions, tmp_path / "scores")
    scores = load_scores(tmp_path / "scores")

    joined = with_labels(scores, variants)
    assert list(joined.columns) == ["assay_id", "mutant", "DMS_score", "DMS_score_bin", "score"]
    by_key = joined.set_index(["assay_id", "mutant"])
    assert (
        by_key.loc[("A", "A4C"), "DMS_score"] == 1.5 and by_key.loc[("A", "A4C"), "score"] == -0.5
    )
    assert by_key.loc[("B", "R5W"), "DMS_score_bin"] == 0 and len(joined) == 6

    with pytest.raises(ValueError, match=r"5 scores, 6 variants, unmatched \{'left_only': 1\}"):
        with_labels(scores.iloc[1:], variants)
    extra = scores.iloc[[0]].assign(mutant="K2W")
    with pytest.raises(ValueError, match="right_only"):
        with_labels(pd.concat([scores, extra], ignore_index=True), variants)


def _tracked(store: Path) -> dict[str, tuple[dict[str, str], dict[str, float], dict[str, str]]]:
    """Run name -> (params, metrics, tags) in a tracking store; the latest run of a name wins."""
    settings = TrackingSettings(enabled=True, directory=store, experiment="zero-shot")
    client = MlflowClient(tracking_uri=settings.uri)
    experiment = client.get_experiment_by_name(settings.experiment)
    assert experiment is not None
    runs = client.search_runs([experiment.experiment_id], order_by=["attributes.start_time ASC"])
    assert all(r.info.status == "FINISHED" for r in runs)
    return {str(r.info.run_name): (r.data.params, r.data.metrics, r.data.tags) for r in runs}


def test_cli_scores_with_the_real_8m_model_resumes_and_tracks_every_run(
    processed_root: Path, reference: pd.DataFrame, tmp_path: Path
) -> None:
    """``mutascope score zero-shot`` twice with one cache directory: the second invocation
    scores nothing and writes the same scores. ``--fast`` writes a separate score set. Then
    ``eval zero-shot``, ``eval compare`` and ``eval model-gate`` on the results; every command
    logs one run."""
    ref_csv = tmp_path / "reference.csv"
    reference.to_csv(ref_csv, index=False)
    variants = processed_root / "processed" / "variants"
    common = ["--config-dir", str(CONF), "--reference", str(ref_csv)]
    overrides = [
        "model=esm2_8m",
        f"scoring.cache_dir={tmp_path / 'cache'}",
        f"tracking.dir={tmp_path / 'mlruns'}",
        "eval.scoring_region.msa_region_assays=null",
        "eval.bootstrap.n_resamples=200",
    ]
    args = ["score", "zero-shot", *common, "--variants-dir", str(variants)]
    args += ["--report-dir", str(tmp_path / "report")]

    first = CliRunner().invoke(app, [*args, "--out-dir", str(tmp_path / "first"), *overrides])
    assert first.exit_code == 0, first.output
    assert "esm2_8m: 2 assays, 6 variants" in first.output
    assert "5 positions scored in 2 assays, 0 assays read from the cache" in first.output

    second = CliRunner().invoke(app, [*args, "--out-dir", str(tmp_path / "second"), *overrides])
    assert second.exit_code == 0, second.output
    assert "0 positions scored in 0 assays, 2 assays read from the cache" in second.output
    assert "all runs (cache log)" in second.output and "5 positions" in second.output

    a, b = load_scores(tmp_path / "first" / "esm2_8m"), load_scores(tmp_path / "second" / "esm2_8m")
    pd.testing.assert_frame_equal(a, b)
    assert np.isfinite(a["score"]).all() and a["score"].abs().max() > 0
    assert set(a["model"]) == {"esm2_8m"}
    assert (tmp_path / "report" / "zero_shot_run_esm2_8m.parquet").exists()

    # The wild-type marginal: its own directory, report and cache-log total (5 residues in one
    # pass, not 10 with the masked run's), and different scores.
    wt = CliRunner().invoke(
        app, [*args, "--fast", "--out-dir", str(tmp_path / "first"), *overrides]
    )
    assert wt.exit_code == 0, wt.output
    assert "esm2_8m_wt: 2 assays, 6 variants" in wt.output
    assert "all runs (cache log)" in wt.output and "5 positions" in wt.output
    fast = load_scores(tmp_path / "first" / "esm2_8m_wt")
    assert set(fast["model"]) == {"esm2_8m_wt"} and not np.allclose(fast["score"], a["score"])
    pd.testing.assert_frame_equal(load_scores(tmp_path / "first" / "esm2_8m"), a)
    assert (tmp_path / "report" / "zero_shot_run_esm2_8m_wt.parquet").exists()

    results = tmp_path / "results"
    bench = ["eval", "zero-shot", *common, "--variants-dir", str(variants)]
    bench += ["--out-dir", str(results)]
    for label, flags in (("esm2_8m", []), ("esm2_8m_wt", ["--fast"])):
        scores_dir = ["--scores-dir", str(tmp_path / "first" / label)]
        done = CliRunner().invoke(app, [*bench, *scores_dir, *flags, *overrides])
        assert done.exit_code == 0, done.output
        assert f"{label}: 2 assays, 6 single mutants" in done.output
        per_assay = read_results(results / f"zero_shot_{label}_single.parquet")
        assert set(per_assay["model"]) == {label} and set(per_assay["assay_id"]) == {"A", "B"}
        assert (results / f"zero_shot_{label}_single_summary.parquet").exists()

    # A score set evaluated under the wrong method (so the wrong label) is refused.
    wrong = CliRunner().invoke(
        app, [*bench, "--scores-dir", str(tmp_path / "first" / "esm2_8m_wt"), *overrides]
    )
    assert wrong.exit_code == 1 and "holds ['esm2_8m_wt'], not esm2_8m" in wrong.output

    # Paired comparison of the two score sets just benchmarked; a pair without results fails.
    compare = ["eval", "compare", *common, "--results-dir", str(results)]
    compare += ["--out", str(results / "zero_shot" / "comparisons.parquet")]
    pair = "eval.comparisons=[[esm2_8m,esm2_8m_wt]]"
    compared = CliRunner().invoke(app, [*compare, *overrides, pair])
    assert compared.exit_code == 0, compared.output
    assert "esm2_8m - esm2_8m_wt: Spearman" in compared.output
    comparisons = read_results(results / "zero_shot" / "comparisons.parquet")
    assert set(comparisons["model_a"]) == {"esm2_8m"} and set(comparisons["model_b"]) == {
        "esm2_8m_wt"
    }
    headline = comparisons[
        (comparisons["metric"] == "spearman") & (comparisons["level"] == "overall")
    ]
    summaries = {
        label: read_results(results / f"zero_shot_{label}_single_summary.parquet")
        for label in ("esm2_8m", "esm2_8m_wt")
    }
    spearman = {
        label: frame[(frame["metric"] == "spearman") & (frame["level"] == "overall")][
            "value"
        ].item()
        for label, frame in summaries.items()
    }
    assert headline["diff"].item() == pytest.approx(spearman["esm2_8m"] - spearman["esm2_8m_wt"])
    absent = CliRunner().invoke(
        app, [*compare, *overrides, "eval.comparisons=[[esm2_8m,esm2_35m]]"]
    )
    assert absent.exit_code == 1 and "no results for ['esm2_35m']" in absent.output

    # The gate against "ProteinGym's" scores: here our own masked scores under their column name.
    column = "ESM2_8M"
    precomputed = with_labels(a, variants).rename(columns={"score": column})
    for assay_id, rows in precomputed.groupby("assay_id"):
        part = tmp_path / "precomputed" / f"assay_id={assay_id}"
        part.mkdir(parents=True)
        rows.drop(columns="assay_id").to_parquet(part / "data.parquet")
    gate = ["eval", "model-gate", *common, "--precomputed-dir", str(tmp_path / "precomputed")]
    gate += ["--scores-dir", str(tmp_path / "first" / "esm2_8m"), "--out-dir", str(results)]
    passed = CliRunner().invoke(app, [*gate, *overrides])
    assert passed.exit_code == 0, passed.output
    assert "model gate passed" in passed.output
    assert (results / "model_gate_esm2_8m.parquet").exists()
    not_masked = CliRunner().invoke(
        app, [*gate, "--scores-dir", str(tmp_path / "first" / "esm2_8m_wt"), *overrides]
    )
    assert not_masked.exit_code == 1 and "not esm2_8m" in not_masked.output

    runs = _tracked(tmp_path / "mlruns")
    assert sorted(runs) == [
        "benchmark esm2_8m",
        "benchmark esm2_8m_wt",
        "compare zero-shot",
        "model-gate esm2_8m",
        "score esm2_8m",
        "score esm2_8m_wt",
    ]  # the two masked scoring runs share a name; the latest is kept here
    params, metrics, tags = runs["benchmark esm2_8m_wt"]
    assert params["model.hf_id"] == "facebook/esm2_t6_8M_UR50D" and params["method"] == "wt"
    assert len(params["model.revision"]) == 40 and tags["step"] == "benchmark"
    summary = read_results(results / "zero_shot_esm2_8m_wt_single_summary.parquet")
    overall = summary[(summary["level"] == "overall") & (summary["metric"] == "spearman")]
    assert metrics["spearman"] == pytest.approx(overall["value"].item())
    assert metrics["variants"] == 6 and "spearman/Stability" in metrics
    params, metrics, tags = runs["compare zero-shot"]
    assert params["pairs"] == "esm2_8m - esm2_8m_wt" and params["bootstrap.n_resamples"] == "200"
    assert metrics["spearman_diff/esm2_8m-vs-esm2_8m_wt"] == pytest.approx(headline["diff"].item())
    assert tags["step"] == "compare" and "model.revision" not in params
    params, metrics, tags = runs["score esm2_8m"]
    assert params["method"] == "masked" and metrics["positions_computed"] == 0
    assert metrics["positions_all_runs"] == 5 and metrics["model_seconds_all_runs"] > 0
    assert runs["score esm2_8m_wt"][1]["positions_computed"] == 5
    _, metrics, tags = runs["model-gate esm2_8m"]
    assert tags["gate_passed"] == "true" and metrics["min_assay_spearman"] == pytest.approx(1.0)
    assert metrics["max_abs_diff"] == 0 and metrics["aggregate_delta"] == 0
