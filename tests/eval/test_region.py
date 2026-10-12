"""Benchmark scoring region: MSA region where it is not the full target, full target otherwise.

Logic tests run on a toy reference. The three tests on the real reference file (skipped without
the downloaded release) pin the rule on the pinned ProteinGym release: the five configured
assays use their MSA region in benchmark mode, every other assay the full target, and website
landscapes the full target everywhere.
"""

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
)
from mutascope.scoring.base import FloatArray
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
    """Landscape value = target position of the row, so lookups reveal the coordinates used."""

    def __init__(self, offset: int) -> None:
        self.offset = offset
        self.calls: list[tuple[str, str]] = []

    @property
    def model_id(self) -> str:
        return "recording"

    def landscape(
        self,
        seq: str,
        struct_tokens: str | None = None,
        *,
        fast: bool = False,
        region: str = "full",
    ) -> FloatArray:
        self.calls.append((seq, region))
        rows = np.arange(len(seq), dtype=np.float32)[:, None] + self.offset
        return np.repeat(rows, len(AA_ORDER), axis=1)


def test_score_assay_scores_the_region_and_maps_positions() -> None:
    region = derive_msa_regions(TOY)["crop"]
    scorer = _RecordingScorer(offset=region.start)
    scores = score_assay(scorer, "MKVARLLE", region, ["V3A", "L6A"])
    assert scorer.calls == [("VARL", "msa:3-6")]
    assert scores.tolist() == [2.0, 5.0]  # target positions 2 and 5 (0-based)
    with pytest.raises(ValueError, match="no residue there"):
        score_assay(scorer, "MKVARLLE", region, ["K2A"])  # outside the region: fail, not drop


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
