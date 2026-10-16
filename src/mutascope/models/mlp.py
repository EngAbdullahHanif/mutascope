"""A small MLP head on the store features, early-stopped inside the training folds (ADR 0023).

**Architecture.** ``LayerNorm -> 512 -> GELU -> Dropout -> 128 -> GELU -> 1`` on the features
of :mod:`mutascope.features.sets` (``seq_struct``), each embedding block first reduced to its
leading principal components (:class:`~mutascope.models.pca.EmbeddingPCA`) and every column
standardised. AdamW with a fixed learning rate, weight decay, batch size and epoch budget; none
of them is searched.

**Losses.** ``mse``: mean squared error on the standardised ``DMS_score``. ``rank``: the
pairwise logistic loss of RankNet (Burges et al., 2005), ``log(1 + exp(-(s_i - s_j)))`` over
every pair in a batch with ``y_i > y_j`` (ties carry no order and are left out). A model is fit
on one assay's rows (the CV loop calls ``fit`` per assay and fold), so every pair is two
variants of the same assay: scores of different assays are never ranked against each other.
The rank model's outputs have no scale, so its MSE is not meaningful; Spearman is.

**Early stopping, split like the outer scheme.** ``fit`` gets each training row's outer fold
(ADR 0019). One of those folds, picked with ``seed``, is the validation set and the others are
trained on, so under ``modulo`` and ``contiguous`` the validation variants sit at positions the
network never trained on, as the test fold's do, and under ``random`` they are a random subset.
After every epoch the validation loss (the training loss, on all validation rows) is computed;
training stops ``patience`` epochs after it last improved. The model is then refit on all
training rows for the best epoch count, as LightGBM is refit for its rounds (ADR 0022): every
rung's final model sees the same rows. The test fold takes no part in any of it.

**No leakage through preprocessing.** The PCA and the column means and standard deviations are
fitted on the rows each network trains on: the inner-training folds for the early-stopping run,
all training rows for the refit. Rows a network is evaluated on never shape them.

**Process rules.** torch is imported when a model is fit, never when this module is imported:
the CLI and the registry must load neither torch nor LightGBM (ADR 0022). Fitting and predicting
use one torch thread (parallelism is across assays, ``train cv --jobs``) and torch's random
state is forked and seeded per fit, so a fit depends only on its inputs and ``seed``, never on
the process it runs in. Nothing is written to disk: the best weights are kept in memory.
"""

from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import numpy as np

from mutascope.features.matrix import FeatureMatrix, FloatArray, FloatMatrix, IntArray
from mutascope.models.pca import EmbeddingPCA

if TYPE_CHECKING:
    import torch

LOSSES: Final = ("mse", "rank")


class MLPError(ValueError):
    """The model cannot be fit as asked (bad settings, too few folds, a site one-hot...)."""


@dataclass(frozen=True)
class Standardiser:
    """Column means and population standard deviations of the rows it was fitted on.

    A constant column keeps scale 1, so it maps to 0 rather than dividing by zero.
    """

    mean: FloatArray
    scale: FloatArray

    @classmethod
    def fit(cls, x: FloatMatrix) -> "Standardiser":
        scale = x.std(axis=0)
        scale[scale == 0] = 1.0
        return cls(mean=x.mean(axis=0), scale=scale)

    def transform(self, x: FloatMatrix) -> FloatMatrix:
        return (x - self.mean) / self.scale


@dataclass(frozen=True)
class Preprocessor:
    """Embedding PCA (if any), then standardisation; both fitted on the same rows."""

    pca: EmbeddingPCA | None
    standardiser: Standardiser

    @classmethod
    def fit(cls, x: FeatureMatrix, pca_components: int | None) -> "Preprocessor":
        if x.site is not None:
            raise MLPError("the MLP takes dense features only, not a site one-hot")
        pca = None if pca_components is None else EmbeddingPCA(pca_components).fit(x)
        dense = x.dense if pca is None else pca.transform(x).dense
        return cls(pca=pca, standardiser=Standardiser.fit(dense))

    def transform(self, x: FeatureMatrix) -> FloatMatrix:
        if x.site is not None:
            raise MLPError("the MLP takes dense features only, not a site one-hot")
        dense = x.dense if self.pca is None else self.pca.transform(x).dense
        return self.standardiser.transform(dense)


def mse_loss(score: "torch.Tensor", target: "torch.Tensor") -> "torch.Tensor | None":
    """Mean squared error."""
    return ((score - target) ** 2).mean()


