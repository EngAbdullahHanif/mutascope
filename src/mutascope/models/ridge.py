"""Ridge regression whose penalty is chosen by cross-validation inside the training folds.

Three of the Phase 3 models are this one class with different inputs (ADR 0021):

- one-hot ridge: the site one-hot of Hsu et al. (2022) alone;
- augmented ridge (Hsu et al., 2022): the site one-hot plus a zero-shot score that is **not
  penalised**, so the one-hot terms only learn what the score gets wrong;
- ridge on all features: a feature-store set (``seq``, ``seq_struct``), every column penalised.

**Loss.** ``|y - b - Z g - X w|^2 + alpha |w|^2``: the intercept ``b`` and the unpenalised columns
``Z`` are free, the penalised columns ``X`` are shrunk. Freeing ``Z`` exactly (rather than giving
it a tiny penalty) uses the Frisch-Waugh-Lovell theorem: project ``[1, Z]`` out of ``X`` and
``y``, solve an ordinary ridge for ``w``, then regress what is left of ``y`` on ``[1, Z]``.

**Scaling.** Dense penalised columns are standardised with the mean and population standard
deviation of the rows being fit, so one penalty treats an embedding dimension and a BLOSUM62
score alike; a constant column is left at 0. The site one-hot is not scaled (Hsu et al. do not:
every entry is already 0 or 1).

**Penalty choice.** The fit gets each training row's outer fold. Each of those folds is held out
in turn, a path over every candidate ``alpha`` is fit on the rest, and the ``alpha`` with the
smallest squared error over all held-out rows is refit on the whole training set. The inner
folds are therefore split like the outer ones (contiguous blocks under ``contiguous``), and the
test fold never takes part (ADR 0019). Ties go to the larger ``alpha``.

**Solvers.** Dense: one eigendecomposition per training set gives every ``alpha`` for free
(of ``X'X`` when rows outnumber columns, else of ``XX'``). With a site one-hot (up to 68,460
columns, two nonzeros per row): Jacobi-preconditioned conjugate gradients on the normal
equations, from the largest ``alpha`` down, each started from the previous solution.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from mutascope.features.matrix import FeatureMatrix, FloatArray, FloatMatrix, IntArray, SiteOneHot

CG_RTOL = 1e-9
"""Conjugate gradients stop at ``|residual| <= CG_RTOL * |right-hand side|``."""
CG_MAX_ITER = 20_000
_RANK_TOL = 1e-10


class RidgeError(ValueError):
    """The ridge cannot be fit as asked (collinear free columns, too few folds, no convergence)."""


@dataclass(frozen=True)
class LinearFit:
    """``prediction = intercept + dense @ coef_dense + site @ coef_site`` on unscaled inputs."""

    intercept: float
    coef_dense: FloatArray
    coef_site: FloatArray | None

    def predict(self, x: FeatureMatrix) -> FloatArray:
        prediction = self.intercept + x.dense @ self.coef_dense
        if self.coef_site is not None:
            if x.site is None:
                raise RidgeError("fit with a site one-hot, asked to predict without one")
            prediction = prediction + x.site.matvec(self.coef_site)
        return prediction


def ridge_path(
    x: FeatureMatrix, y: FloatArray, alphas: Sequence[float], unpenalized: Sequence[int]
) -> list[LinearFit]:
    """One ridge fit per ``alpha`` (in the order given), with ``unpenalized`` dense columns free.

    Raises:
        RidgeError: The intercept and the unpenalised columns are collinear on these rows, or
            conjugate gradients do not converge.
    """
    n, d = x.dense.shape
    free = list(unpenalized)
    shrunk = [j for j in range(d) if j not in set(free)]
    z = np.hstack([np.ones((n, 1)), x.dense[:, free]])
    q, r = np.linalg.qr(z)
    diag = np.abs(np.diag(r))
    if diag.min() <= _RANK_TOL * diag.max():
        raise RidgeError("the intercept and the unpenalised columns are collinear")

    def project(v: FloatArray) -> FloatArray:
        """Remove the part of ``v`` (a vector or columns) that ``[1, Z]`` explains."""
        return v - q @ (q.T @ v)

    raw = x.dense[:, shrunk]
    mean = raw.mean(axis=0)
    scale = raw.std(axis=0)
    scale[scale == 0] = 1.0
    scaled = (raw - mean) / scale
    x_proj = project(scaled)
    y_proj = project(y)
    if x.site is None:
        shrunk_coefs = _dense_path(x_proj, y_proj, alphas)
        site_coefs: list[FloatArray | None] = [None] * len(alphas)
    else:
        site_coefs_found, shrunk_coefs = _site_path(x.site, x_proj, y_proj, alphas, project)
        site_coefs = list(site_coefs_found)
    fits: list[LinearFit] = []
    for w, w_site in zip(shrunk_coefs, site_coefs, strict=True):
        fitted = scaled @ w
        if w_site is not None and x.site is not None:
            fitted = fitted + x.site.matvec(w_site)
        # Free coefficients: least squares of what the penalised part leaves of y on [1, Z].
        g = np.asarray(np.linalg.solve(r, q.T @ (y - fitted)), dtype=np.float64)
        coef = np.zeros(d)
        coef[shrunk] = w / scale
        coef[free] = g[1:]
        fits.append(
            LinearFit(
                intercept=float(g[0] - np.sum(w * mean / scale)), coef_dense=coef, coef_site=w_site
            )
        )
    return fits


def _dense_path(x: FloatMatrix, y: FloatArray, alphas: Sequence[float]) -> list[FloatArray]:
    """``argmin |y - x w|^2 + alpha |w|^2`` for each alpha, from one eigendecomposition."""
    n, p = x.shape
    if p == 0:
        return [np.zeros(0) for _ in alphas]
    if n >= p:
        eigval, eigvec = np.linalg.eigh(x.T @ x)
        projected = eigvec.T @ (x.T @ y)
        eigval = np.clip(eigval, 0.0, None)
        return [eigvec @ (projected / (eigval + a)) for a in alphas]
    # Fewer rows than columns: w = x' (xx' + alpha I)^-1 y, from the n x n kernel.
    eigval, eigvec = np.linalg.eigh(x @ x.T)
    projected = eigvec.T @ y
    eigval = np.clip(eigval, 0.0, None)
    return [x.T @ (eigvec @ (projected / (eigval + a))) for a in alphas]


def _site_path(
    site: SiteOneHot,
    dense: FloatMatrix,
    y: FloatArray,
    alphas: Sequence[float],
    project: Callable[[FloatArray], FloatArray],
) -> tuple[list[FloatArray], list[FloatArray]]:
    """The same problem with site one-hot columns before ``dense``, by conjugate gradients.

    ``dense`` and ``y`` are already projected; the site columns are projected inside the
    operator, so the matrix is never formed.
    """
    width = site.width

    def forward(theta: FloatArray) -> FloatArray:
        return project(site.matvec(theta[:width]) + dense @ theta[width:])

    def backward(v: FloatArray) -> FloatArray:
        v = project(v)
        return np.concatenate([site.rmatvec(v), dense.T @ v])

    rhs = backward(y)
    # Jacobi preconditioner from the unprojected column norms: close enough to the diagonal.
    norms = np.concatenate([site.column_sq_norms(), (dense**2).sum(axis=0)])
    theta = np.zeros(width + dense.shape[1])
    solutions: dict[float, FloatArray] = {}
    for alpha in sorted(set(alphas), reverse=True):
        theta = _conjugate_gradients(
            lambda v, a=alpha: backward(forward(v)) + a * v,
            rhs,
            1.0 / (norms + alpha),
            theta,
        )
        solutions[alpha] = theta
    return (
        [solutions[a][:width] for a in alphas],
        [solutions[a][width:] for a in alphas],
    )


def _conjugate_gradients(
    apply: Callable[[FloatArray], FloatArray],
    rhs: FloatArray,
    precondition: FloatArray,
    start: FloatArray,
) -> FloatArray:
    """Solve ``apply(x) = rhs`` for symmetric positive definite ``apply``."""
    target = float(np.linalg.norm(rhs))
    if target == 0.0:
        return np.zeros_like(rhs)
    x = start.copy()
    residual = rhs - apply(x)
    z = precondition * residual
    direction = z.copy()
    rz = float(residual @ z)
    for _ in range(CG_MAX_ITER):
        if np.linalg.norm(residual) <= CG_RTOL * target:
            return x
        step_image = apply(direction)
        step = rz / float(direction @ step_image)
        x += step * direction
        residual -= step * step_image
        z = precondition * residual
        rz_next = float(residual @ z)
        direction = z + (rz_next / rz) * direction
        rz = rz_next
    raise RidgeError(f"conjugate gradients did not converge in {CG_MAX_ITER} iterations")


class InnerCVRidge:
    """Ridge with ``alpha`` chosen by cross-validation over the training rows' folds.

    Args:
        alphas: Candidate penalties.
        unpenalized: Names of dense columns left unpenalised (augmented ridge: the zero-shot
            score). Every other column is penalised.
    """

    def __init__(self, alphas: Sequence[float], unpenalized: Sequence[str] = ()) -> None:
        if not alphas or min(alphas) <= 0:
            raise RidgeError(f"alphas must be positive and non-empty, got {list(alphas)}")
        self.alphas = sorted(float(a) for a in alphas)
        self.unpenalized = tuple(unpenalized)
        self.alpha_: float | None = None
        self.inner_mse_: FloatArray | None = None
        self.fit_: LinearFit | None = None

    def fit(self, x: FeatureMatrix, y: FloatArray, groups: IntArray) -> "InnerCVRidge":
        free = [x.column(name) for name in self.unpenalized]
        inner = np.unique(groups)
        if inner.size < 2:
            raise RidgeError(f"inner CV needs at least 2 training folds, got {inner.tolist()}")
        squared = np.zeros(len(self.alphas))
        for group in inner:
            held_out = groups == group
            fits = ridge_path(x.take(~held_out), y[~held_out], self.alphas, free)
            validation = x.take(held_out)
            for k, candidate in enumerate(fits):
                squared[k] += float(np.sum((candidate.predict(validation) - y[held_out]) ** 2))
        self.inner_mse_ = squared / y.shape[0]
        # alphas ascend, so the last of the tied minima is the largest penalty.
        best = int(np.flatnonzero(squared <= squared.min())[-1])
        self.alpha_ = self.alphas[best]
        self.fit_ = ridge_path(x, y, [self.alpha_], free)[0]
        return self

    def predict(self, x: FeatureMatrix) -> FloatArray:
        if self.fit_ is None:
            raise RuntimeError("InnerCVRidge.predict called before fit")
        return self.fit_.predict(x)

    def selected(self) -> dict[str, float]:
        if self.alpha_ is None:
            raise RuntimeError("InnerCVRidge.selected called before fit")
        return {"alpha": self.alpha_}
