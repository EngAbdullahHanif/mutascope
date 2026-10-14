"""Supervised models by name (``train.model``), built from ``train.params.<name>``."""

from collections.abc import Callable, Mapping
from typing import Any, Final

import numpy as np

from mutascope.models.baselines import TrainingMean, ZeroShot
from mutascope.models.cv import ModelFactory, Regressor
from mutascope.models.ridge import InnerCVRidge


def _alphas(params: Mapping[str, Any]) -> list[float]:
    """``{log10_min, log10_max, num}`` -> evenly spaced penalties on a log scale."""
    grid = params["alphas"]
    return [
        float(a)
        for a in np.logspace(float(grid["log10_min"]), float(grid["log10_max"]), int(grid["num"]))
    ]


def _ridge(params: Mapping[str, Any]) -> ModelFactory:
    alphas = _alphas(params)
    unpenalized = [str(c) for c in params.get("unpenalized", [])]

    def make() -> Regressor:
        return InnerCVRidge(alphas, unpenalized)

    return make


MODELS: Final[Mapping[str, Callable[[Mapping[str, Any]], ModelFactory]]] = {
    "training_mean": lambda params: TrainingMean,
    "zero_shot": lambda params: ZeroShot,
    "ridge": _ridge,
    "augmented_ridge": _ridge,
}
"""Name -> builder of a model factory from that model's params. ``ridge`` and
``augmented_ridge`` are one class; the latter's params leave the zero-shot score unpenalised."""


def model_factory(name: str, params: Mapping[str, Any] | None = None) -> ModelFactory:
    """The factory registered as ``name``, configured with ``params``.

    Raises:
        KeyError: ``name`` is not a known model, or a required param is missing.
    """
    if name not in MODELS:
        raise KeyError(f"unknown model {name!r}; known: {sorted(MODELS)}")
    return MODELS[name](params or {})
