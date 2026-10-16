"""Supervised models by name (``train.model``), built from ``train.params.<name>``."""

from collections.abc import Callable, Mapping
from functools import partial
from typing import Any, Final

import numpy as np

from mutascope.models.baselines import TrainingMean, ZeroShot
from mutascope.models.cv import ModelFactory
from mutascope.models.ridge import InnerCVRidge


def _alphas(params: Mapping[str, Any]) -> list[float]:
    """``{log10_min, log10_max, num}`` -> evenly spaced penalties on a log scale."""
    grid = params["alphas"]
    return [
        float(a)
        for a in np.logspace(float(grid["log10_min"]), float(grid["log10_max"]), int(grid["num"]))
    ]


def _ridge(params: Mapping[str, Any]) -> ModelFactory:
    # A partial, not a closure: `train cv --jobs` pickles the factory into worker processes.
    return partial(InnerCVRidge, _alphas(params), [str(c) for c in params.get("unpenalized", [])])


def _lgbm(params: Mapping[str, Any]) -> ModelFactory:
    # Imported here: importing the registry (every `mutascope` command does) must not load
    # LightGBM's OpenMP runtime into a process that scores with torch (ADR 0022).
    from mutascope.models.lgbm import InnerCVLightGBM, search_space

    components = params.get("pca_components")
    return partial(
        InnerCVLightGBM,
        search_space(params["search"]),
        dict(params["fixed"]),
        n_trials=int(params["n_trials"]),
        max_rounds=int(params["max_rounds"]),
        patience=int(params["patience"]),
        seed=int(params["seed"]),
        pca_components=None if components is None else int(components),
    )


def _mlp(params: Mapping[str, Any]) -> ModelFactory:
    # mlp imports torch only inside fit, so the registry still loads neither library.
    from mutascope.models.mlp import EarlyStoppedMLP

    components = params.get("pca_components")
    return partial(
        EarlyStoppedMLP,
        loss=str(params["loss"]),
        hidden=[int(h) for h in params["hidden"]],
        dropout=float(params["dropout"]),
        learning_rate=float(params["learning_rate"]),
        weight_decay=float(params["weight_decay"]),
        batch_size=int(params["batch_size"]),
        max_epochs=int(params["max_epochs"]),
        patience=int(params["patience"]),
        pca_components=None if components is None else int(components),
        seed=int(params["seed"]),
    )


MODELS: Final[Mapping[str, Callable[[Mapping[str, Any]], ModelFactory]]] = {
    "training_mean": lambda params: TrainingMean,
    "zero_shot": lambda params: ZeroShot,
    "ridge": _ridge,
    "augmented_ridge": _ridge,
    "lgbm": _lgbm,
    "lgbm_pca": _lgbm,
    "mlp_mse": _mlp,
    "mlp_rank": _mlp,
}
"""Name -> builder of a model factory from that model's params. ``ridge`` and
``augmented_ridge`` are one class; the latter's params leave the zero-shot score unpenalised.
``lgbm`` and ``lgbm_pca`` are one class; the latter's params reduce each embedding by PCA.
``mlp_mse`` and ``mlp_rank`` are one class, trained with MSE or the RankNet loss (ADR 0023)."""


def model_factory(name: str, params: Mapping[str, Any] | None = None) -> ModelFactory:
    """The factory registered as ``name``, configured with ``params``.

    Raises:
        KeyError: ``name`` is not a known model, or a required param is missing.
    """
    if name not in MODELS:
        raise KeyError(f"unknown model {name!r}; known: {sorted(MODELS)}")
    return MODELS[name](params or {})
