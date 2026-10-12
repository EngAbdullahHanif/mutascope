"""Benchmark scoring region: MSA region where it is not the full target, full target otherwise.

Logic tests run on a toy reference. The three tests on the real reference file (skipped without
the downloaded release) pin the rule on the pinned ProteinGym release: the five configured
assays use their MSA region in benchmark mode, every other assay the full target, and website
landscapes the full target everywhere.
"""

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from mutascope.alphabet import AA_ORDER
from mutascope.data.release import load_release
from mutascope.eval.region import (
    ScoringRegion,
    ScoringRegionError,
    derive_msa_regions,
    score_assay,
    scoring_regions,
    structure_regions,
)
from mutascope.scoring.base import FloatArray
from mutascope.scoring.struct_tokens import AssayStructure
from mutascope.utils.config import load_config

CONF = Path(__file__).parents[2] / "conf"
_cfg = load_config(CONF)
EXPECTED = [str(a) for a in _cfg.eval.scoring_region.msa_region_assays]


def _reference(rows: list[tuple[str, str, int, int]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["DMS_id", "target_seq", "MSA_start", "MSA_end"])


TOY = _reference([("full", "MKVAR", 1, 5), ("crop", "MKVARLLE", 3, 6)])


def test_derived_region_is_zero_based_half_open() -> None:
    region = derive_msa_regions(TOY)["crop"]
    assert (region.start, region.end, region.label) == (2, 6, "msa:3-6")
    assert region.sequence("MKVARLLE") == "VARL"
    assert set(derive_msa_regions(TOY)) == {"crop"}


def test_config_mismatch_fails_both_ways() -> None:
    with pytest.raises(ScoringRegionError, match=r"not in eval.scoring_region: \['crop'\]"):
        scoring_regions(TOY, "benchmark", [])
    with pytest.raises(ScoringRegionError, match=r"covers the full target: \['full'\]"):
        scoring_regions(TOY, "landscape", ["crop", "full"])


def test_modes_on_toy_reference() -> None:
    bench = scoring_regions(TOY, "benchmark", ["crop"])
    site = scoring_regions(TOY, "landscape", ["crop"])
    assert bench["crop"].label == "msa:3-6" and bench["full"].is_full
    assert site["crop"].is_full and site["full"].is_full


def test_malformed_region_is_refused() -> None:
    with pytest.raises(ScoringRegionError, match="outside"):
        ScoringRegion("x", 3, 9, 8)


class _RecordingScorer:
    """Landscape value = target position of the row, so lookups reveal the coordinates used.
    Rows that were not requested are NaN, as a real scorer leaves them."""

    def __init__(self, offset: int) -> None:
        self.offset = offset
        self.calls: list[tuple[str, str, list[int] | None]] = []

    @property
    def model_id(self) -> str:
        return "recording"

    @property
    def positions_computed(self) -> int:
        return sum(len(positions or ()) for _, _, positions in self.calls)

    def landscape(
        self,
        seq: str,
        struct_tokens: str | None = None,
        *,
        fast: bool = False,
        region: str = "full",
        positions: Sequence[int] | None = None,
    ) -> FloatArray:
        self.calls.append((seq, region, None if positions is None else list(positions)))
        rows = np.arange(len(seq), dtype=np.float32)[:, None] + self.offset
        landscape = np.repeat(rows, len(AA_ORDER), axis=1)
        if positions is not None:
            landscape[np.setdiff1d(np.arange(len(seq)), positions)] = np.nan
        return landscape


def test_score_assay_scores_the_region_and_maps_positions() -> None:
    """Only the measured positions are requested, in region coordinates (V3 -> 0, L6 -> 3)."""
    region = derive_msa_regions(TOY)["crop"]
    scorer = _RecordingScorer(offset=region.start)
    scores = score_assay(scorer, "MKVARLLE", region, ["V3A", "L6A", "V3C"])
    assert scorer.calls == [("VARL", "msa:3-6", [0, 3])]
    assert scores.tolist() == [2.0, 5.0, 2.0]  # target positions 2 and 5 (0-based)


def test_score_assay_rejects_a_mutant_outside_the_region_before_scoring() -> None:
    region = derive_msa_regions(TOY)["crop"]
    scorer = _RecordingScorer(offset=region.start)
    with pytest.raises(ValueError, match="no residue there"):
        score_assay(scorer, "MKVARLLE", region, ["V3A", "K2A"])  # fail, not drop
    assert scorer.calls == []  # no model time spent on an assay that cannot be scored


def test_score_assay_fails_if_the_scorer_leaves_a_requested_position_unscored() -> None:
    class Lazy(_RecordingScorer):
        def landscape(self, seq: str, *args: object, **kwargs: object) -> FloatArray:
            return np.full((len(seq), len(AA_ORDER)), np.nan, dtype=np.float32)

    with pytest.raises(RuntimeError, match="left requested positions unscored"):
        score_assay(Lazy(0), "MKVAR", ScoringRegion("full", 0, 5, 5), ["K2A"])


# --- the pinned ProteinGym reference ------------------------------------------------------------

_release = load_release(_cfg.data)
_REFERENCE = _release.raw_dir / _release.file("reference").filename
needs_reference = pytest.mark.skipif(
    not _REFERENCE.is_file(), reason="needs the downloaded ProteinGym reference file"
)


@pytest.fixture(scope="module")
def reference() -> pd.DataFrame:
    return pd.read_csv(_REFERENCE)


@needs_reference
def test_benchmark_mode_scores_the_five_on_their_msa_region(reference: pd.DataFrame) -> None:
    regions = scoring_regions(reference, "benchmark", EXPECTED)
    assert len(EXPECTED) == 5
    msa = reference.set_index("DMS_id")
    for assay_id in EXPECTED:
        region = regions[assay_id]
        assert not region.is_full
        assert (
            region.label == f"msa:{msa.loc[assay_id, 'MSA_start']}-{msa.loc[assay_id, 'MSA_end']}"
        )
    assert regions["KCNH2_HUMAN_Kozek_2020"].label == "msa:535-565"


@needs_reference
def test_benchmark_mode_scores_every_other_assay_in_full(reference: pd.DataFrame) -> None:
    regions = scoring_regions(reference, "benchmark", EXPECTED)
    others = [a for a in regions if a not in EXPECTED]
    assert len(others) == len(reference) - 5 == 212
    assert all(regions[a].is_full for a in others)


@needs_reference
def test_landscape_mode_scores_every_assay_in_full(reference: pd.DataFrame) -> None:
    regions = scoring_regions(reference, "landscape", EXPECTED)
    assert len(regions) == 217
    assert all(region.is_full for region in regions.values())


class _Recorder:
    """Records what the scorer is asked for; every score is 0."""

    model_id = "recorder"
    positions_computed = 0

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, str]] = []

    def landscape(
        self,
        seq: str,
        struct_tokens: str | None = None,
        *,
        fast: bool = False,
        region: str = "full",
        positions: Sequence[int] | None = None,
    ) -> FloatArray:
        self.calls.append((seq, struct_tokens, region))
        return np.zeros((len(seq), len(AA_ORDER)), dtype=np.float32)


