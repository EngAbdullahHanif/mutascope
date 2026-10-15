"""The feature store: every possible substitution of every target, with its features (ADR 0020).

Built from what earlier steps wrote, with no model and no label:

- masked-marginal log-probabilities from the log-probability cache (complete at every residue
  since ADR 0018; ``mutascope score check-landscapes``);
- embeddings from the embedding cache (``mutascope features embed``);
- pLDDT, relative SASA and contact count from ``processed/structure_features``.

**Which landscape a residue reads.** A model has a benchmark-region landscape per assay and,
where that region is only part of the target, a full-target one (ADR 0018). A residue inside
the benchmark region reads the benchmark landscape, which is what the zero-shot benchmark and
the model gate used; a residue outside it reads the full-target landscape. Every measured
variant is inside its model's benchmark region (0 of 696,311 outside for ESM-2, 0 of 665,793
for SaProt), so training rows only ever see the benchmark numbers. The same rule applies to
log-probabilities, scores and embeddings, and ``positions.region_<model>`` records it per
residue.

**Labels.** Nothing here reads ``DMS_score`` or ``DMS_score_bin``. The only read of the
processed variants is of their keys, to check that every measured variant is in the store
with the same wild type (ADR 0019, decision 4).

Positions are 0-indexed throughout; a variant is identified by ``(position, mt_aa)``, so no
mutation string is formatted or parsed here.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import duckdb
import numpy as np
import numpy.typing as npt
import pandas as pd
from biotite.sequence import ProteinSequence  # pyright: ignore[reportMissingTypeStubs]
from biotite.sequence.align import SubstitutionMatrix  # pyright: ignore[reportMissingTypeStubs]

from mutascope.alphabet import AA_ORDER
from mutascope.data.db import literal
from mutascope.eval.landscapes import LandscapeTarget
from mutascope.features.schemas import (
    SUBSTITUTIONS_PER_RESIDUE,
    FeaturePositionSchema,
    FeatureStoreReportSchema,
    FeatureVariantSchema,
)
from mutascope.scoring.cache import CacheKey, LogProbCache, embedding_method
from mutascope.utils.io import fresh_tmp_dir, replace_dir
from mutascope.utils.logging import get_logger

log = get_logger(__name__)

Float32Array = npt.NDArray[np.float32]

STRUCTURE_COLUMNS = ("plddt", "rel_sasa", "contact_count")
ARRAY_KINDS = ("log_probs", "embedding")
_AA_INDEX = {aa: i for i, aa in enumerate(AA_ORDER)}


class FeatureStoreError(ValueError):
    """An input the store needs is missing or disagrees with the target sequence."""


@dataclass(frozen=True)
class ModelSource:
    """One zero-shot model's inputs to the store."""

    name: str
    """The model config's ``name``, e.g. ``esm2_650m``; used in column and file names."""
    hf_id: str
    revision: str
    targets: Mapping[str, Sequence[LandscapeTarget]]
    """Per assay, its landscapes (:func:`~mutascope.eval.landscapes.landscape_targets`). An
    assay that is absent has no input from this model."""
    embedding_cache: LogProbCache
    embedding_layers: int


@dataclass(frozen=True)
class AssayFeatures:
    """One assay's partition of the store, validated."""

    variants: pd.DataFrame
    positions: pd.DataFrame
    arrays: dict[str, Float32Array]
    """``<model>.<kind>`` -> ``(L, width)``."""
    residues_from_full_target: int


def array_name(model: str, kind: str) -> str:
    """File stem of a per-residue array: ``esm2_650m.embedding``."""
    if kind not in ARRAY_KINDS:
        raise ValueError(f"unknown array kind {kind!r}; known: {list(ARRAY_KINDS)}")
    return f"{model}.{kind}"


@cache
def blosum62() -> Float32Array:
    """``(20, 20)`` BLOSUM62 in ``AA_ORDER`` (biotite's copy of the NCBI matrix)."""
    alphabet = ProteinSequence.alphabet
    matrix = SubstitutionMatrix(alphabet, alphabet, "BLOSUM62")
    score = matrix.get_score  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return np.array([[score(a, b) for b in AA_ORDER] for a in AA_ORDER], dtype=np.float32)


