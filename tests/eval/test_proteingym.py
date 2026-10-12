"""Reading ProteinGym's precomputed scores and published leaderboard files."""

from pathlib import Path

import pandas as pd
import pytest

from mutascope.eval.proteingym import (
    compare_with_published,
    load_precomputed_scores,
    published_assay_level,
    published_summary,
)

NAMES = {"ESM2_650M": "ESM2 (650M)", "SaProt_650M_AF2": "SaProt (650M)"}


def test_row_sets(release_dir: Path) -> None:
    every = load_precomputed_scores(release_dir / "scores", ["ESM2_650M"], "all")
    single = load_precomputed_scores(release_dir / "scores", ["ESM2_650M"], "single")
    assert len(every) == 40 + 30 + 20 + 3
    assert len(single) == 40 + 30 + 20
    assert not single["mutant"].str.contains(":").any()
    assert list(single.columns) == ["assay_id", "mutant", "DMS_score", "DMS_score_bin", "ESM2_650M"]


def test_published_summary_is_long(release_dir: Path) -> None:
    table = published_summary(release_dir / "summary.csv", NAMES)
    esm = table[table["model"] == "ESM2_650M"].fillna({"group": "-"})
    assert esm.set_index(["level", "group"])["value"].to_dict() == {
        ("overall", "-"): 0.4,
        ("function", "Binding"): 0.35,
        ("function", "Stability"): 0.45,
    }


def test_published_assay_level_keeps_named_models(release_dir: Path) -> None:
    table = published_assay_level(release_dir / "assay_level.csv", NAMES)
    assert set(table["model"]) == set(NAMES)
    assert len(table) == 6


def test_unknown_leaderboard_name_fails(release_dir: Path) -> None:
    with pytest.raises(KeyError, match="ESM9"):
        published_summary(release_dir / "summary.csv", {"x": "ESM9"})


def test_compare_aligns_on_level_and_group() -> None:
    ours = pd.DataFrame(
        {
            "model": ["proteingym:ESM2_650M"] * 2,
            "level": ["overall", "function"],
            "group": pd.array([None, "Binding"], dtype="string"),
            "value": [0.4137, 0.3366],
        }
    )
    published = pd.DataFrame(
        {
            "model": ["ESM2_650M"] * 2,
            "level": ["function", "overall"],
            "group": ["Binding", None],
            "value": [0.337, 0.414],
        }
    )
    table = compare_with_published(ours, published).set_index("level")
    assert table.loc["overall", "delta"] == pytest.approx(-0.0003)
    assert table.loc["function", "delta"] == pytest.approx(-0.0004)
    assert pd.isna(table.loc["overall", "group"])
