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
from typer.testing import CliRunner

from mutascope.alphabet import AA_ORDER
from mutascope.cli import app
from mutascope.eval.region import scoring_regions
from mutascope.eval.zero_shot import load_scores, score_assays
from mutascope.scoring.base import FloatArray, landscape_from_log_probs, resolve_positions

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


def test_cli_scores_with_the_real_8m_model_and_resumes(
    processed_root: Path, reference: pd.DataFrame, tmp_path: Path
) -> None:
    """``mutascope score zero-shot`` twice with one cache directory: the second invocation
    scores nothing and writes the same scores."""
    ref_csv = tmp_path / "reference.csv"
    reference.to_csv(ref_csv, index=False)
    args = ["score", "zero-shot", "--config-dir", str(CONF), "--reference", str(ref_csv)]
    args += ["--variants-dir", str(processed_root / "processed" / "variants")]
    args += ["--report-dir", str(tmp_path / "report"), "model=esm2_8m"]
    args += [
        f"scoring.cache_dir={tmp_path / 'cache'}",
        "eval.scoring_region.msa_region_assays=null",
    ]

    first = CliRunner().invoke(app, [*args, "--out-dir", str(tmp_path / "first")])
    assert first.exit_code == 0, first.output
    assert "esm2_8m: 2 assays, 6 variants" in first.output
    assert "5 positions scored in 2 assays, 0 assays read from the cache" in first.output

    second = CliRunner().invoke(app, [*args, "--out-dir", str(tmp_path / "second")])
    assert second.exit_code == 0, second.output
    assert "0 positions scored in 0 assays, 2 assays read from the cache" in second.output
    assert "all runs (cache log)" in second.output and "5 positions" in second.output

    a, b = load_scores(tmp_path / "first"), load_scores(tmp_path / "second")
    pd.testing.assert_frame_equal(a, b)
    assert np.isfinite(a["score"]).all() and a["score"].abs().max() > 0
    assert (tmp_path / "report" / "zero_shot_run_esm2_8m.parquet").exists()
