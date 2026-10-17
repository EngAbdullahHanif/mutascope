"""Live scoring: the two served ESM-2 models, fast landscapes through the cache, mutation checks.

A request names a model by its short key (``esm2_35m``, ``esm2_150m``); each key is one fp32
ONNX config (ADR 0029). Fast mode is the wild-type marginal, one forward pass, computed inside
the request. Precise mode (the masked marginal, 35M only) is a job (:mod:`api.app.jobs`); it is
split here into the scorer's own forward calls, so each progress event is one finished batch.

Scores are rounded to :data:`DECIMALS` places in responses. That is 1e-4 nats, against a landscape
SD of about 3 and an fp32-vs-PyTorch logit difference below 1e-4 (ADR 0029).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np

from api.app.score_cache import ScoreCache, cache_key
from mutascope.data.parse import parse_mutation
from mutascope.scoring.base import FloatArray
from mutascope.scoring.marginals import RESIDUE_OFFSET
from mutascope.scoring.onnx_esm2 import OnnxEsm2Scorer, OnnxEsm2Settings

DECIMALS = 4
"""Decimal places of every live score in a response."""


class LiveModelName(StrEnum):
    """A model served live, by the key a request uses."""

    esm2_35m = "esm2_35m"
    esm2_150m = "esm2_150m"


LIVE_CONFIGS: Mapping[LiveModelName, str] = {
    LiveModelName.esm2_35m: "esm2_35m_onnx_fp32",
    LiveModelName.esm2_150m: "esm2_150m_onnx_fp32",
}
"""Request key -> model config (``conf/model/<name>.yaml``)."""
PRECISE_MODEL = LiveModelName.esm2_35m
"""Precise mode runs on 35M only: 150M takes 9-10 min at 1,000 residues (ADR 0029)."""


@dataclass(frozen=True)
class LiveModel:
    """One served model and the identity its results are cached and reported under."""

    key: LiveModelName
    scorer: OnnxEsm2Scorer
    revision: str
    """The Hub commit of the files; ``local:<dir>`` when they were read from an export
    directory, so stand-in or unreleased files never share cache entries with released ones."""
    file: str
    """The ``.onnx`` file in the Hub repository."""

    @property
    def name(self) -> str:
        """The model config's name, e.g. ``esm2_150m_onnx_fp32``."""
        return self.scorer.model_id

    @property
    def hf_id(self) -> str:
        return self.scorer.settings.hf_id

    def key_for(self, method: str, sequence: str) -> str:
        """The score cache key of ``sequence`` under ``method`` (``"wt"`` or ``"masked"``)."""
        return cache_key(self.scorer.cache_id, self.revision, method, sequence)

    def precise_batches(self, sequence: str) -> list[list[int]]:
        """Residues (0-indexed) of each masked forward call, as the scorer would batch them."""
        n_tokens = len(sequence) + 2  # <cls> residues <eos>; encode() checks this layout
        batches = self.scorer.masked_batches(n_tokens, range(len(sequence)))
        return [[p.position - RESIDUE_OFFSET for p in batch] for batch in batches]


class LiveModelError(RuntimeError):
    """A served model is missing from the loaded scorers."""


def live_models(
    scorers: Mapping[str, OnnxEsm2Scorer], *, revision: str, local_dir: Path | None
) -> dict[LiveModelName, LiveModel]:
    """The live models among the loaded scorers; precise mode's model must be one.

    Raises:
        LiveModelError: The precise-mode model is not loaded.
    """
    label = revision if local_dir is None else f"local:{local_dir}"
    models: dict[LiveModelName, LiveModel] = {}
    for key, config in LIVE_CONFIGS.items():
        if config not in scorers:
            continue
        scorer = scorers[config]
        settings = scorer.settings
        if not isinstance(settings, OnnxEsm2Settings):
            raise LiveModelError(f"{config}: not an ONNX scorer's settings")
        models[key] = LiveModel(key=key, scorer=scorer, revision=label, file=settings.file)
    if PRECISE_MODEL not in models:
        raise LiveModelError(f"precise mode needs {LIVE_CONFIGS[PRECISE_MODEL]}, not loaded")
    return models


def fast_landscape(model: LiveModel, sequence: str, cache: ScoreCache) -> tuple[FloatArray, bool]:
    """The wild-type-marginal landscape of ``sequence`` and whether it came from the cache.

    Blocking (one forward pass on a cache miss): call it from a worker thread.
    """
    key = model.key_for("wt", sequence)
    cached = cache.get(key, len(sequence))
    if cached is not None:
        return cached, True
    landscape = model.scorer.landscape(sequence, fast=True)
    cache.put(key, landscape)
    return landscape, False


def to_grid(landscape: FloatArray) -> list[float | None]:
    """``(L, 20)`` scores to a row-major list, rounded to :data:`DECIMALS`; NaN to null."""
    flat = np.round(landscape.astype(np.float64).ravel(), DECIMALS)
    return [None if np.isnan(v) else v for v in flat.tolist()]


def mutation_errors(sequence: str, mutations: list[str]) -> list[dict[str, Any]]:
    """Validation errors of ``mutations`` (already well-formed) against ``sequence``.

    Each must name a residue of the sequence and its wild type. Every failing mutation is
    reported, in the shape of a request validation error, so a client sees them all at once.
    """
    errors: list[dict[str, Any]] = []
    for index, text in enumerate(mutations):
        sub = parse_mutation(text)  # the only 1-indexed -> 0-indexed conversion
        if sub.position >= len(sequence):
            msg = f"{text}: position {sub.position + 1} is past the end ({len(sequence)} residues)"
        elif sequence[sub.position] != sub.wt:
            msg = (
                f"{text}: wild type {sub.wt} does not match the sequence, which has "
                f"{sequence[sub.position]} at position {sub.position + 1}"
            )
        else:
            continue
        errors.append({"loc": ("body", "mutations", index), "msg": msg, "type": "mutation"})
    return errors
