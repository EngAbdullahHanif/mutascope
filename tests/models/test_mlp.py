"""The MLP head: losses, early stopping inside the training folds, no leakage (ADR 0023)."""

from typing import Any

import numpy as np
import pytest
import torch
from tests.models.embedding_data import features
from tests.synthetic_assays import synthetic_assay

from mutascope.eval.metrics import spearman
from mutascope.eval.supervised import below_minimum
from mutascope.features.matrix import FeatureMatrix, SiteOneHot
from mutascope.models.cv import cross_validate
from mutascope.models.mlp import EarlyStoppedMLP, MLPError, Preprocessor, ranknet_loss
from mutascope.models.registry import model_factory

SMALL: dict[str, Any] = {
    "hidden": [32, 8],
    "dropout": 0.2,
    "learning_rate": 3e-3,
    "weight_decay": 1e-2,
    "batch_size": 32,
    "max_epochs": 60,
    "patience": 8,
    "pca_components": 4,
    "seed": 0,
}


def mlp(loss: str = "mse", **changes: Any) -> EarlyStoppedMLP:
    model = model_factory(f"mlp_{loss}", SMALL | {"loss": loss} | changes)()
    assert isinstance(model, EarlyStoppedMLP)
    return model


def folds_of(n: int, scheme: str = "contiguous") -> np.ndarray:
    if scheme == "contiguous":
        return np.repeat(np.arange(5), -(-n // 5))[:n].astype(np.int64)
    return (np.arange(n) % 5).astype(np.int64)


# --- RankNet loss ------------------------------------------------------------------------------


def test_ranknet_loss_is_the_mean_over_ordered_pairs_ties_excluded() -> None:
    score = torch.tensor([0.3, -1.0, 2.0, 0.5])
    target = torch.tensor([1.0, 0.0, 1.0, 3.0])  # rows 0 and 2 tie
    expected = [
        np.log1p(np.exp(-(float(score[i]) - float(score[j]))))
        for i in range(4)
        for j in range(4)
        if float(target[i]) > float(target[j])
    ]
    loss = ranknet_loss(score, target)
    assert loss is not None
    assert len(expected) == 5  # 6 pairs, one tied
    assert float(loss) == pytest.approx(float(np.mean(expected)), rel=1e-6)


def test_ranknet_loss_has_nothing_to_learn_without_an_ordered_pair() -> None:
    assert ranknet_loss(torch.tensor([0.1]), torch.tensor([2.0])) is None
    assert ranknet_loss(torch.tensor([0.1, 0.4]), torch.tensor([2.0, 2.0])) is None


def test_ranknet_loss_falls_when_scores_follow_the_targets() -> None:
    target = torch.tensor([0.0, 1.0, 2.0, 3.0])
    aligned, reversed_ = ranknet_loss(target, target), ranknet_loss(-target, target)
    assert aligned is not None and reversed_ is not None
    assert float(aligned) < float(reversed_)


# --- No leakage --------------------------------------------------------------------------------


@pytest.mark.parametrize("loss", ["mse", "rank"])
@pytest.mark.parametrize("scheme", ["random", "modulo", "contiguous"])
def test_test_fold_labels_never_reach_its_predictions(loss: str, scheme: str) -> None:
    """Early stopping, PCA, scaling and weights all come from the other folds (ADR 0021)."""
    assay = synthetic_assay("A_TEST", seed=5)
    n = len(assay)
    x, _ = features(n, seed=3)
    y = assay["DMS_score"].to_numpy(np.float64)
    folds = assay[f"fold_{scheme}"].to_numpy(np.int64)
    test = folds == 1
    small = {"max_epochs": 15, "patience": 4}
    baseline = cross_validate(x, y, folds, lambda: mlp(loss, **small))
    for change in ("permute", "scale"):
        y_changed = y.copy()
        if change == "permute":
            y_changed[test] = np.random.default_rng(0).permutation(y[test])
        else:
            y_changed[test] = 7 * y[test] - 3
        after = cross_validate(x, y_changed, folds, lambda: mlp(loss, **small))
        np.testing.assert_array_equal(after.prediction[test], baseline.prediction[test])


@pytest.mark.parametrize("scheme", ["random", "modulo", "contiguous"])
def test_early_stopping_holds_out_one_training_fold_split_like_the_scheme(scheme: str) -> None:
    """The validation rows are one whole training fold: unseen positions under modulo and
    contiguous, as for the test fold; and the early run's preprocessing never saw them."""
    assay = synthetic_assay("A_TEST", seed=2)
    x, _ = features(len(assay), seed=1)
    y = assay["DMS_score"].to_numpy(np.float64)
    folds = assay[f"fold_{scheme}"].to_numpy(np.int64)
    train = folds != 0  # outer test fold 0
    model = mlp(max_epochs=5, patience=2).fit(x.take(train), y[train], folds[train])
    chosen = model.validation_fold_
    assert chosen in {1, 2, 3, 4}
    positions = assay["position"].to_numpy()[train]
    held_out = folds[train] == chosen
    shared = set(positions[held_out]) & set(positions[~held_out])
    assert (len(shared) == 0) == (scheme != "random")

    early, refit = model.early_preprocessor_, model.preprocessor_
    assert early is not None and refit is not None
    inner = x.take(train).take(~held_out)
    expected = Preprocessor.fit(inner, SMALL["pca_components"])
    np.testing.assert_array_equal(early.standardiser.mean, expected.standardiser.mean)
    np.testing.assert_array_equal(early.standardiser.scale, expected.standardiser.scale)
    everything = Preprocessor.fit(x.take(train), SMALL["pca_components"])
    np.testing.assert_array_equal(refit.standardiser.mean, everything.standardiser.mean)


# --- Learning, determinism, settings -----------------------------------------------------------


@pytest.mark.parametrize("loss", ["mse", "rank"])
def test_learns_a_nonlinear_signal_under_contiguous_folds(loss: str) -> None:
    x, y = features(400)
    oof = cross_validate(x, y, folds_of(400), lambda: mlp(loss))
    assert spearman(oof.target, oof.prediction) > 0.6


def test_fit_is_deterministic_whatever_the_global_torch_state() -> None:
    x, y = features(200)
    groups = folds_of(200)
    torch.manual_seed(123)  # pyright: ignore[reportUnknownMemberType]
    first = mlp("rank").fit(x, y, groups)
    torch.manual_seed(456)  # pyright: ignore[reportUnknownMemberType]
    torch.rand(10)
    threads = torch.get_num_threads()
    second = mlp("rank").fit(x, y, groups)
    assert torch.get_num_threads() == threads  # one thread inside fit only
    np.testing.assert_array_equal(first.predict(x), second.predict(x))
    other_seed = mlp("rank", seed=1).fit(x, y, groups)
    assert not np.array_equal(first.predict(x), other_seed.predict(x))


def test_reports_the_epochs_early_stopping_chose() -> None:
    x, y = features(200)
    model = mlp().fit(x, y, folds_of(200))
    chosen = model.selected()
    assert set(chosen) == {"epochs", "validation_fold"}
    assert 1 <= chosen["epochs"] <= SMALL["max_epochs"]
    assert model.validation_loss_ is not None and np.isfinite(model.validation_loss_)


def test_the_configured_models_build_from_conf() -> None:
    from pathlib import Path

    from omegaconf import OmegaConf

    from mutascope.utils.config import as_str_dict, load_config

    cfg = load_config(Path(__file__).parents[2] / "conf", [])
    for name, loss in (("mlp_mse", "mse"), ("mlp_rank", "rank")):
        params = as_str_dict(OmegaConf.to_container(cfg.train.params[name], resolve=True), name)
        model = model_factory(name, params)()
        assert isinstance(model, EarlyStoppedMLP)
        assert (model.loss, model.hidden, model.dropout) == (loss, (512, 128), 0.2)
        assert model.pca_components == 32
    assert dict(cfg.train.min_variants) == {"mlp_mse": 300, "mlp_rank": 300}


def test_the_network_follows_the_configured_architecture() -> None:
    from torch.nn import Linear

    from mutascope.models.mlp import network

    layers = [type(m).__name__ for m in network(10, [512, 128], 0.2)]
    assert layers == ["LayerNorm", "Linear", "GELU", "Dropout", "Linear", "GELU", "Linear"]
    widths = [m.out_features for m in network(10, [512, 128], 0.2) if isinstance(m, Linear)]
    assert widths == [512, 128, 1]


@pytest.mark.parametrize(
    "change",
    [{"loss": "huber"}, {"hidden": []}, {"dropout": 1.0}, {"batch_size": 0}, {"patience": 0}],
)
def test_bad_settings_fail(change: dict[str, Any]) -> None:
    with pytest.raises(MLPError):
        model_factory("mlp_mse", SMALL | {"loss": "mse"} | change)()


def test_needs_two_training_folds_and_dense_features() -> None:
    x, y = features(50)
    with pytest.raises(MLPError, match="2 training folds"):
        mlp().fit(x, y, np.zeros(50, dtype=np.int64))
    site = SiteOneHot.from_keys(np.zeros(50, np.int64), ["A"] * 50, ["C"] * 50, length=1)
    with pytest.raises(MLPError, match="dense"):
        mlp().fit(FeatureMatrix(dense=x.dense, names=x.names, site=site), y, folds_of(50))


def test_below_minimum_is_strict_and_sorted() -> None:
    sizes = {"B": 299, "A": 300, "C": 33}
    assert below_minimum(sizes, 300) == ["B", "C"]
    assert below_minimum(sizes, 0) == []