def test_structure_regions_follow_the_structure_not_the_msa() -> None:
    """``crop`` has MSA region 3-6, but its structure models residues 2-7: a structure-aware
    model is scored on 2-7. An assay without a structure gets no region."""
    structures = {"crop": AssayStructure("crop", "#dvvla##", 1, 7)}
    regions = structure_regions(TOY, structures)
    assert set(regions) == {"crop"}
    assert (regions["crop"].start, regions["crop"].end) == (1, 7)
    assert regions["crop"].label == "pdb:2-7"
    whole = structure_regions(TOY, {"full": AssayStructure("full", "ddvvl", 0, 5)})
    assert whole["full"].label == "full"


def test_score_assay_passes_the_regions_part_of_the_structure_tokens() -> None:
    region = ScoringRegion("crop", 1, 7, 8, kind="pdb")
    scorer = _Recorder()
    score_assay(scorer, "MKVARLLE", region, ["K2A", "L7G"], struct_tokens="#dvvla##")
    assert scorer.calls == [("KVARLL", "dvvla#", "pdb:2-7")]
    with pytest.raises(ScoringRegionError, match="3 structure tokens for a 8-residue target"):
        score_assay(scorer, "MKVARLLE", region, ["K2A"], struct_tokens="dvv")
    score_assay(scorer, "MKVARLLE", region, ["K2A"])
    assert scorer.calls[-1] == ("KVARLL", None, "pdb:2-7")
