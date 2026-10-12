"""One call to seed every random number generator a stochastic step may use."""

import random

import numpy as np


def seed_all(seed: int) -> np.random.Generator:
    """Seed Python's and numpy's global generators, and return a fresh numpy ``Generator``.

    New code should draw from the returned generator and pass it down explicitly; the global
    seeds cover library code that still uses the legacy global state. torch is seeded here too
    once it is a dependency (Phase 2 scorers).
    """
    random.seed(seed)
    np.random.seed(seed)  # legacy global state, still used by some libraries
    return np.random.default_rng(seed)
