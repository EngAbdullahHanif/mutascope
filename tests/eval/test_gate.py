"""The model gate's two checks, on toy scores where the right verdict is known."""

import numpy as np
import pandas as pd
import pytest

from mutascope.eval.gate import GateThresholds, join_scores, model_gate

THRESHOLDS = GateThresholds(min_assay_spearman=0.99, aggregate_tolerance=0.01)
REFERENCE = pd.DataFrame(
    {
        "DMS_id": ["A1", "A2", "B1"],
        "UniProt_ID": ["P1", "P1", "P2"],
        "coarse_selection_type": ["Stability", "Stability", "Binding"],
    }
)


def _theirs(seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    frames: list[pd.DataFrame] = []
    for assay_id, n in (("A1", 60), ("A2", 40), ("B1", 50)):
        dms = rng.normal(size=n)
        frames.append(
            pd.DataFrame(
                {
                    "assay_id": assay_id,
                    "mutant": [f"M{i + 1}A" for i in range(n)],
                    "DMS_score": dms,
                    "DMS_score_bin": (dms > 0).astype(int),
                    "ESM2_650M": dms + rng.normal(size=n),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def _ours(theirs: pd.DataFrame) -> pd.DataFrame:
    """A faithful reimplementation: their scores plus float noise."""
    noise = np.random.default_rng(1).normal(scale=1e-5, size=len(theirs))
    return theirs[["assay_id", "mutant"]].assign(
        model="esm2_650m", score=theirs["ESM2_650M"] + noise
    )


def test_matching_scores_pass() -> None:
    theirs = _theirs()
    gate = model_gate(join_scores(_ours(theirs), theirs, "ESM2_650M"), REFERENCE, THRESHOLDS)
    assert gate.passed and gate.failing.empty
    assert gate.per_assay["assay_id"].tolist() == ["A1", "A2", "B1"]
    assert gate.per_assay["variants"].tolist() == [60, 40, 50]
    assert (gate.per_assay["spearman"] > 0.9999).all()
    assert gate.per_assay["max_abs_diff"].max() < 1e-4
    assert abs(gate.aggregate_delta) < 1e-3


def test_one_broken_assay_fails_the_gate_even_if_the_aggregate_barely_moves() -> None:
    """Shift A2's scores by one row, as a token off-by-one would. A2 is one of two assays of
    one protein, so the aggregate hides most of it; the per-variant check does not."""
    theirs = _theirs()
    ours = _ours(theirs)
    a2 = ours["assay_id"] == "A2"
    ours.loc[a2, "score"] = np.roll(ours.loc[a2, "score"].to_numpy(), 1)
    gate = model_gate(join_scores(ours, theirs, "ESM2_650M"), REFERENCE, THRESHOLDS)
    assert not gate.passed
    assert gate.failing["assay_id"].tolist() == ["A2"]
    assert gate.failing["spearman"].iloc[0] < 0.5


def test_aggregate_check_fails_on_its_own() -> None:
    """Every assay clears a (lowered) per-variant threshold, but the aggregate has moved."""
    theirs = _theirs()
    ours = _ours(theirs)
    ours["score"] += np.random.default_rng(2).normal(scale=0.6, size=len(ours))
    loose = GateThresholds(min_assay_spearman=0.5, aggregate_tolerance=0.01)
    gate = model_gate(join_scores(ours, theirs, "ESM2_650M"), REFERENCE, loose)
    assert gate.failing.empty
    assert abs(gate.aggregate_delta) > 0.01 and not gate.passed


def test_constant_scores_are_a_failure_not_a_pass() -> None:
    theirs = _theirs()
    ours = _ours(theirs)
    ours.loc[ours["assay_id"] == "B1", "score"] = 0.0
    gate = model_gate(join_scores(ours, theirs, "ESM2_650M"), REFERENCE, THRESHOLDS)
    assert gate.failing["assay_id"].tolist() == ["B1"]
    assert np.isnan(gate.failing["spearman"].iloc[0])


@pytest.mark.parametrize("drop_from", ["ours", "theirs"])
def test_tables_with_different_variants_are_refused(drop_from: str) -> None:
    theirs = _theirs()
    ours = _ours(theirs)
    if drop_from == "ours":
        ours = ours.iloc[1:]
    else:
        theirs = theirs.iloc[1:]
    with pytest.raises(ValueError, match="score tables differ"):
        join_scores(ours, theirs, "ESM2_650M")


def _with_broken_a2() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Our scores are right; "ProteinGym's" A2 scores are scrambled, as an upstream error would."""
    theirs = _theirs()
    ours = _ours(theirs)
    a2 = theirs["assay_id"] == "A2"
    theirs.loc[a2, "ESM2_650M"] = np.roll(theirs.loc[a2, "ESM2_650M"].to_numpy(), 1)
    return ours, theirs


def test_a_documented_exception_is_left_out_of_both_checks() -> None:
    ours, theirs = _with_broken_a2()
    joined = join_scores(ours, theirs, "ESM2_650M")
    assert not model_gate(joined, REFERENCE, THRESHOLDS).passed
    gate = model_gate(joined, REFERENCE, THRESHOLDS, ["A2"])
    assert gate.passed and gate.failing.empty and gate.stale.empty
    assert gate.exceptions["assay_id"].tolist() == ["A2"]
    assert gate.per_assay["exception"].tolist() == [False, True, False]
    assert not gate.per_assay.set_index("assay_id").loc["A2", "passed"]
    # The gated aggregates agree; with A2 included, theirs is pulled away by the wrong scores.
    assert abs(gate.aggregate_delta) < 1e-3
    assert abs(gate.ours_all - gate.theirs_all) > abs(gate.aggregate_delta)
    assert gate.ours_all != gate.ours


def test_a_stale_exception_fails_the_gate() -> None:
    """A2 is listed but agrees: upstream was fixed, or the entry was never right. Either way
    the list no longer describes the data, and the gate says so instead of ignoring A2."""
    theirs = _theirs()
    gate = model_gate(
        join_scores(_ours(theirs), theirs, "ESM2_650M"), REFERENCE, THRESHOLDS, ["A2"]
    )
    assert gate.failing.empty and gate.stale["assay_id"].tolist() == ["A2"]
    assert not gate.passed


def test_an_exception_for_an_assay_that_was_not_compared_is_refused() -> None:
    theirs = _theirs()
    joined = join_scores(_ours(theirs), theirs, "ESM2_650M")
    with pytest.raises(ValueError, match=r"not compared: \['Z9'\]"):
        model_gate(joined, REFERENCE, THRESHOLDS, ["Z9"])


def test_an_exception_does_not_excuse_another_assay() -> None:
    ours, theirs = _with_broken_a2()
    gate = model_gate(join_scores(ours, theirs, "ESM2_650M"), REFERENCE, THRESHOLDS, ["A1"])
    assert gate.failing["assay_id"].tolist() == ["A2"] and not gate.passed
