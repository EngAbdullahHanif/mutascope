"""LightGBM with its hyperparameters tuned by Optuna inside the training folds (ADR 0022).

**Search.** Each fit runs a fixed budget of Optuna trials (``n_trials``, TPE sampler seeded
with ``seed``). A trial is scored the way ridge scores a penalty (ADR 0021): every training
row's outer fold is held out in turn, a booster is trained on the other training folds, and
the squared error is summed over all held-out rows. The inner folds are therefore split like
the outer scheme (contiguous blocks under ``contiguous``) and the test fold never takes part.
With 10 trials and Optuna's default of 10 random start-up trials, the search is a seeded
random search; the budget, not the sampler, is what is fixed.

**Boosting rounds.** Each inner booster trains for up to ``max_rounds`` rounds and stops
``patience`` rounds after its held-out error last improved. The pooled held-out error is
summed round by round over the inner folds, up to the shortest of their curves (every curve
runs at least ``patience`` rounds past its own best), and the trial's number of rounds is the
round where that sum is smallest. The final model is refit on all training rows with the best
trial's parameters for that many rounds.

**PCA.** With ``pca_components`` set, each embedding block is replaced by its leading principal
components (:class:`~mutascope.models.pca.EmbeddingPCA`), fitted on the rows the booster is
trained on: anew for each inner split and once more for the refit. Rows a booster is evaluated
on never shape the components it sees.

**Import.** LightGBM is imported when a model is fit, never when this module is imported. On
macOS its OpenMP runtime and torch's cannot share a process (either order segfaults), so the
CLI, the registry and test collection must not load it (ADR 0022).

**Determinism.** ``num_threads`` is 1 (parallelism is across assays, ``train cv --jobs``) and
``deterministic`` is on, so a fit depends only on its inputs and ``seed``. The binned dataset of
each split is built once and reused by every trial; only booster parameters are searched.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
import optuna

from mutascope.features.matrix import FeatureMatrix, FloatArray, FloatMatrix, IntArray
from mutascope.models.pca import EmbeddingPCA

if TYPE_CHECKING:
    import lightgbm as lgb

optuna.logging.set_verbosity(optuna.logging.WARNING)

DATASET_PARAMS: Mapping[str, Any] = {"feature_pre_filter": False, "verbose": -1}
"""Binning parameters, fixed: the dataset is binned once per split and shared by all trials.
``feature_pre_filter`` off lets trials vary ``min_data_in_leaf`` on one binned dataset."""


class LightGBMError(ValueError):
    """The model cannot be fit as asked (too few folds, a bad search space, a site one-hot)."""


@dataclass(frozen=True)
class Distribution:
    """One searched hyperparameter: a float or an integer range, optionally log-uniform."""

    low: float
    high: float
    log: bool = False
    integer: bool = False

    def suggest(self, trial: optuna.Trial, name: str) -> float:
        if self.integer:
            return float(trial.suggest_int(name, int(self.low), int(self.high), log=self.log))
        return trial.suggest_float(name, self.low, self.high, log=self.log)


def search_space(config: Mapping[str, Mapping[str, Any]]) -> dict[str, Distribution]:
    """``{name: {low, high, log?, int?}}`` from ``train.params.lgbm.search``."""
    space = {
        str(name): Distribution(
            low=float(spec["low"]),
            high=float(spec["high"]),
            log=bool(spec.get("log", False)),
            integer=bool(spec.get("int", False)),
        )
        for name, spec in config.items()
    }
    for name, d in space.items():
        if not d.low < d.high or (d.log and d.low <= 0):
            raise LightGBMError(f"search.{name}: need 0 < low < high for log, low < high otherwise")
    return space


def _train(
    params: Mapping[str, Any],
    data: "lgb.Dataset",
    rounds: int,
    *,
    valid: "lgb.Dataset | None" = None,
    history: dict[str, dict[str, list[float]]] | None = None,
    patience: int | None = None,
) -> "lgb.Booster":
    """``lgb.train``, typed: with ``valid``, records its l2 curve and stops early."""
    import lightgbm as lgb

    if valid is None:
        return lgb.train(dict(params), data, num_boost_round=rounds)  # pyright: ignore[reportUnknownMemberType]
    callbacks: list[Callable[..., None]] = [
        lgb.record_evaluation(history if history is not None else {}),  # pyright: ignore[reportUnknownMemberType, reportArgumentType]
        lgb.early_stopping(patience or rounds, first_metric_only=True, verbose=False),
    ]
    return lgb.train(  # pyright: ignore[reportUnknownMemberType]
        dict(params),
        data,
        num_boost_round=rounds,
        valid_sets=[valid],
        valid_names=["valid"],
        callbacks=callbacks,  # pyright: ignore[reportUnknownArgumentType]
    )


@dataclass(frozen=True)
class _Split:
    """One inner split: binned training and held-out datasets, and the held-out targets."""

    train: "lgb.Dataset"
    valid: "lgb.Dataset"
    n_valid: int


class InnerCVLightGBM:
    """Gradient-boosted trees, tuned by a seeded Optuna search over the training rows' folds.

    Args:
        search: Searched booster parameters by LightGBM name.
        fixed: Booster parameters that are not searched (objective, bagging frequency...).
        n_trials: Optuna trials per fit.
        max_rounds: Upper bound on boosting rounds.
        patience: Early-stopping patience of each inner booster, in rounds.
        seed: Seeds the sampler and every booster.
        pca_components: Principal components kept per embedding block; None keeps the raw
            embedding columns.
    """

    def __init__(
        self,
        search: Mapping[str, Distribution],
        fixed: Mapping[str, Any],
        *,
        n_trials: int,
        max_rounds: int,
        patience: int,
        seed: int,
        pca_components: int | None = None,
    ) -> None:
        if n_trials < 1 or max_rounds < 1 or patience < 1:
            raise LightGBMError("n_trials, max_rounds and patience must be at least 1")
        self.search = dict(search)
        self.fixed = dict(fixed)
        self.n_trials = n_trials
        self.max_rounds = max_rounds
        self.patience = patience
        self.seed = seed
        self.pca_components = pca_components
        self.params_: dict[str, Any] | None = None
        self.rounds_: int | None = None
        self.inner_mse_: float | None = None
        self.pca_: EmbeddingPCA | None = None
        self.booster_: lgb.Booster | None = None
        self.names_: tuple[str, ...] = ()

    def _params(self, searched: Mapping[str, float]) -> dict[str, Any]:
        params = {**self.fixed, **DATASET_PARAMS, "seed": self.seed}
        for name, value in searched.items():
            params[name] = int(value) if self.search[name].integer else value
        return params

    def _prepare(
        self, x_fit: FeatureMatrix, others: list[FeatureMatrix]
    ) -> tuple[EmbeddingPCA | None, FloatMatrix, list[FloatMatrix]]:
        """Fit the PCA (if any) on ``x_fit`` only, then transform it and ``others``."""
        if x_fit.site is not None:
            raise LightGBMError("LightGBM takes dense features only, not a site one-hot")
        if self.pca_components is None:
            return None, x_fit.dense, [o.dense for o in others]
        pca = EmbeddingPCA(self.pca_components).fit(x_fit)
        return pca, pca.transform(x_fit).dense, [pca.transform(o).dense for o in others]

    def fit(self, x: FeatureMatrix, y: FloatArray, groups: IntArray) -> "InnerCVLightGBM":
        import lightgbm as lgb  # see the module docstring: never at import time

        inner = np.unique(groups)
        if inner.size < 2:
            raise LightGBMError(f"inner CV needs at least 2 training folds, got {inner.tolist()}")
        splits: list[_Split] = []
        for group in inner:
            held_out = groups == group
            _, fit_rows, (valid_rows,) = self._prepare(x.take(~held_out), [x.take(held_out)])
            # Binned now, so the float64 copies are freed before the next split is built
            # (free_raw_data): four splits' raw rows at once is most of a worker's memory.
            train = lgb.Dataset(fit_rows, y[~held_out], params=dict(DATASET_PARAMS)).construct()
            valid = lgb.Dataset(
                valid_rows, y[held_out], reference=train, params=dict(DATASET_PARAMS)
            ).construct()
            splits.append(_Split(train, valid, int(held_out.sum())))

        def objective(trial: optuna.Trial) -> float:
            searched = {name: d.suggest(trial, name) for name, d in self.search.items()}
            curve, rounds = self._inner_curve(self._params(searched), splits)
            trial.set_user_attr("rounds", rounds)
            return curve

        study = optuna.create_study(
            direction="minimize", sampler=optuna.samplers.TPESampler(seed=self.seed)
        )
        study.optimize(objective, n_trials=self.n_trials)
        best = study.best_trial
        self.params_ = self._params(best.params)
        self.rounds_ = int(best.user_attrs["rounds"])
        self.inner_mse_ = float(best.value) if best.value is not None else float("nan")
        self.pca_, fit_rows, _ = self._prepare(x, [])
        self.names_ = self.pca_.names_ if self.pca_ is not None else x.names
        self.booster_ = _train(
            self.params_, lgb.Dataset(fit_rows, y, params=dict(DATASET_PARAMS)), self.rounds_
        )
        return self

    def _inner_curve(self, params: dict[str, Any], splits: list[_Split]) -> tuple[float, int]:
        """Pooled held-out MSE at the best round over the inner splits, and that round."""
        curves: list[FloatArray] = []
        for split in splits:
            history: dict[str, dict[str, list[float]]] = {}
            _train(
                params,
                split.train,
                self.max_rounds,
                valid=split.valid,
                history=history,
                patience=self.patience,
            )
            # l2 is the held-out mean squared error after each round; x n_valid = sum of squares.
            curves.append(np.asarray(history["valid"]["l2"], dtype=np.float64) * split.n_valid)
        length = min(c.size for c in curves)
        pooled = np.sum([c[:length] for c in curves], axis=0) / sum(s.n_valid for s in splits)
        best = int(np.argmin(pooled))
        return float(pooled[best]), best + 1

    def _features(self, x: FeatureMatrix) -> FloatMatrix:
        if x.site is not None:
            raise LightGBMError("LightGBM takes dense features only, not a site one-hot")
        return x.dense if self.pca_ is None else self.pca_.transform(x).dense

    def predict(self, x: FeatureMatrix) -> FloatArray:
        if self.booster_ is None:
            raise RuntimeError("InnerCVLightGBM.predict called before fit")
        return np.asarray(self.booster_.predict(self._features(x)), dtype=np.float64)

    def contributions(self, x: FeatureMatrix) -> tuple[FloatMatrix, tuple[str, ...]]:
        """TreeSHAP values of every row: ``(n, columns)``, plus the column names.

        LightGBM computes them exactly (``pred_contrib``); each row's values plus the expected
        value (dropped here) sum to its prediction. With PCA the columns are the components.
        """
        if self.booster_ is None:
            raise RuntimeError("InnerCVLightGBM.contributions called before fit")
        values = np.asarray(
            self.booster_.predict(self._features(x), pred_contrib=True), dtype=np.float64
        )
        return values[:, :-1], self.names_

    def selected(self) -> dict[str, float]:
        if self.params_ is None or self.rounds_ is None:
            raise RuntimeError("InnerCVLightGBM.selected called before fit")
        chosen = {name: float(self.params_[name]) for name in self.search}
        return chosen | {"num_boost_round": float(self.rounds_)}
