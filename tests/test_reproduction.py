"""Reproduction gate (CLAUDE.md, ADR 0010), in two parts that must both pass.

Harness gate: our metrics and aggregation on ProteinGym's precomputed ``ESM2_650M`` scores over
all rows (singles and multi-mutants) reproduce the published average Spearman 0.414.

Model gate: on single mutants, our ESM-2 650M scores have Spearman >= 0.99 with ProteinGym's
precomputed scores in every assay, and our aggregate is within ±0.01 of the aggregate the same
harness computes from their scores on the same rows.

Both are implemented in Phase 2.
"""

import pytest


def test_harness_reproduces_published_esm2_650m() -> None:
    pytest.skip("Harness gate is implemented in Phase 2 (benchmark harness).")


@pytest.mark.slow
def test_esm2_650m_matches_proteingym_scores_on_single_mutants() -> None:
    pytest.skip("Model gate is implemented in Phase 2 (zero-shot scorers).")
