"""PCA of the embedding blocks, fitted on the rows it is given (ADR 0022)."""

import numpy as np
import pytest
from sklearn.decomposition import PCA  # pyright: ignore[reportMissingTypeStubs]
from tests.models.embedding_data import WIDTH, features

from mutascope.features.matrix import FeatureMatrix
from mutascope.models.pca import EmbeddingPCA, PCAError, principal_components


@pytest.mark.parametrize("rows", [200, 15], ids=["rows>columns", "rows<columns"])
def test_principal_components_match_scikit_learn(rows: int) -> None:
    rng = np.random.default_rng(1)
    block = rng.normal(size=(rows, WIDTH)) @ rng.normal(size=(WIDTH, WIDTH))
    k = 6
    mean, components = principal_components(block, k)
    reference = PCA(n_components=k, svd_solver="full").fit(block)  # pyright: ignore[reportUnknownMemberType]
    np.testing.assert_allclose(mean, reference.mean_, atol=1e-10)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    # Same subspace and order; signs are our own convention.
    overlap = np.abs(components @ np.asarray(reference.components_).T)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    np.testing.assert_allclose(overlap, np.eye(k), atol=1e-6)
    peaks = components[np.arange(k), np.abs(components).argmax(axis=1)]
    assert (peaks > 0).all()


def test_components_stop_at_the_rank_of_the_centred_rows() -> None:
    block = np.random.default_rng(2).normal(size=(5, WIDTH))
    _, components = principal_components(block, 32)
    assert components.shape == (4, WIDTH)  # 5 centred rows span at most 4 directions


def test_embedding_pca_replaces_each_block_and_keeps_the_rest() -> None:
    x, _ = features(100)
    pca = EmbeddingPCA(4).fit(x)
    out = pca.transform(x)
    assert out.names == (
        "esm2_650m:score",
        "blosum62",
        *(f"esm2_650m:embedding_pc{k}" for k in range(1, 5)),
        *(f"saprot_650m_af2:embedding_pc{k}" for k in range(1, 5)),
    )
    np.testing.assert_array_equal(out.dense[:, :2], x.dense[:, [0, -1]])
    # Components of training rows are centred on the training mean.
    np.testing.assert_allclose(out.dense[:, 2:].mean(axis=0), 0, atol=1e-10)


def test_embedding_pca_needs_named_embedding_columns() -> None:
    with pytest.raises(PCAError, match="named columns"):
        EmbeddingPCA(2).fit(FeatureMatrix.of(np.ones((4, 3))))
    with pytest.raises(PCAError, match="no <model>:embedding"):
        EmbeddingPCA(2).fit(FeatureMatrix(dense=np.ones((4, 1)), names=("blosum62",)))
