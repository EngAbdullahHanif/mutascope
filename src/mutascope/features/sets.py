"""Named feature sets over the feature store, assembled into one matrix per assay (ADR 0020).

A feature set is a list of blocks, declared in ``conf/features/default.yaml``:

- ``onehot``: wild type and mutant, one-hot in ``AA_ORDER`` (40 columns);
- ``blosum62``, ``rel_position``: one column each;
- ``structure``: pLDDT, relative SASA, contact count (3 columns);
- ``<model>:score``: the model's masked-marginal score of the substitution (1 column);
- ``<model>:log_probs``: its 20 log-probabilities at the position (20 columns);
- ``<model>:embedding``: its embedding of the position (hidden size, e.g. 1,280 columns).

A set with ``structure: true`` exists only on the assays with a structure; asking for another
assay fails, it is not filled with NaN. Per-residue blocks are stored once per residue and
repeated here for the 19 substitutions of that residue, so the store stays a few hundred MB
where the assembled matrices of all assays would be 8.8 GB (``seq``) and 16.8 GB
(``seq_struct``) in float32.

The input is variant keys only (``position, wt_aa, mt_aa``), never labels (ADR 0019).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd

from mutascope.alphabet import AA_ORDER
from mutascope.features.store import (
    ARRAY_KINDS,
    STRUCTURE_COLUMNS,
    FeatureStoreError,
    array_name,
)
from mutascope.utils.io import PARTITION_FILE

Float32Matrix = npt.NDArray[np.float32]

FIXED_BLOCKS = ("onehot", "blosum62", "rel_position", "structure")
MODEL_KINDS = ("score", *ARRAY_KINDS)
_AA_INDEX = {aa: i for i, aa in enumerate(AA_ORDER)}


class FeatureSetError(ValueError):
    """A feature set is malformed, or asked for on an assay it does not cover."""


@dataclass(frozen=True)
class FeatureSet:
    """A named list of blocks; see the module docstring."""

    name: str
    blocks: tuple[str, ...]
    structure: bool
    """Needs a structure: only defined on the assays that have one."""

    def __post_init__(self) -> None:
        for block in self.blocks:
            model, _, kind = block.partition(":")
            if block not in FIXED_BLOCKS and not (model and kind in MODEL_KINDS):
                raise FeatureSetError(f"{self.name}: unknown block {block!r}")
        if len(set(self.blocks)) != len(self.blocks):
            raise FeatureSetError(f"{self.name}: a block is listed twice")
        if "structure" in self.blocks and not self.structure:
            raise FeatureSetError(f"{self.name}: the structure block needs structure: true")

    @property
    def models(self) -> list[str]:
        """The zero-shot models whose blocks the set uses, in order of first use."""
        return list(dict.fromkeys(b.partition(":")[0] for b in self.blocks if ":" in b))


def feature_sets(config: Mapping[str, Any]) -> dict[str, FeatureSet]:
    """``features.sets`` from the config, by name."""
    return {
        str(name): FeatureSet(
            name=str(name),
            blocks=tuple(str(b) for b in spec["blocks"]),
            structure=bool(spec["structure"]),
        )
        for name, spec in config.items()
    }


@dataclass(frozen=True)
class AssayMatrix:
    """One assay's rows of a feature set."""

    keys: pd.DataFrame
    """``position, wt_aa, mt_aa`` of each row."""
    features: Float32Matrix
    names: list[str]
    """One name per column, e.g. ``onehot_wt_A`` or ``esm2_650m:embedding_17``."""


def assemble(
    store_dir: Path,
    feature_set: FeatureSet,
    assay_id: str,
    keys: pd.DataFrame | None = None,
) -> AssayMatrix:
    """The feature matrix of ``feature_set`` for one assay.

    Args:
        store_dir: ``processed/features`` (:func:`~mutascope.features.store.build_store`).
        feature_set: What to assemble.
        assay_id: The assay.
        keys: Variants to assemble, as ``position`` (0-indexed), ``wt_aa``, ``mt_aa``; any other
            column is ignored. None: every substitution in the store (``19 * L``).

    Raises:
        FeatureSetError: The set needs a structure and the assay has none, a model block has
            no input for the assay, or a key is not in the store or names another wild type.
    """
    part = f"assay_id={assay_id}"
    variant_path = store_dir / "variants" / part / PARTITION_FILE
    if not variant_path.exists():
        raise FeatureSetError(f"{assay_id}: not in the feature store {store_dir}")
    store = pd.read_parquet(variant_path)
    positions = pd.read_parquet(store_dir / "positions" / part / PARTITION_FILE)
    rows = _locate(store, keys, assay_id)
    variants = store.iloc[rows].reset_index(drop=True)
    has_structure = bool(positions["plddt"].notna().any())
    if feature_set.structure and not has_structure:
        raise FeatureSetError(f"{feature_set.name} needs a structure; {assay_id} has none")
    residue = variants["position"].to_numpy(np.int64)
    columns: list[Float32Matrix] = []
    names: list[str] = []
    for block in feature_set.blocks:
        values, labels = _block(block, store_dir / "arrays" / part, variants, positions, residue)
        if not np.isfinite(values).all() and block != "structure":
            raise FeatureSetError(f"{feature_set.name}: {block} has no input for {assay_id}")
        columns.append(values)
        names += labels
    features = np.hstack(columns).astype(np.float32, copy=False)
    return AssayMatrix(
        keys=variants[["position", "wt_aa", "mt_aa"]], features=features, names=names
    )


