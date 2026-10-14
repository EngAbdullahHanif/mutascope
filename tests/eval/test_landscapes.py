"""Full landscapes: which ones an assay has, filling the cache in slices, and the coverage check.

The runner is tested with a small scorer whose "cache" is a dictionary shared between
instances, like the disk cache is shared between processes. The CLI test runs the real
``esm2_t6_8M`` checkpoint and checks that the benchmark's scores are the same before and after.
"""

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from mutascope.alphabet import AA_ORDER
from mutascope.cli import app
from mutascope.eval.landscapes import (
    LandscapeTarget,
    fill_landscapes,
    format_duration,
    landscape_coverage,
    landscape_targets,
    pass_tokens,
)
from mutascope.eval.region import ScoringRegion, scoring_regions, structure_regions
from mutascope.eval.zero_shot import load_scores
from mutascope.scoring.base import FloatArray, landscape_from_log_probs, resolve_positions
from mutascope.scoring.cache import CacheError, CacheKey, LogProbCache
from mutascope.scoring.struct_tokens import AssayStructure, load_struct_tokens

CONF = Path(__file__).parents[2] / "conf"
LONG = "MKVARNDCQEGH"

Cache = dict[tuple[str, str | None, str], FloatArray]


class _SlicedScorer:
    """Row ``p`` of an entry is ``p`` in every column; each call that computes is recorded."""

    def __init__(self, cache: Cache, dies_after: int | None = None) -> None:
        self.cache = cache
        self.dies_after = dies_after
        """Number of computations after which the next one "crashes"."""
        self.computed: list[tuple[str, list[int]]] = []

    @property
    def model_id(self) -> str:
        return "toy"

    @property
    def positions_computed(self) -> int:
        return sum(len(positions) for _, positions in self.computed)

    def landscape(
        self,
        seq: str,
        struct_tokens: str | None = None,
        *,
        fast: bool = False,
        region: str = "full",
        positions: Sequence[int] | None = None,
    ) -> FloatArray:
        entry = self.cache.setdefault(
            (seq, struct_tokens, region), np.full((len(seq), 20), np.nan, dtype=np.float32)
        )
        missing = [p for p in resolve_positions(positions, len(seq)) if np.isnan(entry[p, 0])]
        if missing:
            if self.dies_after is not None and len(self.computed) >= self.dies_after:
                raise KeyboardInterrupt
            self.computed.append((region, missing))
            for p in missing:
                entry[p] = p
        return landscape_from_log_probs(entry, seq)


def _reference(rows: list[tuple[str, str, int, int]]) -> pd.DataFrame:
    """A reference table from ``(assay, target, MSA_start, MSA_end)`` rows."""
    return pd.DataFrame(rows, columns=["DMS_id", "target_seq", "MSA_start", "MSA_end"])


def _target(assay_id: str, seq: str, start: int = 0, end: int | None = None) -> LandscapeTarget:
    region = ScoringRegion(assay_id, start, len(seq) if end is None else end, len(seq))
    return LandscapeTarget(assay_id, region, region.sequence(seq), None, benchmark=True)


def test_an_msa_region_assay_has_two_labelled_landscapes() -> None:
    """A is scored on its full target. B's MSA covers residues 3-6 of 12, so it has the
    benchmark landscape of that region and a second one of the full target."""
    reference = _reference([("A", "MKVAR", 1, 5), ("B", LONG, 3, 6)])
    targets = landscape_targets(reference, scoring_regions(reference, "benchmark", ["B"]))

    assert [(t.assay_id, t.region.label, t.sequence) for t in targets] == [
        ("A", "full", "MKVAR"),
        ("B", "msa:3-6", "VARN"),
        ("B", "full", LONG),
    ]
    assert [(t.benchmark, t.full_target) for t in targets] == [
        (True, True),
        (True, False),
        (False, True),
    ]
    assert len({t.entry for t in targets}) == 3


