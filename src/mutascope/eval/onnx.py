"""Checks and timings of the ONNX export against the PyTorch model (ADR 0029).

Pure functions on arrays and callables; ``mutascope onnx check-parity`` and ``onnx latency``
feed them the two runtimes. Neither runtime is imported here.
"""

import time
from collections.abc import Callable, Sequence

import numpy as np
import numpy.typing as npt

from mutascope.alphabet import AA_ORDER
from mutascope.eval.metrics import spearman
from mutascope.scoring.marginals import RESIDUE_OFFSET

FloatArray = npt.NDArray[np.float32]
IntArray = npt.NDArray[np.int64]


def parity_inputs(tokens: IntArray, mask_id: int, masked: int) -> dict[str, IntArray]:
    """The two inputs of the logit check for one sequence's tokens ``<cls> residues <eos>``.

    ``wt``: the sequence unmasked, one row. ``masked``: ``masked`` rows, each with one residue
    replaced by ``mask_id``, the residues evenly spaced from the first to the last. Both kinds of
    pass are what the scorer runs, and a masked token changes ESM-2's embedding scaling
    (``token_dropout``), so each needs its own check.
    """
    length = len(tokens) - 2
    residues = np.unique(np.linspace(0, length - 1, num=min(masked, length)).round().astype(int))
    rows = np.repeat(tokens[None, :], len(residues), axis=0)
    rows[np.arange(len(residues)), RESIDUE_OFFSET + residues] = mask_id
    return {"wt": tokens[None, :].copy(), "masked": rows}


def max_abs_diff(a: FloatArray, b: FloatArray) -> float:
    """Largest absolute difference of two equally shaped arrays (an error if they are not)."""
    if a.shape != b.shape:
        raise ValueError(f"shapes differ: {a.shape} and {b.shape}")
    return float(np.abs(a.astype(np.float64) - b.astype(np.float64)).max())


def landscape_agreement(reference: FloatArray, other: FloatArray, seq: str) -> tuple[float, float]:
    """Spearman and largest absolute difference over the 19 x L substitutions of ``seq``.

    The wild-type column (0 in both by construction) is left out, so it cannot inflate the
    correlation.

    Raises:
        ValueError: Shapes differ, or either landscape has a NaN substitution.
    """
    if reference.shape != other.shape or reference.shape != (len(seq), len(AA_ORDER)):
        raise ValueError(f"landscapes of shape {reference.shape} and {other.shape}")
    off = np.ones(reference.shape, dtype=bool)
    off[np.arange(len(seq)), [AA_ORDER.index(aa) for aa in seq]] = False
    a, b = reference[off].astype(np.float64), other[off].astype(np.float64)
    if np.isnan(a).any() or np.isnan(b).any():
        raise ValueError("a landscape has unscored substitutions")
    return spearman(a, b), float(np.abs(a - b).max())


def random_sequence(length: int, rng: np.random.Generator) -> str:
    """A uniformly random canonical sequence: latency depends on the length, not the residues."""
    return "".join(rng.choice(list(AA_ORDER), size=length))


def time_calls(call: Callable[[], object], *, warmup: int, repeats: int) -> list[float]:
    """Wall-clock seconds of ``repeats`` calls of ``call`` after ``warmup`` untimed ones."""
    for _ in range(warmup):
        call()
    seconds: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        call()
        seconds.append(time.perf_counter() - start)
    return seconds


def tokenizer_mismatches(
    ours: Callable[[str], Sequence[int]],
    theirs: Callable[[str], Sequence[int]],
    sequences: Sequence[str],
) -> list[int]:
    """Indices of ``sequences`` on which two tokenizers give different ids."""
    return [i for i, seq in enumerate(sequences) if list(ours(seq)) != list(theirs(seq))]
