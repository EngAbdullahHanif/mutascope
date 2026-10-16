"""Torch and LightGBM never load into one process (ADR 0022).

On macOS LightGBM's wheel links Homebrew's OpenMP runtime and torch bundles another. With torch
loaded first, LightGBM segfaulted on its first ``Dataset.construct()``; with LightGBM loaded
first, ESM-2's forward pass segfaulted. So importing the CLI (every command and every
``train --jobs`` worker does) loads neither, and each command loads only the one it needs.
Checked in fresh interpreters: this test process has its own imports.
"""

import subprocess
import sys

import pytest


def python(code: str) -> str:
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=300, check=False
    )
    assert done.returncode == 0, done.stderr[-2000:]
    return done.stdout.strip()


@pytest.mark.parametrize(
    "module", ["mutascope.cli", "mutascope.models.registry", "mutascope.models.lgbm"]
)
def test_importing_loads_neither_torch_nor_lightgbm(module: str) -> None:
    loaded = python(
        f"import sys, {module}; print('torch' in sys.modules, 'lightgbm' in sys.modules)"
    )
    assert loaded == "False False"


def test_a_lightgbm_fit_loads_lightgbm_but_not_torch() -> None:
    loaded = python(
        """
import sys
import numpy as np
from mutascope.features.matrix import FeatureMatrix
from mutascope.models.registry import model_factory
model = model_factory("lgbm", {
    "n_trials": 2, "max_rounds": 20, "patience": 5, "seed": 0,
    "fixed": {"objective": "regression", "metric": "l2", "num_threads": 1, "verbose": -1},
    "search": {"num_leaves": {"low": 4, "high": 8, "int": True}},
})()
x = np.random.default_rng(0).normal(size=(120, 6))
model.fit(FeatureMatrix.of(x), x[:, 0], np.arange(120) % 3)
print('torch' in sys.modules, 'lightgbm' in sys.modules)
"""
    )
    assert loaded == "False True"
