"""Reproduction gate (CLAUDE.md, ADR 0010), in two parts that must both pass.

Harness gate: our metrics and aggregation on ProteinGym's precomputed ``ESM2_650M`` scores over
all rows (singles and multi-mutants) reproduce the published average Spearman 0.414
(|Δ| ≤ 0.0005, the published value's rounding). The same check runs for every model in
``eval.precomputed_models`` (``SaProt_650M_AF2``, published 0.457). Two diagnostics sit next to
it so a failure says where the bug is: per-assay Spearman against the published DMS-level file
(the metric), and our aggregation applied to the published per-assay values (the aggregation).
They need the downloaded release and are skipped without it, as in CI.

Model gate: on single mutants, our ESM-2 650M scores have Spearman >= 0.99 with ProteinGym's
precomputed scores in every assay, and our aggregate is within ±0.01 of the aggregate the same
harness computes from their scores on the same rows. Implemented with the zero-shot scorers.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from mutascope.data.release import load_release
from mutascope.eval.aggregate import AssayGroups, aggregate
from mutascope.eval.benchmark import assay_metrics_table
from mutascope.eval.proteingym import (
    PRECOMPUTED_PREFIX,
    load_precomputed_scores,
    published_assay_level,
    published_summary,
)
from mutascope.utils.config import load_config

CONF = Path(__file__).parents[1] / "conf"
TOLERANCE = 0.0005
"""Published values have 3 decimals: ours must round to them (ADR 0010)."""
FLOAT_SLACK = 1e-9
"""Room for binary floating point at the exact rounding boundary (e.g. 0.4135 vs 0.414)."""
FUNCTION_TOLERANCE = 2 * TOLERANCE
"""Function-level values (our extra check, not part of ADR 0010's gate) are rounded twice
upstream: each assay to 3 decimals, then the mean. Each rounding moves a value by up to 0.0005.
SaProt Binding is the case that needs it: 0.37853 unrounded, but exactly 0.3785 from the rounded
per-assay values, which rounds half-to-even to the published 0.378."""

_cfg = load_config(CONF)
_release = load_release(_cfg.data)
SCORES_DIR = _release.raw_dir / _release.file("zero_shot_scores").extract_dirname
REFERENCE = _release.raw_dir / _release.file("reference").filename
SUMMARY = Path(_cfg.eval.published.summary)
ASSAY_LEVEL = Path(_cfg.eval.published.assay_level)
NAMES = {str(k): str(v) for k, v in _cfg.eval.precomputed_models.items()}

needs_release = pytest.mark.skipif(
    not (SCORES_DIR.is_dir() and REFERENCE.is_file() and SUMMARY.is_file()),
    reason="needs the downloaded ProteinGym release (`mutascope data download`)",
)


@pytest.fixture(scope="module")
def our_spearman() -> pd.DataFrame:
    """Our per-assay Spearman of every precomputed model over all rows: model, assay_id, value."""
    variants = load_precomputed_scores(SCORES_DIR, list(NAMES), "all")
    table = assay_metrics_table(variants, {c: c for c in NAMES}, float(_cfg.eval.top_percent))
    return table[table["metric"] == "spearman"][["model", "assay_id", "value"]]


@pytest.fixture(scope="module")
def reference() -> pd.DataFrame:
    return pd.read_csv(REFERENCE)


@needs_release
def test_harness_reproduces_published_esm2_650m(
    our_spearman: pd.DataFrame, reference: pd.DataFrame
) -> None:
    published = published_summary(SUMMARY, NAMES)
    overall = published[published["level"] == "overall"].set_index("model")["value"]
    assert overall["ESM2_650M"] == 0.414  # the target ADR 0010 names
    for model, rows in our_spearman.groupby("model"):
        groups = AssayGroups.from_reference(reference, rows["assay_id"].tolist())
        ours = aggregate(rows["value"].to_numpy(np.float64), groups)
        assert len(rows) == 217
        assert abs(ours.overall - overall[model]) <= TOLERANCE + FLOAT_SLACK, (
            f"{PRECOMPUTED_PREFIX}{model}: ours {ours.overall:.5f}, published {overall[model]}"
        )
        functions = published[(published["model"] == model) & (published["level"] == "function")]
        for function, value in zip(functions["group"], functions["value"], strict=True):
            delta = ours.by_function[str(function)] - value
            assert abs(delta) <= FUNCTION_TOLERANCE + FLOAT_SLACK, (
                f"{model} {function}: {delta:+.5f}"
            )


@needs_release
def test_per_assay_spearman_matches_published_dms_level(our_spearman: pd.DataFrame) -> None:
    """Diagnostic for the metric: every assay rounds to ProteinGym's 3-decimal value."""
    published = published_assay_level(ASSAY_LEVEL, NAMES)
    joined = published.merge(our_spearman, on=["model", "assay_id"], suffixes=("_pub", ""))
    assert len(joined) == len(published) == 2 * 217
    gap = (joined["value"] - joined["value_pub"]).abs()
    worst = joined.iloc[int(np.argmax(gap.to_numpy()))]
    assert gap.max() <= TOLERANCE + FLOAT_SLACK, f"worst: {dict(worst)}"


@needs_release
def test_aggregation_of_published_values_is_exact(reference: pd.DataFrame) -> None:
    """Diagnostic for the aggregation: on ProteinGym's own (rounded) per-assay values, our
    aggregation gives the published aggregates exactly once rounded the way pandas does
    (half to even after scaling; Python's ``round`` would turn 0.3785 into 0.379, not 0.378)."""
    per_assay = published_assay_level(ASSAY_LEVEL, NAMES)
    published = published_summary(SUMMARY, NAMES)
    for model, rows in per_assay.groupby("model"):
        groups = AssayGroups.from_reference(reference, rows["assay_id"].tolist())
        ours = aggregate(rows["value"].to_numpy(np.float64), groups)
        expected = published[published["model"] == model]
        for level, group, value in zip(
            expected["level"], expected["group"], expected["value"], strict=True
        ):
            mine = ours.overall if level == "overall" else ours.by_function[str(group)]
            assert np.round(mine, 3) == value, f"{model} {level} {group}: {mine} vs {value}"


@pytest.mark.slow
def test_esm2_650m_matches_proteingym_scores_on_single_mutants() -> None:
    pytest.skip("Model gate is implemented in Phase 2 (zero-shot scorers).")