def test_a_structure_aware_model_has_landscapes_only_where_there_is_a_structure(
    processed_root: Path,
) -> None:
    """Assay A has a structure of its whole target, B has none: one landscape, with the
    structure tokens (``#`` at the two low-pLDDT residues)."""
    reference = pd.read_csv(processed_root / "DMS_substitutions.csv")
    processed = processed_root / "processed"
    sequences = dict(zip(reference["DMS_id"], reference["target_seq"], strict=True))
    structures = load_struct_tokens(
        processed / "three_di", processed / "structure_features", sequences, 70.0
    )
    targets = landscape_targets(reference, structure_regions(reference, structures), structures)
    assert [(t.assay_id, t.region.label, t.struct_tokens) for t in targets] == [
        ("A", "full", "dd#v#")
    ]


def test_a_partial_structure_gives_a_full_target_landscape_with_its_own_tokens() -> None:
    """The structure models residues 3-6 of 12: the benchmark landscape gets those four
    tokens, the full-target one all twelve."""
    reference = _reference([("A", LONG, 1, 12)])
    tokens = "##ddvv######"
    structures = {"A": AssayStructure("A", tokens, 2, 6)}
    targets = landscape_targets(reference, structure_regions(reference, structures), structures)
    assert [(t.region.label, t.sequence, t.struct_tokens) for t in targets] == [
        ("pdb:3-6", "VARN", "ddvv"),
        ("full", LONG, tokens),
    ]
    assert [t.benchmark for t in targets] == [True, False]


def test_only_missing_residues_are_scored_in_slices_of_the_checkpoint_size() -> None:
    """Residues 1 and 7 were scored by the benchmark. A pass is 14 tokens (12 residues and two
    special tokens), so 56 tokens between cache writes is 4 residues per slice."""
    cache: Cache = {}
    target = _target("A", LONG)
    benchmark = _SlicedScorer(cache)
    benchmark.landscape(LONG, positions=[1, 7])
    before = cache[target.entry].copy()

    scorer = _SlicedScorer(cache)
    report = fill_landscapes(scorer, [target], window_tokens=None, checkpoint_tokens=56)

    assert scorer.computed == [
        ("full", [0, 2, 3, 4]),
        ("full", [5, 6, 8, 9]),
        ("full", [10, 11]),
    ]
    assert np.isfinite(cache[target.entry]).all()
    np.testing.assert_array_equal(cache[target.entry][[1, 7]], before[[1, 7]])
    assert report["positions_computed"].tolist() == [10]
    assert report[["region", "region_length", "benchmark", "full_target"]].iloc[0].tolist() == [
        "full",
        12,
        True,
        True,
    ]


def test_a_run_killed_inside_a_sequence_resumes_where_it_stopped() -> None:
    """The first run dies in its third slice: the two finished slices are in the cache, and
    the rerun scores the four residues that are left and nothing else."""
    cache: Cache = {}
    target = _target("A", LONG)
    with pytest.raises(KeyboardInterrupt):
        fill_landscapes(
            _SlicedScorer(cache, dies_after=2), [target], window_tokens=None, checkpoint_tokens=56
        )
    assert int(np.isfinite(cache[target.entry][:, 0]).sum()) == 8

    resumed = _SlicedScorer(cache)
    report = fill_landscapes(resumed, [target], window_tokens=None, checkpoint_tokens=56)
    assert resumed.computed == [("full", [8, 9, 10, 11])]
    assert report["positions_computed"].tolist() == [4]

    again = _SlicedScorer(cache)
    report = fill_landscapes(again, [target], window_tokens=None, checkpoint_tokens=56)
    assert again.computed == [] and report["positions_computed"].tolist() == [0]


def test_assays_of_one_protein_share_a_landscape_and_it_is_computed_once() -> None:
    reference = _reference([("A", "MKVAR", 1, 5), ("B", "MKVAR", 1, 5), ("C", LONG, 3, 6)])
    targets = landscape_targets(reference, scoring_regions(reference, "benchmark", ["C"]))
    scorer = _SlicedScorer({})
    report = fill_landscapes(scorer, targets, window_tokens=None, checkpoint_tokens=10_000)

    assert [region for region, _ in scorer.computed] == ["full", "msa:3-6", "full"]
    assert report["assay_id"].tolist() == ["A", "B", "C", "C"]
    assert report["positions_computed"].tolist() == [5, 0, 4, 12]
    assert report["region"].tolist() == ["full", "full", "msa:3-6", "full"]


