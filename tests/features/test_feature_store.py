"""The feature store: every substitution, scores from the right landscape, no labels read.

The caches are filled by hand with known arrays, so every stored number can be checked
against the entry it must come from. The toy model has a benchmark region (residues 3-5 of a
7-residue target, like an MSA-region assay) and a full-target landscape beside it.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from mutascope.alphabet import AA_ORDER
from mutascope.eval.landscapes import LandscapeTarget
from mutascope.eval.region import ScoringRegion
from mutascope.features.store import (
    FeatureStoreError,
    ModelSource,
    assay_features,
    blosum62,
    build_store,
    check_measured,
    measured_keys,
    substitution_grid,
)
from mutascope.scoring.cache import CacheKey, LogProbCache, embedding_method

TARGET = "MKVARLE"
WIDTH = 3
LAYERS = 4

Filled = dict[str, tuple[np.ndarray, np.ndarray]]
Toy = tuple[ModelSource, LogProbCache, Filled]


def _target(start: int, end: int, *, benchmark: bool) -> LandscapeTarget:
    region = ScoringRegion("A", start, end, len(TARGET))
    return LandscapeTarget("A", region, TARGET[start:end], None, benchmark=benchmark)


def _key(target: LandscapeTarget, method: str) -> CacheKey:
    return CacheKey("hub/toy", "r" * 40, target.sequence, None, method, target.region.label)


def _fill(
    log_probs: LogProbCache, embeddings: LogProbCache, targets: list[LandscapeTarget], seed: int
) -> Filled:
    """Random log-probabilities and embeddings for each target, by region label."""
    rng = np.random.default_rng(seed)
    filled: Filled = {}
    for target in targets:
        lp = rng.normal(size=(len(target.sequence), 20)).astype(np.float32)
        emb = rng.normal(size=(len(target.sequence), WIDTH)).astype(np.float32)
        log_probs.put(_key(target, "masked"), lp)
        embeddings.put(_key(target, embedding_method(LAYERS)), emb)
        filled[target.region.label] = (lp, emb)
    return filled


@pytest.fixture
def toy(tmp_path: Path) -> Toy:
    targets = [_target(2, 5, benchmark=True), _target(0, len(TARGET), benchmark=False)]
    log_probs = LogProbCache(tmp_path / "logprobs")
    embeddings = LogProbCache(tmp_path / "embeddings", width=WIDTH)
    filled = _fill(log_probs, embeddings, targets, seed=0)
    source = ModelSource("toy", "hub/toy", "r" * 40, {"A": targets}, embeddings, LAYERS)
    return source, log_probs, filled


def _structure() -> pd.DataFrame:
    """Residues 1-5 modelled, like a structure of part of the target."""
    return pd.DataFrame(
        {
            "position": [1, 2, 3, 4, 5],
            "residue": list(TARGET[1:6]),
            "plddt": [90.0, 80.0, 70.0, 60.0, 50.0],
            "rel_sasa": [0.1, 0.2, 0.3, 0.4, 0.5],
            "contact_count": [1, 2, 3, 4, 5],
        }
    )


def test_every_substitution_once_in_position_then_aa_order() -> None:
    position, column = substitution_grid("MK")
    assert len(position) == 38
    assert [AA_ORDER[c] for c in column[:19]] == [a for a in AA_ORDER if a != "M"]
    assert (position[:19] == 0).all() and (position[19:] == 1).all()


def test_blosum62_is_the_standard_matrix() -> None:
    matrix, i = blosum62(), AA_ORDER.index
    assert matrix[i("W"), i("W")] == 11 and matrix[i("A"), i("A")] == 4
    assert matrix[i("W"), i("F")] == 1 and matrix[i("D"), i("E")] == 2
    assert matrix[i("C"), i("E")] == -4
    np.testing.assert_array_equal(matrix, matrix.T)


def test_inside_the_benchmark_region_the_benchmark_landscape_is_read(toy: Toy) -> None:
    """Residues 2-4 read the benchmark entry (row p - 2), the others the full-target entry.
    A shift by one row or a swapped entry changes every number."""
    source, log_probs, filled = toy
    features = assay_features("A", TARGET, [source], log_probs, None)
    bench_lp, bench_emb = filled["msa:3-5"]
    full_lp, full_emb = filled["full"]
    expected_lp = full_lp.copy()
    expected_lp[2:5] = bench_lp
    expected_emb = full_emb.copy()
    expected_emb[2:5] = bench_emb
    np.testing.assert_array_equal(features.arrays["toy.log_probs"], expected_lp)
    np.testing.assert_array_equal(features.arrays["toy.embedding"], expected_emb)
    regions = features.positions["region_toy"].tolist()
    assert regions == ["full", "full", "msa:3-5", "msa:3-5", "msa:3-5", "full", "full"]
    assert features.residues_from_full_target == 4

    variants = features.variants
    assert len(variants) == 19 * len(TARGET)
    p = variants["position"].to_numpy(np.int64)
    wt = np.array([AA_ORDER.index(a) for a in variants["wt_aa"]])
    mt = np.array([AA_ORDER.index(a) for a in variants["mt_aa"]])
    assert "".join(TARGET[i] for i in p) == "".join(variants["wt_aa"])
    np.testing.assert_allclose(
        variants["score_toy"], expected_lp[p, mt] - expected_lp[p, wt], atol=1e-6
    )
    np.testing.assert_array_equal(variants["blosum62"], blosum62()[wt, mt])
    np.testing.assert_allclose(variants["rel_position"], p / (len(TARGET) - 1), rtol=1e-6)


def test_structure_features_are_nan_outside_the_structure(toy: Toy) -> None:
    source, log_probs, _ = toy
    positions = assay_features("A", TARGET, [source], log_probs, _structure()).positions
    assert positions["plddt"].isna().tolist() == [True, False, False, False, False, False, True]
    assert positions.loc[3, "contact_count"] == 3
    assert positions["plddt"].dtype == np.float32


def test_a_structure_that_disagrees_with_the_target_fails(toy: Toy) -> None:
    source, log_probs, _ = toy
    wrong = _structure().assign(residue=list("AAAAA"))
    with pytest.raises(FeatureStoreError, match="structure residues differ"):
        assay_features("A", TARGET, [source], log_probs, wrong)


def test_a_model_without_input_for_the_assay_gets_empty_columns(toy: Toy, tmp_path: Path) -> None:
    source, log_probs, _ = toy
    absent = ModelSource("other", "hub/other", "s" * 40, {}, LogProbCache(tmp_path / "x"), 4)
    features = assay_features("A", TARGET, [source, absent], log_probs, None)
    assert features.variants["score_other"].isna().all()
    assert features.positions["region_other"].isna().all()
    assert set(features.arrays) == {"toy.log_probs", "toy.embedding"}


def test_a_missing_or_incomplete_entry_fails_and_names_the_command(tmp_path: Path) -> None:
    targets = [_target(0, len(TARGET), benchmark=True)]
    log_probs = LogProbCache(tmp_path / "logprobs")
    embeddings = LogProbCache(tmp_path / "embeddings", width=WIDTH)
    source = ModelSource("toy", "hub/toy", "r" * 40, {"A": targets}, embeddings, LAYERS)
    with pytest.raises(FeatureStoreError, match="score landscapes"):
        assay_features("A", TARGET, [source], log_probs, None)
    partial = np.zeros((len(TARGET), 20), dtype=np.float32)
    partial[3] = np.nan  # a residue the benchmark never scored
    log_probs.put(_key(targets[0], "masked"), partial)
    with pytest.raises(FeatureStoreError, match="1 residues not computed"):
        assay_features("A", TARGET, [source], log_probs, None)
    log_probs.put(_key(targets[0], "masked"), np.zeros((len(TARGET), 20), dtype=np.float32))
    with pytest.raises(FeatureStoreError, match="features embed"):
        assay_features("A", TARGET, [source], log_probs, None)


def test_a_partial_benchmark_region_without_a_full_target_landscape_fails(toy: Toy) -> None:
    source, log_probs, _ = toy
    only_benchmark = ModelSource(
        "toy", "hub/toy", "r" * 40, {"A": source.targets["A"][:1]}, source.embedding_cache, LAYERS
    )
    with pytest.raises(FeatureStoreError, match="no full-target landscape"):
        assay_features("A", TARGET, [only_benchmark], log_probs, None)


def test_measured_variants_must_be_in_the_store_with_their_wild_type(toy: Toy) -> None:
    source, log_probs, _ = toy
    variants = assay_features("A", TARGET, [source], log_probs, None).variants
    good = pd.DataFrame({"position": [0, 3], "wt_aa": ["M", "A"], "mt_aa": ["A", "C"]})
    check_measured("A", variants, good)
    wrong_wt = good.assign(wt_aa=["M", "G"])
    with pytest.raises(FeatureStoreError, match="1 measured variants"):
        check_measured("A", variants, wrong_wt)
    outside = pd.DataFrame({"position": [7], "wt_aa": ["M"], "mt_aa": ["A"]})
    with pytest.raises(FeatureStoreError, match="not in the store"):
        check_measured("A", variants, outside)


def _write_variants(root: Path, labels: list[float]) -> Path:
    part = root / "assay_id=A"
    part.mkdir(parents=True)
    pd.DataFrame(
        {
            "mutant": ["M1A", "A4C"],
            "position": [0, 3],
            "wt_aa": ["M", "A"],
            "mt_aa": ["A", "C"],
            "DMS_score": labels,
            "DMS_score_bin": [0, 1],
        }
    ).to_parquet(part / "data.parquet")
    return root


def test_the_store_reads_keys_only_and_does_not_depend_on_labels(toy: Toy, tmp_path: Path) -> None:
    """Two variant tables that differ only in their labels give byte-identical stores."""
    source, log_probs, _ = toy
    reference = pd.DataFrame({"DMS_id": ["A"], "target_seq": [TARGET]})
    stores: list[dict[str, bytes]] = []
    for i, labels in enumerate(([1.0, -1.0], [np.nan, 1e9])):
        keys = measured_keys(_write_variants(tmp_path / f"variants{i}", labels))
        assert list(keys.columns) == ["assay_id", "position", "wt_aa", "mt_aa"]
        out = tmp_path / f"store{i}"
        report = build_store(reference, [source], log_probs, {"A": _structure()}, keys, out)
        assert report["variants"].tolist() == [19 * len(TARGET)]
        assert report["measured"].tolist() == [2]
        assert report["residues_with_structure"].tolist() == [5]
        files = sorted(p for p in out.rglob("*") if p.is_file())
        stores.append({str(p.relative_to(out)): p.read_bytes() for p in files})
        assert not (tmp_path / f"store{i}.tmp").exists()
    assert stores[0] == stores[1]
    assert set(stores[0]) == {
        "variants/assay_id=A/data.parquet",
        "positions/assay_id=A/data.parquet",
        "arrays/assay_id=A/toy.log_probs.npy",
        "arrays/assay_id=A/toy.embedding.npy",
    }


def test_a_failed_build_keeps_the_previous_store(toy: Toy, tmp_path: Path) -> None:
    source, log_probs, _ = toy
    reference = pd.DataFrame({"DMS_id": ["A"], "target_seq": [TARGET]})
    keys = measured_keys(_write_variants(tmp_path / "variants", [0.0, 1.0]))
    out = tmp_path / "store"
    build_store(reference, [source], log_probs, {}, keys, out)
    before = (out / "variants" / "assay_id=A" / "data.parquet").read_bytes()
    bad = keys.assign(wt_aa="W")
    with pytest.raises(FeatureStoreError):
        build_store(reference, [source], log_probs, {}, bad, out)
    assert (out / "variants" / "assay_id=A" / "data.parquet").read_bytes() == before
