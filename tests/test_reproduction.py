"""Reproduction gate (CLAUDE.md): ESM-2 650M zero-shot average Spearman must match
ProteinGym's published value within ±0.01. Implemented in Phase 2.
"""

import pytest


@pytest.mark.slow
def test_esm2_650m_reproduces_proteingym() -> None:
    pytest.skip("Reproduction gate is implemented in Phase 2 (zero-shot scorers).")