def ranknet_loss(score: "torch.Tensor", target: "torch.Tensor") -> "torch.Tensor | None":
    """Mean of ``log(1 + exp(-(s_i - s_j)))`` over the pairs with ``y_i > y_j``.

    Each untied pair counts once, ordered so that ``i`` is the fitter variant. None when no pair
    is ordered (one row, or all targets equal): there is nothing to learn from such a batch.
    """
    import torch.nn.functional as functional

    fitter = target[:, None] > target[None, :]
    if not bool(fitter.any()):
        return None
    margin = score[:, None] - score[None, :]
    return functional.softplus(-margin[fitter]).mean()


_LOSS_FUNCTIONS: Final[
    dict[str, Callable[["torch.Tensor", "torch.Tensor"], "torch.Tensor | None"]]
] = {"mse": mse_loss, "rank": ranknet_loss}


@contextmanager
def one_thread() -> Generator[None]:
    """torch uses one intra-op thread inside the block; the previous count is restored."""
    import torch

    previous = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        yield
    finally:
        torch.set_num_threads(previous)


def network(width: int, hidden: Sequence[int], dropout: float) -> "torch.nn.Sequential":
    """``LayerNorm -> hidden[0] -> GELU -> Dropout -> hidden[1] -> GELU -> ... -> 1``.

    Dropout follows the first hidden layer only, as in the configured architecture.
    """
    from torch import nn

    layers: list[nn.Module] = [nn.LayerNorm(width)]
    previous = width
    for k, size in enumerate(hidden):
        layers += [nn.Linear(previous, size), nn.GELU()]
        if k == 0:
            layers.append(nn.Dropout(dropout))
        previous = size
    layers.append(nn.Linear(previous, 1))
    return nn.Sequential(*layers)