def substitution_grid(target_seq: str) -> tuple[npt.NDArray[np.int64], npt.NDArray[np.int64]]:
    """Every single substitution of ``target_seq`` as (position, mutant column in AA_ORDER).

    Ordered by position, then by mutant in ``AA_ORDER``, wild type left out: ``19 * L`` rows.
    """
    wt = np.array([_AA_INDEX[aa] for aa in target_seq])
    position = np.repeat(np.arange(len(target_seq)), len(AA_ORDER))
    column = np.tile(np.arange(len(AA_ORDER)), len(target_seq))
    keep = column != wt[position]
    return position[keep], column[keep]


def stitch(
    targets: Sequence[LandscapeTarget],
    target_length: int,
    read: Callable[[LandscapeTarget], Float32Array],
) -> tuple[Float32Array, list[str]]:
    """One ``(L, width)`` array over the whole target from a model's landscapes of one assay.

    Residues inside the benchmark region come from the benchmark landscape, the others from
    the full-target one (see the module docstring).

    Returns:
        The array, and per residue the region label it was read from.

    Raises:
        FeatureStoreError: There is no benchmark landscape, a partial benchmark region has no
            full-target landscape beside it, or a read is incomplete.
    """
    benchmark = [t for t in targets if t.benchmark]
    if len(benchmark) != 1:
        raise FeatureStoreError(f"expected one benchmark landscape, got {len(benchmark)}")
    bench = benchmark[0]
    pieces = [(bench, read(bench))]
    if not bench.full_target:
        full = [t for t in targets if t.full_target]
        if len(full) != 1:
            raise FeatureStoreError(
                f"{bench.assay_id}: benchmark region {bench.region.label} has no full-target "
                "landscape beside it"
            )
        pieces.insert(0, (full[0], read(full[0])))
    width = pieces[0][1].shape[1]
    array = np.full((target_length, width), np.nan, dtype=np.float32)
    labels = [""] * target_length
    for target, values in pieces:  # full target first, so the benchmark region overwrites it
        start, end = target.region.start, target.region.end
        array[start:end] = values
        labels[start:end] = [target.region.label] * (end - start)
    return array, labels


class _Reader:
    """Reads one kind of array of one model from its cache, refusing incomplete entries."""

    def __init__(self, source: ModelSource, kind: str, logprob_cache: LogProbCache) -> None:
        self.source, self.kind = source, kind
        self.cache = logprob_cache if kind == "log_probs" else source.embedding_cache
        self.method = "masked" if kind == "log_probs" else embedding_method(source.embedding_layers)

    def __call__(self, target: LandscapeTarget) -> Float32Array:
        key = CacheKey(
            model_id=self.source.hf_id,
            revision=self.source.revision,
            sequence=target.sequence,
            struct_tokens=target.struct_tokens,
            method=self.method,
            region=target.region.label,
        )
        entry = self.cache.get(key)
        where = f"{self.source.name} {self.kind}, {target.assay_id} ({target.region.label})"
        if entry is None:
            hint = "features embed" if self.kind == "embedding" else "score landscapes"
            raise FeatureStoreError(f"{where}: not in the cache; run `mutascope {hint}`")
        if not np.isfinite(entry).all():
            missing = int((~np.isfinite(entry).all(axis=1)).sum())
            raise FeatureStoreError(f"{where}: {missing} residues not computed")
        return entry


