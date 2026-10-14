"""The feature matrix a supervised model is trained on: dense columns plus a site one-hot.

Hsu et al. (2022) one-hot encode the whole sequence: 20 columns per residue, ``20 * L`` in all.
Stored densely that is up to 68,460 columns for the longest target (3,423 residues), most of
them zero in any one row. For a single mutant the encoding differs from the wild type's in two
places only: residue i's wild-type column turns off and its mutant column turns on. A constant
row shift changes nothing for a model with an intercept, so the variant's row is kept as those
two entries, ``+1`` at ``(i, mt)`` and ``-1`` at ``(i, wt)`` (:class:`SiteOneHot`), and the
models multiply by it without ever forming the matrix.
"""

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from mutascope.alphabet import AA_ORDER

FloatArray = npt.NDArray[np.float64]
FloatMatrix = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]
BoolArray = npt.NDArray[np.bool_]

SITE_WIDTH = len(AA_ORDER)
"""Site one-hot columns per residue."""
_AA_INDEX = {aa: i for i, aa in enumerate(AA_ORDER)}


@dataclass(frozen=True)
class SiteOneHot:
    """Mutant minus wild-type one-hot sequence of each variant: two nonzero entries per row.

    Column ``20 * i + a`` is amino acid ``AA_ORDER[a]`` at residue i (0-indexed).
    """

    plus: IntArray
    """Column of each row's ``+1``: the mutant residue at its position."""
    minus: IntArray
    """Column of each row's ``-1``: the wild-type residue at its position."""
    width: int
    """``20 * L``: every residue of the target has its columns, measured or not."""

    def __post_init__(self) -> None:
        if self.plus.shape != self.minus.shape or self.plus.ndim != 1:
            raise ValueError("plus and minus must be 1-D arrays of the same length")
        if (self.plus == self.minus).any():
            raise ValueError("a row's mutant and wild-type columns coincide (a synonymous row)")
        for columns in (self.plus, self.minus):
            if columns.size and not (columns.min() >= 0 and columns.max() < self.width):
                raise ValueError(f"column index outside 0..{self.width - 1}")

    @classmethod
    def from_keys(
        cls, position: IntArray, wt_aa: npt.ArrayLike, mt_aa: npt.ArrayLike, length: int
    ) -> "SiteOneHot":
        """Rows for variants given as 0-indexed position, wild-type and mutant amino acid."""
        wt = np.array([_AA_INDEX[str(a)] for a in np.asarray(wt_aa)], dtype=np.int64)
        mt = np.array([_AA_INDEX[str(a)] for a in np.asarray(mt_aa)], dtype=np.int64)
        base = np.asarray(position, dtype=np.int64) * SITE_WIDTH
        return cls(plus=base + mt, minus=base + wt, width=length * SITE_WIDTH)

    @property
    def n_rows(self) -> int:
        return int(self.plus.shape[0])

    def take(self, rows: BoolArray | IntArray) -> "SiteOneHot":
        return SiteOneHot(plus=self.plus[rows], minus=self.minus[rows], width=self.width)

    def matvec(self, coef: FloatArray) -> FloatArray:
        """``X @ coef``: one value per row."""
        return coef[self.plus] - coef[self.minus]

    def rmatvec(self, values: FloatArray) -> FloatArray:
        """``X.T @ values``: one value per column."""
        plus = np.bincount(self.plus, values, self.width).astype(np.float64)
        return plus - np.bincount(self.minus, values, self.width).astype(np.float64)

    def column_sq_norms(self) -> FloatArray:
        """Squared norm of every column (the diagonal of ``X.T @ X``)."""
        return np.bincount(self.plus, minlength=self.width).astype(np.float64) + np.bincount(
            self.minus, minlength=self.width
        ).astype(np.float64)

    def to_dense(self) -> FloatMatrix:
        """The full matrix. For tests and small assays only."""
        out = np.zeros((self.n_rows, self.width))
        rows = np.arange(self.n_rows)
        out[rows, self.plus] = 1.0
        out[rows, self.minus] = -1.0
        return out


@dataclass(frozen=True)
class FeatureMatrix:
    """One assay's features: ``dense`` columns, optionally followed by a :class:`SiteOneHot`."""

    dense: FloatMatrix
    names: tuple[str, ...] = field(default=())
    """One name per dense column (e.g. ``esm2_650m:score``); may be empty for unnamed tests."""
    site: SiteOneHot | None = None

    def __post_init__(self) -> None:
        if self.dense.ndim != 2:
            raise ValueError(f"dense features must be 2-D, got shape {self.dense.shape}")
        if self.names and len(self.names) != self.dense.shape[1]:
            raise ValueError(f"{len(self.names)} names for {self.dense.shape[1]} dense columns")
        if self.site is not None and self.site.n_rows != self.dense.shape[0]:
            raise ValueError(f"site one-hot has {self.site.n_rows} rows, dense {self.n_rows}")

    @classmethod
    def of(cls, dense: npt.ArrayLike) -> "FeatureMatrix":
        """Dense, unnamed features."""
        return cls(dense=np.asarray(dense, dtype=np.float64))

    @property
    def n_rows(self) -> int:
        return int(self.dense.shape[0])

    def take(self, rows: BoolArray | IntArray) -> "FeatureMatrix":
        site = None if self.site is None else self.site.take(rows)
        return FeatureMatrix(dense=self.dense[rows], names=self.names, site=site)

    def column(self, name: str) -> int:
        """Index of the dense column called ``name``.

        Raises:
            KeyError: No dense column has that name.
        """
        if name not in self.names:
            raise KeyError(f"no feature column {name!r}; columns include {list(self.names[:5])}")
        return self.names.index(name)