class EarlyStoppedMLP:
    """MLP regressor (or ranker) early-stopped on one held-out training fold, then refit.

    Args:
        loss: ``mse`` or ``rank`` (RankNet).
        hidden: Hidden layer widths.
        dropout: Dropout after the first hidden layer.
        learning_rate: AdamW learning rate, fixed.
        weight_decay: AdamW (decoupled) weight decay.
        batch_size: Rows per update; also the rows whose pairs one RankNet step sees.
        max_epochs: Upper bound on epochs of the early-stopping run.
        patience: Epochs without a better validation loss before stopping.
        pca_components: Principal components kept per embedding block; None keeps them raw.
        seed: Seeds the choice of validation fold, the initial weights, the batch order and
            dropout.
    """

    def __init__(
        self,
        *,
        loss: str,
        hidden: Sequence[int],
        dropout: float,
        learning_rate: float,
        weight_decay: float,
        batch_size: int,
        max_epochs: int,
        patience: int,
        pca_components: int | None,
        seed: int,
    ) -> None:
        if loss not in LOSSES:
            raise MLPError(f"loss must be one of {LOSSES}, got {loss!r}")
        if not hidden or min(hidden) < 1:
            raise MLPError(f"hidden widths must be positive and non-empty, got {list(hidden)}")
        if not 0 <= dropout < 1:
            raise MLPError(f"dropout must be in [0, 1), got {dropout}")
        if min(batch_size, max_epochs, patience) < 1 or learning_rate <= 0 or weight_decay < 0:
            raise MLPError("batch_size, max_epochs, patience and learning_rate must be positive")
        self.loss = loss
        self.hidden = tuple(int(h) for h in hidden)
        self.dropout = dropout
        self.learning_rate = learning_rate
        self.weight_decay = weight_decay
        self.batch_size = batch_size
        self.max_epochs = max_epochs
        self.patience = patience
        self.pca_components = pca_components
        self.seed = seed
        self.validation_fold_: int | None = None
        self.epochs_: int | None = None
        self.validation_loss_: float | None = None
        self.early_preprocessor_: Preprocessor | None = None
        """Fitted on the inner-training folds only: the early-stopping run's preprocessing."""
        self.preprocessor_: Preprocessor | None = None
        """Fitted on all training rows: the refit's, used by :meth:`predict`."""
        self.network_: torch.nn.Sequential | None = None

    def fit(self, x: FeatureMatrix, y: FloatArray, groups: IntArray) -> "EarlyStoppedMLP":
        inner = np.unique(groups)
        if inner.size < 2:
            raise MLPError(f"early stopping needs at least 2 training folds, got {inner.tolist()}")
        self.validation_fold_ = int(np.random.default_rng(self.seed).choice(inner))
        held_out = groups == self.validation_fold_
        with one_thread():
            early = Preprocessor.fit(x.take(~held_out), self.pca_components)
            self.early_preprocessor_ = early
            _, epochs, best = self._train(
                early.transform(x.take(~held_out)),
                y[~held_out],
                validation=(early.transform(x.take(held_out)), y[held_out]),
            )
            self.epochs_, self.validation_loss_ = epochs, best
            self.preprocessor_ = Preprocessor.fit(x, self.pca_components)
            self.network_, _, _ = self._train(self.preprocessor_.transform(x), y, epochs=epochs)
        return self

    def _train(
        self,
        x: FloatMatrix,
        y: FloatArray,
        *,
        validation: tuple[FloatMatrix, FloatArray] | None = None,
        epochs: int | None = None,
    ) -> tuple["torch.nn.Sequential", int, float]:
        """Train one network; early-stopped on ``validation``, else for ``epochs`` epochs.

        Returns the network (with its best weights when early-stopped), the epoch count and
        the best validation loss (NaN without validation).
        """
        import torch

        loss_of = _LOSS_FUNCTIONS[self.loss]
        inputs = torch.tensor(x.astype(np.float32))
        targets = torch.tensor(y.astype(np.float32))
        n = inputs.shape[0]
        # Forked: dropout draws from torch's global generator, seeded here without touching
        # the caller's.
        with torch.random.fork_rng(devices=[]):  # pyright: ignore[reportUnknownMemberType]
            torch.manual_seed(self.seed)  # pyright: ignore[reportUnknownMemberType]
            net = network(inputs.shape[1], self.hidden, self.dropout)
            optimiser = torch.optim.AdamW(
                net.parameters(), lr=self.learning_rate, weight_decay=self.weight_decay
            )
            order = torch.Generator().manual_seed(self.seed)
            budget = self.max_epochs if epochs is None else epochs
            best_loss, best_epoch = float("inf"), 0
            best_state: dict[str, torch.Tensor] = {}
            for epoch in range(1, budget + 1):
                net.train()
                permutation = torch.randperm(n, generator=order)
                for start in range(0, n, self.batch_size):
                    batch = permutation[start : start + self.batch_size]
                    value = loss_of(net(inputs[batch]).squeeze(-1), targets[batch])
                    if value is None:  # a RankNet batch with no ordered pair
                        continue
                    optimiser.zero_grad()
                    value.backward()  # pyright: ignore[reportUnknownMemberType]
                    optimiser.step()  # pyright: ignore[reportUnknownMemberType]
                if validation is None:
                    continue
                current = self._loss(net, *validation)
                if current < best_loss:
                    best_loss, best_epoch = current, epoch
                    best_state = {k: v.detach().clone() for k, v in net.state_dict().items()}
                elif epoch - best_epoch >= self.patience:
                    break
            if validation is None:
                return net, budget, float("nan")
            if not best_state:
                raise MLPError("the validation loss was never finite")
            net.load_state_dict(best_state)
            return net, best_epoch, best_loss

    def _loss(self, net: "torch.nn.Sequential", x: FloatMatrix, y: FloatArray) -> float:
        """The training loss on all of ``x``, in evaluation mode (no dropout)."""
        import torch

        net.eval()
        with torch.no_grad():
            score = net(torch.tensor(x.astype(np.float32))).squeeze(-1)
            value = _LOSS_FUNCTIONS[self.loss](score, torch.tensor(y.astype(np.float32)))
        if value is None:
            raise MLPError("the validation fold's targets are all equal: nothing to rank")
        return float(value.item())

    def predict(self, x: FeatureMatrix) -> FloatArray:
        if self.network_ is None or self.preprocessor_ is None:
            raise RuntimeError("EarlyStoppedMLP.predict called before fit")
        import torch

        net = self.network_
        net.eval()
        with one_thread(), torch.no_grad():
            features = torch.tensor(self.preprocessor_.transform(x).astype(np.float32))
            return net(features).squeeze(-1).numpy().astype(np.float64)

    def selected(self) -> dict[str, float]:
        """The epoch count early stopping chose, and the validation fold it was chosen on."""
        if self.epochs_ is None or self.validation_fold_ is None:
            raise RuntimeError("EarlyStoppedMLP.selected called before fit")
        return {"epochs": float(self.epochs_), "validation_fold": float(self.validation_fold_)}
