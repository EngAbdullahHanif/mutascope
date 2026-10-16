"""PCA of each embedding block, fitted on the rows a model is trained on (ADR 0022).

A store feature set carries one 1,280-column embedding per zero-shot model
(``esm2_650m:embedding_0`` … ``_1279``). :class:`EmbeddingPCA` replaces each such block with its
leading principal components and leaves every other column as it is. It is fitted inside a
model's ``fit``, so in the CV loop it only ever sees training rows: the test fold cannot shape
the components, the mean they are centred on, or how many there are.

The decomposition is an eigendecomposition of the centred block's Gram matrix, whichever of
``X'X`` (rows ≥ columns) and ``XX'`` is smaller, so a 300-row training set costs a 300 x 300
problem, not 1,280 x 1,280. Each component's sign is fixed (its largest loading is positive), so
the same rows always give the same features.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from mutascope.features.matrix import FeatureMatrix, FloatArray, FloatMatrix

EMBEDDING_COLUMN = re.compile(r"^(?P<model>[^:]+):embedding_\d+$")
"""Name of an embedding column in a store feature set (:mod:`mutascope.features.sets`)."""
_RANK_TOL = 1e-10


class PCAError(ValueError):
    """The features cannot be reduced as asked (no embedding block, unnamed columns...)."""


@dataclass(frozen=True)
class BlockComponents:
    """The fitted PCA of one embedding block."""

    model: str
    columns: tuple[int, ...]
    """Dense columns of the block, in order."""
    mean: FloatArray
    components: FloatMatrix
    """``(k, width)``: one unit-length component per row, largest variance first."""


def embedding_blocks(names: Sequence[str]) -> dict[str, list[int]]:
    """Columns of each ``<model>:embedding_<i>`` block, by model, in column order."""
    blocks: dict[str, list[int]] = {}
    for j, name in enumerate(names):
        match = EMBEDDING_COLUMN.match(name)
        if match:
            blocks.setdefault(match["model"], []).append(j)
    return blocks


def principal_components(block: FloatMatrix, n_components: int) -> tuple[FloatArray, FloatMatrix]:
    """Mean and the leading ``n_components`` components of ``block``'s rows.

    Fewer are returned when the centred rows have lower rank (a training set of 30 rows has at
    most 29 components with nonzero variance).
    """
    n, width = block.shape
    mean = block.mean(axis=0)
    centred = block - mean
    if n >= width:
        eigval, eigvec = np.linalg.eigh(centred.T @ centred)
        order = np.argsort(eigval)[::-1]
        eigval, components = eigval[order], eigvec[:, order].T
    else:
        # Left singular vectors from the n x n Gram matrix, mapped back to the columns.
        eigval, eigvec = np.linalg.eigh(centred @ centred.T)
        order = np.argsort(eigval)[::-1]
        eigval = eigval[order]
        keep = eigval > _RANK_TOL * max(float(eigval[0]), 1.0)
        components = (centred.T @ eigvec[:, order][:, keep] / np.sqrt(eigval[keep])).T
        eigval = eigval[keep]
    rank = int(np.sum(eigval > _RANK_TOL * max(float(eigval[0]), 1.0))) if eigval.size else 0
    components = components[: min(n_components, rank)]
    # Sign convention: the largest-magnitude loading of each component is positive.
    peak = np.abs(components).argmax(axis=1)
    signs = np.sign(components[np.arange(components.shape[0]), peak])
    return mean, np.asarray(components * signs[:, None], dtype=np.float64)


class EmbeddingPCA:
    """Replaces each embedding block with its first ``n_components`` principal components.

    The output columns are the non-embedding columns in their original order, then each
    model's components, named ``<model>:embedding_pc<k>`` (1-based).
    """

    def __init__(self, n_components: int) -> None:
        if n_components < 1:
            raise PCAError(f"n_components must be at least 1, got {n_components}")
        self.n_components = n_components
        self.blocks_: tuple[BlockComponents, ...] | None = None
        self.kept_: tuple[int, ...] = ()
        self.names_: tuple[str, ...] = ()

    def fit(self, x: FeatureMatrix) -> "EmbeddingPCA":
        if not x.names:
            raise PCAError("EmbeddingPCA needs named columns to find the embedding blocks")
        blocks = embedding_blocks(x.names)
        if not blocks:
            raise PCAError("no <model>:embedding_<i> columns to reduce")
        fitted: list[BlockComponents] = []
        for model, columns in blocks.items():
            mean, components = principal_components(x.dense[:, columns], self.n_components)
            fitted.append(BlockComponents(model, tuple(columns), mean, components))
        in_blocks = {j for columns in blocks.values() for j in columns}
        self.kept_ = tuple(j for j in range(x.dense.shape[1]) if j not in in_blocks)
        self.blocks_ = tuple(fitted)
        self.names_ = tuple(x.names[j] for j in self.kept_) + tuple(
            f"{b.model}:embedding_pc{k + 1}" for b in fitted for k in range(b.components.shape[0])
        )
        return self

    def transform(self, x: FeatureMatrix) -> FeatureMatrix:
        if self.blocks_ is None:
            raise RuntimeError("EmbeddingPCA.transform called before fit")
        parts = [x.dense[:, list(self.kept_)]]
        parts += [(x.dense[:, list(b.columns)] - b.mean) @ b.components.T for b in self.blocks_]
        return FeatureMatrix(dense=np.hstack(parts), names=self.names_, site=x.site)