def assay_features(
    assay_id: str,
    target_seq: str,
    sources: Sequence[ModelSource],
    logprob_cache: LogProbCache,
    structure: pd.DataFrame | None,
) -> AssayFeatures:
    """Every substitution of one assay's target with its features.

    Args:
        assay_id: The assay.
        target_seq: Its target sequence (ProteinGym reference).
        sources: The models; one whose ``targets`` lack the assay gets NaN / null columns and
            no arrays.
        logprob_cache: The masked-marginal cache.
        structure: The assay's ``processed/structure_features`` rows (``position``,
            ``residue`` and :data:`STRUCTURE_COLUMNS`), or None without a structure.

    Raises:
        FeatureStoreError: See :func:`stitch`, or the structure's residues differ from the
            target.
    """
    length = len(target_seq)
    wt = np.array([_AA_INDEX[aa] for aa in target_seq])
    position, column = substitution_grid(target_seq)
    variants = pd.DataFrame(
        {
            "assay_id": assay_id,
            "position": position,
            "wt_aa": [target_seq[p] for p in position],
            "mt_aa": [AA_ORDER[c] for c in column],
            "rel_position": (position / max(length - 1, 1)).astype(np.float32),
            "blosum62": blosum62()[wt[position], column],
        }
    )
    positions = pd.DataFrame(
        {"assay_id": assay_id, "position": np.arange(length), "wt_aa": list(target_seq)}
    )
    for name in STRUCTURE_COLUMNS:
        positions[name] = np.full(length, np.nan, dtype=np.float32)
    if structure is not None:
        rows = structure["position"].to_numpy(np.int64)
        if (
            (rows < 0).any()
            or (rows >= length).any()
            or any(
                target_seq[p] != r
                for p, r in zip(rows, structure["residue"].astype(str), strict=True)
            )
        ):
            raise FeatureStoreError(f"{assay_id}: structure residues differ from the target")
        for name in STRUCTURE_COLUMNS:
            positions.loc[rows, name] = structure[name].to_numpy(np.float32)
    arrays: dict[str, Float32Array] = {}
    from_full = np.zeros(length, dtype=bool)
    for source in sources:
        targets = source.targets.get(assay_id)
        if targets is None:
            variants[f"score_{source.name}"] = np.full(len(variants), np.nan, dtype=np.float32)
            positions[f"region_{source.name}"] = pd.Series([None] * length, dtype="str")
            continue
        try:
            log_probs, labels = stitch(targets, length, _Reader(source, "log_probs", logprob_cache))
            embedding, embedding_labels = stitch(
                targets, length, _Reader(source, "embedding", logprob_cache)
            )
        except FeatureStoreError as exc:
            raise FeatureStoreError(f"{assay_id}: {exc}") from exc
        if labels != embedding_labels:  # same targets, same rule: a mismatch is a bug here
            raise FeatureStoreError(f"{assay_id}: {source.name} arrays come from different regions")
        score = log_probs[position, column] - log_probs[position, wt[position]]
        variants[f"score_{source.name}"] = score.astype(np.float32)
        positions[f"region_{source.name}"] = labels
        arrays[array_name(source.name, "log_probs")] = log_probs
        arrays[array_name(source.name, "embedding")] = embedding
        if not next(t for t in targets if t.benchmark).full_target:
            from_full |= np.array([label == "full" for label in labels])
    return AssayFeatures(
        variants=FeatureVariantSchema.validate(variants, lazy=True),
        positions=FeaturePositionSchema.validate(positions, lazy=True),
        arrays=arrays,
        residues_from_full_target=int(from_full.sum()),
    )


def read_structure_features(structure_features_dir: Path) -> dict[str, pd.DataFrame]:
    """``processed/structure_features`` per assay: ``position``, ``residue`` and the features."""
    pattern = literal(str(structure_features_dir.resolve() / "*" / "*.parquet"))
    columns = ", ".join(STRUCTURE_COLUMNS)
    query = (
        f"SELECT assay_id, position, residue, {columns} FROM read_parquet({pattern}, "
        "hive_partitioning = true, hive_types = {'assay_id': 'VARCHAR'}) "
        "ORDER BY assay_id, position"
    )
    with duckdb.connect() as con:
        table = con.execute(query).df()
    return {
        str(assay_id): rows.drop(columns="assay_id").reset_index(drop=True)
        for assay_id, rows in table.groupby("assay_id", sort=True)
    }


def measured_keys(variants_dir: Path) -> pd.DataFrame:
    """``assay_id, position, wt_aa, mt_aa`` of the processed single mutants. Keys only: the
    labels in the same files are never selected."""
    pattern = literal(str(variants_dir.resolve() / "*" / "*.parquet"))
    query = (
        f"SELECT assay_id, position, wt_aa, mt_aa FROM read_parquet({pattern}, "
        "hive_partitioning = true, hive_types = {'assay_id': 'VARCHAR'})"
    )
    with duckdb.connect() as con:
        return con.execute(query).df()


