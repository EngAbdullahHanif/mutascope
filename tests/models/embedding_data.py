"""Synthetic named features with two embedding blocks, for the LightGBM and PCA tests."""

import numpy as np

from mutascope.features.matrix import FeatureMatrix

WIDTH = 24
"""Columns per synthetic embedding block (1,280 in the real store)."""


def features(n: int, seed: int = 0) -> tuple[FeatureMatrix, np.ndarray]:
    """Two named embedding blocks plus two scalar columns; y is a nonlinear function of them."""
    rng = np.random.default_rng(seed)
    latent = rng.normal(size=(n, 3))
    blocks = [
        latent @ rng.normal(size=(3, WIDTH)) + 0.1 * rng.normal(size=(n, WIDTH)) for _ in "ab"
    ]
    score = latent[:, 0] + 0.3 * rng.normal(size=n)
    other = rng.normal(size=n)
    dense = np.column_stack([score, *blocks, other])
    names = (
        "esm2_650m:score",
        *(f"esm2_650m:embedding_{i}" for i in range(WIDTH)),
        *(f"saprot_650m_af2:embedding_{i}" for i in range(WIDTH)),
        "blosum62",
    )
    y = np.tanh(2 * latent[:, 0]) + 0.5 * latent[:, 1] ** 2 + 0.2 * rng.normal(size=n)
    return FeatureMatrix(dense=dense, names=names), y