def _locate(store: pd.DataFrame, keys: pd.DataFrame | None, assay_id: str) -> npt.NDArray[np.intp]:
    """Row of ``store`` for each key, in key order; the wild type must match the target."""
    if keys is None:
        return np.arange(len(store))
    index = pd.MultiIndex.from_frame(store[["position", "mt_aa"]])
    wanted = pd.MultiIndex.from_frame(keys[["position", "mt_aa"]].astype({"position": "int64"}))
    rows = index.get_indexer(wanted)
    found = rows >= 0
    wt_ok = np.zeros(len(keys), dtype=bool)
    wt_ok[found] = store["wt_aa"].to_numpy()[rows[found]] == keys["wt_aa"].to_numpy()[found]
    if not wt_ok.all():
        bad = keys[~wt_ok].head(5)
        listed = ", ".join(f"{r.wt_aa}@{r.position}->{r.mt_aa}" for r in bad.itertuples())
        raise FeatureSetError(
            f"{assay_id}: {int((~wt_ok).sum())} keys not in the store or with another wild "
            f"type: {listed}"
        )
    return rows


def _block(
    block: str,
    arrays_dir: Path,
    variants: pd.DataFrame,
    positions: pd.DataFrame,
    residue: npt.NDArray[np.int64],
) -> tuple[Float32Matrix, list[str]]:
    n = len(variants)
    if block == "onehot":
        out = np.zeros((n, 2 * len(AA_ORDER)), dtype=np.float32)
        wt = variants["wt_aa"].map(_AA_INDEX).to_numpy(np.int64)
        mt = variants["mt_aa"].map(_AA_INDEX).to_numpy(np.int64)
        out[np.arange(n), wt] = 1
        out[np.arange(n), len(AA_ORDER) + mt] = 1
        return out, [f"onehot_wt_{a}" for a in AA_ORDER] + [f"onehot_mt_{a}" for a in AA_ORDER]
    if block in ("blosum62", "rel_position"):
        return variants[[block]].to_numpy(np.float32), [block]
    if block == "structure":
        return positions[list(STRUCTURE_COLUMNS)].to_numpy(np.float32)[residue], list(
            STRUCTURE_COLUMNS
        )
    model, _, kind = block.partition(":")
    if kind == "score":
        column = f"score_{model}"
        if column not in variants:
            raise FeatureSetError(f"the store has no {model} scores")
        return variants[[column]].to_numpy(np.float32), [block]
    path = arrays_dir / f"{array_name(model, kind)}.npy"
    if not path.exists():
        raise FeatureSetError(f"{block}: no {path.name} for {arrays_dir.name}")
    array = np.load(path)
    if array.shape[0] != len(positions):
        raise FeatureStoreError(f"{path}: {array.shape[0]} rows for {len(positions)} residues")
    return array[residue], [f"{block}_{j}" for j in range(array.shape[1])]


def width(feature_set: FeatureSet, hidden: Mapping[str, int]) -> int:
    """Columns of ``feature_set``, given each model's hidden size."""
    sizes = {"onehot": 2 * len(AA_ORDER), "blosum62": 1, "rel_position": 1, "structure": 3}
    total = 0
    for block in feature_set.blocks:
        model, _, kind = block.partition(":")
        if block in sizes:
            total += sizes[block]
        else:
            total += {"score": 1, "log_probs": len(AA_ORDER), "embedding": hidden[model]}[kind]
    return total


def assays_of(
    feature_set: FeatureSet, assays: Sequence[str], with_structure: set[str]
) -> list[str]:
    """The assays ``feature_set`` is defined on: all, or those with a structure."""
    return [a for a in assays if not feature_set.structure or a in with_structure]