def check_measured(assay_id: str, variants: pd.DataFrame, measured: pd.DataFrame) -> None:
    """Fail unless every measured variant of the assay is a store row with the same wild type.

    Raises:
        FeatureStoreError: Listing the first few variants that are missing or whose wild type
            differs from the target (CLAUDE.md, data rule 3: never dropped or fixed).
    """
    joined = measured.merge(
        variants[["position", "wt_aa", "mt_aa"]],
        on=["position", "mt_aa"],
        how="left",
        suffixes=("", "_store"),
        indicator=True,
    )
    bad = joined[(joined["_merge"] != "both") | (joined["wt_aa"] != joined["wt_aa_store"])]
    if len(bad):
        rows = ", ".join(
            f"{r.wt_aa}@{r.position}->{r.mt_aa} (target {r.wt_aa_store})"
            for r in bad.head(5).itertuples()
        )
        raise FeatureStoreError(
            f"{assay_id}: {len(bad)} measured variants not in the store or with another wild "
            f"type: {rows}"
        )


def build_store(
    reference: pd.DataFrame,
    sources: Sequence[ModelSource],
    logprob_cache: LogProbCache,
    structures: Mapping[str, pd.DataFrame],
    measured: pd.DataFrame,
    out_dir: Path,
) -> pd.DataFrame:
    """Write the store for every assay of ``reference`` to ``out_dir``, replacing it whole.

    The store is built in ``<out_dir>.tmp`` and moved into place at the end, so a failed or
    interrupted build leaves the previous store (or none), never part of one.

    Args:
        reference: ProteinGym reference (``DMS_id``, ``target_seq``).
        sources: The models (:class:`ModelSource`).
        logprob_cache: The masked-marginal cache.
        structures: :func:`read_structure_features` output; an assay that is absent has no
            structure.
        measured: :func:`measured_keys` output.
        out_dir: ``processed/features``.

    Returns:
        One row per assay (:class:`FeatureStoreReportSchema`).
    """
    tmp = fresh_tmp_dir(out_dir)
    by_assay = dict(tuple(measured.groupby("assay_id", sort=False)))
    widths: dict[str, int] = {}
    rows: list[dict[str, object]] = []
    with duckdb.connect() as con:
        for assay_id, target_seq in zip(
            reference["DMS_id"].astype(str), reference["target_seq"].astype(str), strict=True
        ):
            structure = structures.get(assay_id)
            features = assay_features(assay_id, target_seq, sources, logprob_cache, structure)
            keys = by_assay.get(assay_id, measured.iloc[:0])
            check_measured(assay_id, features.variants, keys)
            part = f"assay_id={assay_id}"
            written: list[Path] = []
            for table, frame in (
                ("variants", features.variants),
                ("positions", features.positions),
            ):
                path = tmp / table / part / "data.parquet"
                path.parent.mkdir(parents=True)
                con.from_df(frame.drop(columns="assay_id")).write_parquet(str(path))
                written.append(path)
            for name, array in features.arrays.items():
                if widths.setdefault(name, array.shape[1]) != array.shape[1]:
                    raise FeatureStoreError(
                        f"{assay_id}: {name} has width {array.shape[1]}, other assays "
                        f"{widths[name]}"
                    )
                path = tmp / "arrays" / part / f"{name}.npy"
                path.parent.mkdir(parents=True, exist_ok=True)
                np.save(path, array)
                written.append(path)
            rows.append(
                {
                    "assay_id": assay_id,
                    "target_length": len(target_seq),
                    "variants": len(features.variants),
                    "measured": len(keys),
                    "structure": structure is not None,
                    "residues_with_structure": 0 if structure is None else len(structure),
                    "residues_from_full_target": features.residues_from_full_target,
                    "bytes": sum(p.stat().st_size for p in written),
                }
            )
            log.info("features_written", assay_id=assay_id, variants=len(features.variants))
    replace_dir(tmp, out_dir)
    report = FeatureStoreReportSchema.validate(pd.DataFrame.from_records(rows), lazy=True)
    expected = SUBSTITUTIONS_PER_RESIDUE * report["target_length"]
    if not (report["variants"] == expected).all():
        raise FeatureStoreError("an assay does not have 19 variants per residue")
    return report
