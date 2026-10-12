"""The ``Scorer`` protocol every zero-shot model implements, and the registry that builds one.

A scorer turns a sequence into a landscape: an ``(L, 20)`` array whose entry ``[i, a]`` is the
score of substituting residue ``i`` (0-indexed) by ``AA_ORDER[a]``, defined as
``log P(a | x) - log P(wt_i | x)``. With ``fast=False`` (the default) ``x`` is the sequence with
position ``i`` masked (masked marginal, ProteinGym's strategy); with ``fast=True`` it is the
unmasked sequence, one forward pass for all positions (wild-type marginal). The wild-type column
is 0 by construction. Higher means "predicted fitter", the same direction as ``DMS_score``.

``region`` labels which part of the assay's target ``seq`` is (``"full"`` or ``"msa:<a>-<b>"``,
see :mod:`mutascope.eval.region`). The scorer scores ``seq`` as given; the label only enters
the cache key, so landscapes of different regions can never be confused on disk.

``positions`` limits the work to some residues (0-indexed in ``seq``): the benchmark needs only
the positions an assay measured. The landscape keeps its ``(L, 20)`` shape, and a row that was
neither requested nor scored earlier is NaN, wild-type column included, so "not scored" can
never be read as a score. ``None`` scores every residue.

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

    @property
    def positions_computed(self) -> int:
        """Residues run through the model so far (cache hits excluded).

        Lets a caller tell a computation from a cache hit, e.g. to report what a resumed run
        still had to do.
        """
        ...

    def landscape(
        self,
        seq: str,
        struct_tokens: str | None = None,
        *,
        fast: bool = False,
        region: str = "full",
        positions: Sequence[int] | None = None,
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


def resolve_positions(positions: Sequence[int] | None, length: int) -> list[int]:
    """The residues to score, sorted and without duplicates; every residue for ``None``.

    Raises:
        ValueError: A position is outside ``0..length - 1``.
    """
    if positions is None:
        return list(range(length))
    wanted = sorted(set(positions))
    if wanted and not 0 <= wanted[0] <= wanted[-1] < length:
        raise ValueError(f"positions must be within 0..{length - 1}, got {wanted[0]}..{wanted[-1]}")
    return wanted


def landscape_from_log_probs(log_probs: FloatArray, seq: str) -> FloatArray:
    """``log P(a) - log P(wt)`` per position, from ``(L, 20)`` log-probabilities in AA_ORDER.

    A NaN row (a residue not scored) stays NaN.
    """
    if log_probs.shape != (len(seq), len(AA_ORDER)):
        raise ValueError(f"log_probs shape {log_probs.shape} does not match length {len(seq)}")
    wt = np.array([_AA_INDEX[aa] for aa in seq])
    return (log_probs - log_probs[np.arange(len(seq)), wt][:, None]).astype(np.float32)


def mutant_positions(seq: str, mutants: Sequence[str], *, offset: int = 0) -> list[int]:
    """The distinct residues of ``seq`` (0-indexed, sorted) that ``mutants`` substitute.

    Same arguments and checks as :func:`score_mutants`; call it before scoring so that only
    these residues are scored and a bad mutant fails before any model runs.
    """
    return sorted({local for local, _ in _locate(seq, mutants, offset)})


def score_mutants(
    landscape: FloatArray, seq: str, mutants: Sequence[str], *, offset: int = 0
) -> FloatArray:
    """Look up single substitutions (``"A123G"``, 1-indexed) in a landscape.

    Positions are converted by :func:`~mutascope.data.parse.parse_mutation`, the only place
    that does it, and each wild type is checked against ``seq``.

    Args:
        landscape: ``(len(seq), 20)`` scores.
        seq: The scored sequence.
        mutants: Mutations in target coordinates.
        offset: Target position (0-based) of ``seq[0]``; non-zero when ``seq`` is a region.

    Raises:
        ValueError: A wild type does not match ``seq``, or a position is outside ``seq``.
    """
    located = _locate(seq, mutants, offset)
    rows = np.array([local for local, _ in located], dtype=np.intp)
    columns = np.array([column for _, column in located], dtype=np.intp)
    return landscape[rows, columns].astype(np.float32)


def _locate(seq: str, mutants: Sequence[str], offset: int) -> list[tuple[int, int]]:
    """``(row, column)`` of each mutant in a landscape of ``seq``, wild types checked."""
    located: list[tuple[int, int]] = []
    for row, text in enumerate(mutants):
        sub = parse_mutation(text)
        local = sub.position - offset
        if not 0 <= local < len(seq) or seq[local] != sub.wt:
            found = seq[local] if 0 <= local < len(seq) else "no residue there"
            raise ValueError(f"row {row}: {text} but the scored sequence has {found}")
        located.append((local, _AA_INDEX[sub.mt]))
    return located