def test_a_scorer_that_leaves_a_residue_unscored_fails() -> None:
    class _Lazy(_SlicedScorer):
        def landscape(
            self,
            seq: str,
            struct_tokens: str | None = None,
            *,
            fast: bool = False,
            region: str = "full",
            positions: Sequence[int] | None = None,
        ) -> FloatArray:
            return np.full((len(seq), 20), np.nan, dtype=np.float32)

    with pytest.raises(RuntimeError, match="left residues unscored"):
        fill_landscapes(_Lazy({}), [_target("A", "MKVAR")], window_tokens=None, checkpoint_tokens=1)
    with pytest.raises(ValueError, match="checkpoint_tokens"):
        fill_landscapes(
            _SlicedScorer({}), [_target("A", "MKVAR")], window_tokens=None, checkpoint_tokens=0
        )


def test_a_pass_is_the_sequence_or_one_window() -> None:
    assert pass_tokens(100, 1024) == 102
    assert pass_tokens(1022, 1024) == 1024
    assert pass_tokens(3033, 1024) == 1024
    assert pass_tokens(3033, None) == 3035


def test_durations_are_readable() -> None:
    assert format_duration(42.4) == "42s"
    assert format_duration(450) == "7m 30s"
    assert format_duration(7500) == "2h 05m"


def test_coverage_counts_only_rows_with_all_twenty_values(tmp_path: Path) -> None:
    """Three landscapes of one model: complete, partly scored (one row has a NaN in a single
    column and does not count), and absent from the cache."""
    cache = LogProbCache(tmp_path)
    targets = [_target("A", "MKVAR"), _target("B", LONG), _target("C", "ACDEF")]

    def key(target: LandscapeTarget) -> CacheKey:
        return CacheKey("hub/toy", "r" * 40, target.sequence, None, "masked", "full")

    cache.put(key(targets[0]), np.zeros((5, 20), dtype=np.float32))
    partial = np.full((12, 20), np.nan, dtype=np.float32)
    partial[:6] = 0.0
    partial[5, AA_ORDER.index("W")] = np.nan
    cache.put(key(targets[1]), partial)

    coverage = landscape_coverage(cache, targets, model="toy", hf_id="hub/toy", revision="r" * 40)
    assert coverage["positions_scored"].tolist() == [5, 5, 0]
    assert coverage["complete"].tolist() == [True, False, False]
    assert set(coverage["model"]) == {"toy"}

    # The same sequence under another revision or method is another entry: nothing is found.
    other = landscape_coverage(cache, targets, model="toy", hf_id="hub/toy", revision="s" * 40)
    assert other["positions_scored"].tolist() == [0, 0, 0]

    wrong_shape = cache.path(key(targets[2]))
    wrong_shape.parent.mkdir(parents=True, exist_ok=True)
    np.save(wrong_shape, np.zeros((4, 20), dtype=np.float32))
    with pytest.raises(CacheError):
        landscape_coverage(cache, targets, model="toy", hf_id="hub/toy", revision="r" * 40)


