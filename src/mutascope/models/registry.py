"""Supervised models by name (``train.model``)."""

from collections.abc import Mapping
from typing import Final

from mutascope.models.baselines import TrainingMean
from mutascope.models.cv import ModelFactory

MODELS: Final[Mapping[str, ModelFactory]] = {"training_mean": TrainingMean}


def model_factory(name: str) -> ModelFactory:
    """The factory registered as ``name``.

    Raises:
        KeyError: ``name`` is not a known model.
    """
    if name not in MODELS:
        raise KeyError(f"unknown model {name!r}; known: {sorted(MODELS)}")
    return MODELS[name]
