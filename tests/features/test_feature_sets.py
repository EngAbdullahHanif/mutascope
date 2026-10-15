"""Feature sets: block layout, key lookup, and refusing assays a set does not cover."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from omegaconf import OmegaConf

from mutascope.alphabet import AA_ORDER
from mutascope.eval.landscapes import LandscapeTarget
from mutascope.eval.region import ScoringRegion
from mutascope.features.sets import (
    FeatureSet,
    FeatureSetError,
    assays_of,
    assemble,
    feature_sets,
    width,
)
from mutascope.features.store import ModelSource, build_store
from mutascope.scoring.cache import CacheKey, LogProbCache, embedding_method
from mutascope.utils.config import as_str_dict, load_config

CONF = Path(__file__).parents[2] / "conf"
TARGETS = {"S": "MKVA", "N": "MKVAR"}
"""``S`` has a structure, ``N`` none."""


@pytest.fixture
def store(tmp_path: Path) -> Path:
    """Two models: ``seq`` on both assays, ``struct`` (width 2) on ``S`` only."""
    log_probs = LogProbCache(tmp_path / "logprobs")
    embeddings = LogProbCache(tmp_path / "embeddings", width=None)
    rng = np.random.default_rng(0)
    sources: list[ModelSource] = []
    for name, assays, hidden in (("seq", ["S", "N"], 3), ("struct", ["S"], 2)):
        targets: dict[str, list[LandscapeTarget]] = {}
        for assay in assays:
            seq = TARGETS[assay]
            target = LandscapeTarget(
                assay, ScoringRegion(assay, 0, len(seq), len(seq)), seq, None, benchmark=True
            )
            targets[assay] = [target]
            for method, cols, cache in (
                ("masked", 20, log_probs),
                (embedding_method(4), hidden, embeddings),
            ):
                key = CacheKey(f"hub/{name}", "r" * 40, seq, None, method, "full")
                cache.put(key, rng.normal(size=(len(seq), cols)).astype(np.float32))
        sources.append(ModelSource(name, f"hub/{name}", "r" * 40, targets, embeddings, 4))
    structure = pd.DataFrame(
        {
            "position": [0, 1, 2, 3],
            "residue": list("MKVA"),
            "plddt": [90.0, 80.0, 70.0, 60.0],
            "rel_sasa": [0.1, 0.2, 0.3, 0.4],
            "contact_count": [1, 2, 3, 4],
        }
    )
    reference = pd.DataFrame({"DMS_id": list(TARGETS), "target_seq": list(TARGETS.values())})
    keys = pd.DataFrame(columns=["assay_id", "position", "wt_aa", "mt_aa"])
    build_store(reference, sources, log_probs, {"S": structure}, keys, tmp_path / "store")
    return tmp_path / "store"


SEQ = FeatureSet(
    "seq", ("onehot", "blosum62", "rel_position", "seq:score", "seq:log_probs", "seq:embedding"),
    structure=False,
)  # fmt: skip
SEQ_STRUCT = FeatureSet(
    "seq_struct", (*SEQ.blocks, "struct:score", "struct:embedding", "structure"), structure=True
)


def test_the_configured_sets_have_the_documented_width() -> None:
    cfg = load_config(CONF, [])
    sets = feature_sets(
        as_str_dict(OmegaConf.to_container(cfg.features.sets, resolve=True), "features.sets")
    )
    hidden = {"esm2_650m": 1280, "saprot_650m_af2": 1280}
    assert width(sets["seq"], hidden) == 40 + 1 + 1 + 1 + 20 + 1280 == 1343
    assert width(sets["seq_struct"], hidden) == 1343 + 1 + 20 + 1280 + 3 == 2647
    assert sets["seq"].models == ["esm2_650m"] and not sets["seq"].structure
    assert sets["seq_struct"].models == ["esm2_650m", "saprot_650m_af2"]
    assert sets["seq_struct"].structure


def test_every_substitution_with_named_columns(store: Path) -> None:
    matrix = assemble(store, SEQ, "N")
    assert matrix.features.shape == (19 * 5, width(SEQ, {"seq": 3}))
    assert matrix.features.dtype == np.float32
    assert len(matrix.names) == matrix.features.shape[1]
    assert matrix.names[:2] == ["onehot_wt_A", "onehot_wt_C"]
    assert matrix.names[-1] == "seq:embedding_2"
    onehot = matrix.features[:, :40]
    assert (onehot.sum(axis=1) == 2).all()
    first = matrix.keys.iloc[0]
    assert onehot[0, AA_ORDER.index(first.wt_aa)] == 1
    assert onehot[0, 20 + AA_ORDER.index(first.mt_aa)] == 1


def test_per_residue_blocks_repeat_for_the_19_substitutions_of_a_residue(store: Path) -> None:
    matrix = assemble(store, SEQ, "N")
    embedding = matrix.features[:, -3:]
    stored = np.load(store / "arrays" / "assay_id=N" / "seq.embedding.npy")
    np.testing.assert_array_equal(embedding, stored[matrix.keys["position"].to_numpy()])
    assert len(np.unique(embedding, axis=0)) == 5


def test_keys_come_back_in_the_order_asked(store: Path) -> None:
    keys = pd.DataFrame(
        {"position": [4, 0, 2], "wt_aa": ["R", "M", "V"], "mt_aa": ["A", "W", "C"], "x": 1}
    )
    matrix = assemble(store, SEQ, "N", keys)
    pd.testing.assert_frame_equal(matrix.keys, keys[["position", "wt_aa", "mt_aa"]])
    every = assemble(store, SEQ, "N")
    index = [
        int(np.flatnonzero((every.keys["position"] == p) & (every.keys["mt_aa"] == m))[0])
        for p, m in zip(keys["position"], keys["mt_aa"], strict=True)
    ]
    np.testing.assert_array_equal(matrix.features, every.features[index])


def test_a_key_with_another_wild_type_fails(store: Path) -> None:
    keys = pd.DataFrame({"position": [0], "wt_aa": ["A"], "mt_aa": ["W"]})
    with pytest.raises(FeatureSetError, match="another wild type"):
        assemble(store, SEQ, "N", keys)


def test_a_structure_set_is_defined_only_where_there_is_a_structure(store: Path) -> None:
    matrix = assemble(store, SEQ_STRUCT, "S")
    assert matrix.features.shape == (19 * 4, width(SEQ_STRUCT, {"seq": 3, "struct": 2}))
    assert matrix.names[-3:] == ["plddt", "rel_sasa", "contact_count"]
    with pytest.raises(FeatureSetError, match="needs a structure"):
        assemble(store, SEQ_STRUCT, "N")
    assert assays_of(SEQ_STRUCT, ["S", "N"], {"S"}) == ["S"]
    assert assays_of(SEQ, ["S", "N"], {"S"}) == ["S", "N"]


def test_a_model_block_without_input_fails_instead_of_returning_nan(store: Path) -> None:
    no_structure_flag = FeatureSet("bad", ("struct:score",), structure=False)
    with pytest.raises(FeatureSetError, match="no input"):
        assemble(store, no_structure_flag, "N")


@pytest.mark.parametrize(
    ("blocks", "structure", "match"),
    [
        (("onehot", "nope"), False, "unknown block"),
        (("seq:logits",), False, "unknown block"),
        (("onehot", "onehot"), False, "twice"),
        (("structure",), False, "structure: true"),
    ],
)
def test_malformed_sets_are_rejected(blocks: tuple[str, ...], structure: bool, match: str) -> None:
    with pytest.raises(FeatureSetError, match=match):
        FeatureSet("bad", blocks, structure=structure)