def test_cli_fills_landscapes_with_the_real_8m_model_and_the_benchmark_scores_stay(
    processed_root: Path, tmp_path: Path
) -> None:
    """``score zero-shot`` (measured positions), the check fails, ``score landscapes``, the
    check passes, and ``score zero-shot`` again scores nothing and writes the same scores.

    C is a third assay whose MSA covers residues 3-6 of a 12-residue target, so it has a
    benchmark-region landscape and a full-target one. It has no variants and is left out of
    the two ``score zero-shot`` runs.
    """
    table = pd.read_csv(processed_root / "DMS_substitutions.csv")
    table["MSA_start"], table["MSA_end"] = 1, table["target_seq"].str.len()
    benchmark_csv = tmp_path / "benchmark.csv"
    table.to_csv(benchmark_csv, index=False)
    extra = table.iloc[[0]].assign(DMS_id="C", target_seq=LONG, MSA_start=3, MSA_end=6)
    landscape_csv = tmp_path / "landscapes.csv"
    pd.concat([table, extra]).to_csv(landscape_csv, index=False)

    variants = processed_root / "processed" / "variants"
    overrides = [
        "model=esm2_8m",
        f"scoring.cache_dir={tmp_path / 'cache'}",
        f"tracking.dir={tmp_path / 'mlruns'}",
        "scoring.landscapes.models=[esm2_8m]",
        "scoring.landscapes.checkpoint_tokens=21",
    ]
    no_msa = ["eval.scoring_region.msa_region_assays=null"]
    # Replace the five real MSA-region assays by C (Hydra: delete the key, then add it).
    with_msa = [
        "~eval.scoring_region.msa_region_assays",
        "+eval.scoring_region.msa_region_assays=[C]",
    ]
    zero_shot = ["score", "zero-shot", "--config-dir", str(CONF), "--reference", str(benchmark_csv)]
    zero_shot += ["--variants-dir", str(variants), "--report-dir", str(tmp_path / "report")]
    common = ["--config-dir", str(CONF), "--reference", str(landscape_csv)]
    check = ["score", "check-landscapes", *common, "--out", str(tmp_path / "coverage.parquet")]

    first = CliRunner().invoke(
        app, [*zero_shot, "--out-dir", str(tmp_path / "first"), *overrides, *no_msa]
    )
    assert first.exit_code == 0, first.output

    missing = CliRunner().invoke(app, [*check, *overrides, *with_msa])
    assert missing.exit_code == 1, missing.output
    assert "esm2_8m: 2/4 landscapes complete" in missing.output
    assert "FAIL: 2 of 4 landscapes incomplete" in missing.output
    assert "incomplete: esm2_8m C (msa:3-6): 0/4" in missing.output

    run = ["score", "landscapes", *common, "--report-dir", str(tmp_path / "report")]
    filled = CliRunner().invoke(app, [*run, *overrides, *with_msa])
    assert filled.exit_code == 0, filled.output
    assert "esm2_8m: 4 complete landscapes of 3 assays" in filled.output
    assert "(1 full-target landscapes beside a partial benchmark region)" in filled.output
    assert "16 positions scored" in filled.output
    report = pd.read_parquet(tmp_path / "report" / "landscape_run_esm2_8m.parquet")
    assert report["region"].tolist() == ["full", "full", "msa:3-6", "full"]
    assert report["positions_computed"].tolist() == [0, 0, 4, 12]

    passed = CliRunner().invoke(app, [*check, *overrides, *with_msa])
    assert passed.exit_code == 0, passed.output
    assert "PASS: all 4 landscapes are complete (L, 20) arrays" in passed.output
    coverage = pd.read_parquet(tmp_path / "coverage.parquet")
    assert coverage["complete"].all()
    assert coverage["positions_scored"].tolist() == [5, 5, 4, 12]

    rerun = CliRunner().invoke(app, [*run, *overrides, *with_msa])
    assert rerun.exit_code == 0, rerun.output
    assert "0 positions scored" in rerun.output

    second = CliRunner().invoke(
        app, [*zero_shot, "--out-dir", str(tmp_path / "second"), *overrides, *no_msa]
    )
    assert second.exit_code == 0, second.output
    assert "0 positions scored in 0 assays, 2 assays read from the cache" in second.output
    pd.testing.assert_frame_equal(
        load_scores(tmp_path / "first" / "esm2_8m"), load_scores(tmp_path / "second" / "esm2_8m")
    )


def test_cli_refuses_an_ensemble(tmp_path: Path) -> None:
    reference = tmp_path / "reference.csv"
    reference.write_text("DMS_id,target_seq\n")
    result = CliRunner().invoke(
        app,
        ["score", "landscapes", "--config-dir", str(CONF), "--reference", str(reference),
         "--report-dir", str(tmp_path), "model=ensemble_esm2_650m_saprot_650m_af2"],
    )  # fmt: skip
    assert result.exit_code == 1 and "is an ensemble" in result.output
