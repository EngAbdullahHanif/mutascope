"""The ``Scorer`` protocol every zero-shot model implements, and the registry that builds one.

A scorer turns a sequence into a landscape: an ``(L, 20)`` array whose entry ``[i, a]`` is the
score of substituting residue ``i`` (0-indexed) by ``AA_ORDER[a]``, defined as
``log P(a | x) - log P(wt_i | x)``. With ``fast=False`` (the default) ``x`` is the sequence with
position ``i`` masked (masked marginal, ProteinGym's strategy); with ``fast=True`` it is the
unmasked sequence, one forward pass for all positions (wild-type marginal). The wild-type column
is 0 by construction. Higher means "predicted fitter", the same direction as ``DMS_score``.

The registry maps a scorer name (``model.scorer`` in the config) to the module that implements
it. Modules are imported only when a scorer is built, so commands that never score never pay for
importing torch.
"""

import importlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np
import numpy.typing as npt

from mutascope.alphabet import AA_ORDER
from mutascope.data.parse import parse_mutation

FloatArray = npt.NDArray[np.float32]

_AA_INDEX = {aa: i for i, aa in enumerate(AA_ORDER)}


@runtime_checkable
class Scorer(Protocol):
    """A zero-shot model that scores every single substitution of a sequence."""

    @property
    def model_id(self) -> str:
        """Short name used in results, e.g. ``esm2_650m``."""
        ...

    def landscape(
        self, seq: str, struct_tokens: str | None = None, *, fast: bool = False
    ) -> FloatArray:
        """``(L, 20)`` scores in ``AA_ORDER``; see the module docstring."""
        ...


class ScorerConfigError(ValueError):
    """The model config names an unknown scorer or lacks a field."""


SCORERS: Mapping[str, str] = {"esm2": "mutascope.scoring.esm2"}
"""Scorer name -> module exposing ``from_config(model_cfg, *, device, cache_dir)``."""


def build_scorer(model_cfg: Mapping[str, Any], *, device: str, cache_dir: Path | None) -> Scorer:
    """Build the scorer named by ``model_cfg["scorer"]``.

    Args:
        model_cfg: One ``conf/model/*.yaml`` group, resolved to a plain mapping.
        device: ``"auto"``, ``"mps"`` or ``"cpu"`` (see :mod:`mutascope.utils.device`).
        cache_dir: Root of the log-probability cache, or None to disable it.
    """
    name = str(model_cfg.get("scorer"))
    if name not in SCORERS:
        raise ScorerConfigError(f"unknown scorer {name!r}; known: {sorted(SCORERS)}")
    module = importlib.import_module(SCORERS[name])
    scorer = module.from_config(model_cfg, device=device, cache_dir=cache_dir)
    if not isinstance(scorer, Scorer):
        raise TypeError(f"{SCORERS[name]}.from_config did not return a Scorer")
    return scorer


def check_sequence(seq: str) -> None:
    """Fail unless ``seq`` is non-empty and uses only the 20 canonical amino acids."""
    if not seq or set(seq) - set(AA_ORDER):
        bad = sorted(set(seq) - set(AA_ORDER))
        raise ValueError(f"sequence must be non-empty canonical amino acids; found {bad}")


def landscape_from_log_probs(log_probs: FloatArray, seq: str) -> FloatArray:
    """``log P(a) - log P(wt)`` per position, from ``(L, 20)`` log-probabilities in AA_ORDER."""
    if log_probs.shape != (len(seq), len(AA_ORDER)):
        raise ValueError(f"log_probs shape {log_probs.shape} does not match length {len(seq)}")
    wt = np.array([_AA_INDEX[aa] for aa in seq])
    return (log_probs - log_probs[np.arange(len(seq)), wt][:, None]).astype(np.float32)


def score_mutants(landscape: FloatArray, seq: str, mutants: Sequence[str]) -> FloatArray:
    """Look up single substitutions (``"A123G"``, 1-indexed) in a landscape.

    Positions are converted by :func:`~mutascope.data.parse.parse_mutation`, the only place
    that does it, and each wild type is checked against ``seq``.

    Raises:
        ValueError: A wild type does not match ``seq``, or a position is out of range.
    """
    scores = np.empty(len(mutants), dtype=np.float32)
    for row, text in enumerate(mutants):
        sub = parse_mutation(text)
        if sub.position >= len(seq) or seq[sub.position] != sub.wt:
            found = seq[sub.position] if sub.position < len(seq) else "out of range"
            raise ValueError(f"row {row}: {text} but the sequence has {found}")
        scores[row] = landscape[sub.position, _AA_INDEX[sub.mt]]
    return scores
