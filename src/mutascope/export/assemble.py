"""Write the whole bundle from loaded inputs, and prove its rounding harmless (ADR 0028).

The CLI (``mutascope export bundle``) loads the inputs; this module does the rest without I/O
other than the bundle folder: every protein file is built, written, read back and decoded,
and the decoded file is what the fidelity check (:mod:`mutascope.export.fidelity`) scores.
The round trip is therefore tested on the real bytes, not on an in-memory copy.

The bundle is swapped into place only if every number passed and every file is within its
budget; the size and fidelity tables are returned either way, so a failure can be read.

Structures (ADR 0039): each assay with a structure gets its PDB's backbone file, written once
per PDB, after every residue was checked against that assay's target at its mapped position
and its pLDDT against the published structure features. Structure files have their own size
budget (``structure_bytes``), not the protein files', and count towards the total.
"""

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from mutascope.data.structures import StructureMapping, parse_pdb_range
from mutascope.eval.region import ScoringRegion
from mutascope.export.bundle import (
    BundleError,
    BundleWriter,
    Encoding,
    SupervisedInfo,
    ZeroShotModel,
    build_protein,
    decoded_grids,
    load_protein,
    load_structure,
)
from mutascope.export.fidelity import Tolerances, assay_values, fidelity_table
from mutascope.export.format import Benchmarks, Manifest, StructureRef, VersionStamp
from mutascope.export.schemas import BundleSizeSchema, StructureSizeSchema
from mutascope.export.structure import (
    Backbone,
    StructureError,
    check_against_target,
    check_plddt,
    parse_backbone,
    structure_file,
)
from mutascope.utils.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class BundleInputs:
    """Everything the bundle is made from, loaded."""

    assays: pd.DataFrame
    """Published assays table (``PublishedAssaySchema``)."""
    variants: pd.DataFrame
    """Processed single mutants (``assay_id``, ``position``, ``wt_aa``, ``mt_aa``,
    ``DMS_score``)."""
    structure_features: pd.DataFrame
    manifest: pd.DataFrame
    """``FinalManifestSchema``: one row per assay, the bundle's list of proteins."""
    landscapes: pd.DataFrame
    """``FinalLandscapeSchema`` rows of every eligible assay."""
    reference: pd.DataFrame
    """ProteinGym reference, for the ProteinGym aggregate."""
    zero_shot: list[ZeroShotModel]
    msa_regions: Mapping[str, ScoringRegion]
    """ESM-2 benchmark region of the assays where it is part of the target."""
    reported: Mapping[tuple[str, str, str], float]
    """``(assay_id, model, metric)`` -> reported value comparable with the bundle."""
    structures: Mapping[str, str]
    """PDB file name -> text, from the verified AF2 archive
    (:func:`.structure.read_structures_zip`)."""
    low_plddt_threshold: float
    """``structure.low_plddt_threshold``: the viewer marks residues below it."""


@dataclass(frozen=True)
class Budget:
    """Size limits (``export.budget``): per protein file, and for all of them."""

    protein_bytes: int
    structure_bytes: int
    """Per structure file: fetched on its own, after the protein file, so not part of
    ``protein_bytes``."""
    total_bytes: int
    """Protein and structure files together."""


@dataclass(frozen=True)
class BundleResult:
    """What :func:`write_bundle` produced. ``manifest`` is None when it failed."""

    manifest: Manifest | None
    fidelity: pd.DataFrame
    sizes: pd.DataFrame
    structure_sizes: pd.DataFrame
    failure: str | None


def write_bundle(
    inputs: BundleInputs,
    *,
    out_dir: Path,
    benchmarks: Benchmarks,
    version: VersionStamp,
    encoding: Encoding,
    supervised_info: SupervisedInfo,
    budget: Budget,
    tolerances: Tolerances,
    card: str,
) -> BundleResult:
    """Build, write, decode and check every protein file; swap the bundle in if all passed.

    Raises:
        BundleError: An input disagrees with the target or another input (nothing written).
    """
    by_assay = _split(inputs)
    zero_names = [m.name for m in inputs.zero_shot]
    n_for_protein = inputs.assays.groupby("UniProt_ID")["assay_id"].count().to_dict()
    writer = BundleWriter(
        out_dir, budget_bytes=budget.protein_bytes, structure_budget_bytes=budget.structure_bytes
    )
    backbones: dict[str, Backbone] = {}
    users: dict[str, list[str]] = {}
    source: dict[str, dict[tuple[str, str], float]] = {}
    encoded: dict[str, dict[tuple[str, str], float]] = {}
    sizes: list[dict[str, object]] = []
    try:
        for record in inputs.manifest.sort_values("assay_id").to_dict(orient="records"):
            row = {str(k): v for k, v in record.items()}
            assay_id = str(row["assay_id"])
            assay = by_assay["assays"][assay_id]
            assay_row = {str(k): v for k, v in assay.iloc[0].to_dict().items()}
            features = by_assay["structure"].get(assay_id)
            ref = None
            if bool(assay_row["has_structure"]):
                ref = _structure_ref(
                    assay_row, features, inputs, writer, backbones, encoding.plddt_decimals
                )
                users.setdefault(str(assay_row["pdb_file"]), []).append(assay_id)
            protein, grids = build_protein(
                assay=assay_row,
                variants=by_assay["variants"].get(assay_id, inputs.variants.iloc[:0]),
                structure=features,
                manifest_row=row,
                supervised=by_assay["landscapes"].get(assay_id),
                supervised_info=supervised_info,
                zero_shot=inputs.zero_shot,
                msa_region=inputs.msa_regions.get(assay_id),
                n_assays_for_protein=int(n_for_protein[str(assay.iloc[0]["UniProt_ID"])]),
                encoding=encoding,
                version=version,
                structure_file=ref,
            )
            data = writer.add(protein)
            decoded = load_protein(data)
            if decoded != protein:
                raise BundleError(f"{assay_id}: the written file does not parse back unchanged")
            supervised = supervised_info.model if protein.supervised is not None else None
            source[assay_id] = assay_values(
                grids.grids, zero_shot=zero_names, supervised=supervised
            )
            encoded[assay_id] = assay_values(
                decoded_grids(decoded), zero_shot=zero_names, supervised=supervised
            )
            sizes.append(
                {
                    "assay_id": assay_id,
                    "length": protein.assay.length,
                    "supervised": protein.supervised is not None,
                    "bytes": len(data),
                    "raw_bytes": writer.raw_bytes[assay_id],
                    "budget_bytes": budget.protein_bytes,
                }
            )
    except BaseException:
        writer.abort()
        raise
    fidelity = fidelity_table(
        source,
        encoded,
        inputs.reported,
        reference=inputs.reference,
        tolerances=tolerances,
    )
    size_table = BundleSizeSchema.validate(pd.DataFrame.from_records(sizes), lazy=True)
    structure_table = StructureSizeSchema.validate(
        pd.DataFrame.from_records(
            [
                {
                    "pdb_file": pdb_file,
                    "file": path,
                    "n_residues": len(backbones[pdb_file].sequence),
                    "n_assays": len(users[pdb_file]),
                    "bytes": len(data),
                    "budget_bytes": budget.structure_bytes,
                }
                for pdb_file, (path, data) in sorted(writer.structures.items())
            ],
            columns=list(StructureSizeSchema.to_schema().columns),
        ).astype({"n_residues": int, "n_assays": int, "bytes": int, "budget_bytes": int}),
        lazy=True,
    )
    failure = _failure(fidelity, size_table, structure_table, budget)
    if failure is not None:
        writer.abort()
        return BundleResult(None, fidelity, size_table, structure_table, failure)
    try:
        readme = readme_text(
            card,
            version,
            size_table["bytes"].astype(int).tolist(),
            structure_table["bytes"].astype(int).tolist(),
        )
        manifest = writer.finish(benchmarks, version, readme)
    except BundleError as exc:
        return BundleResult(None, fidelity, size_table, structure_table, str(exc))
    return BundleResult(manifest, fidelity, size_table, structure_table, None)


def _structure_ref(
    assay: Mapping[str, object],
    features: pd.DataFrame | None,
    inputs: BundleInputs,
    writer: BundleWriter,
    backbones: dict[str, Backbone],
    plddt_decimals: int,
) -> StructureRef:
    """Check the assay's structure against its target and features; write it if new.

    Raises:
        BundleError: The PDB is not in the archive, or a residue or pLDDT disagrees.
    """
    assay_id, target = str(assay["assay_id"]), str(assay["target_seq"])
    pdb_file = str(assay["pdb_file"])
    try:
        if pdb_file not in backbones:
            if pdb_file not in inputs.structures:
                raise StructureError(f"{assay_id}: {pdb_file} is not in the AF2 archive")
            backbones[pdb_file] = parse_backbone(pdb_file, inputs.structures[pdb_file])
        backbone = backbones[pdb_file]
        first, last = parse_pdb_range(str(assay["pdb_range"]))
        # StructureMapping.offset is the one place pdb_range becomes a 0-based index.
        start = StructureMapping(assay_id, Path(pdb_file), first, last, target).offset
        end = start + (last - first + 1)
        check_against_target(assay_id, backbone, target, start, end)
        if features is None:
            raise StructureError(f"{assay_id}: a structure but no structure features")
        check_plddt(
            assay_id,
            backbone,
            dict(
                zip(
                    features["position"].astype(int),
                    features["plddt"].astype(float),
                    strict=True,
                )
            ),
            start,
        )
        encoded = structure_file(backbone, plddt_decimals)
    except (StructureError, ValueError) as exc:
        raise BundleError(str(exc)) from exc
    path, data = writer.add_structure(encoded)
    if load_structure(data) != encoded:
        raise BundleError(f"{path}: the written file does not parse back unchanged")
    return StructureRef(
        file=path,
        bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        start=start,
        end=end,
        low_plddt_threshold=inputs.low_plddt_threshold,
    )


def _failure(
    fidelity: pd.DataFrame, sizes: pd.DataFrame, structures: pd.DataFrame, budget: Budget
) -> str | None:
    """Why the bundle must not be published, or None."""
    problems: list[str] = []
    failed = fidelity[~fidelity["passed"]]
    if len(failed):
        listed = "; ".join(
            f"{r.assay_id or 'aggregate'} {r.model} {r.metric} (|diff| {r.diff_source:.2g} "
            f"source, {r.diff_reported} reported)"
            for r in failed.head(10).itertuples()
        )
        problems.append(f"{len(failed)} numbers moved beyond tolerance: {listed}")
    over = sizes[sizes["bytes"] > budget.protein_bytes]
    if len(over):
        listed = ", ".join(
            f"{a} ({b:,} bytes)" for a, b in zip(over["assay_id"], over["bytes"], strict=True)
        )
        problems.append(
            f"{len(over)} protein files over the {budget.protein_bytes:,}-byte budget: {listed}"
        )
    over = structures[structures["bytes"] > budget.structure_bytes]
    if len(over):
        listed = ", ".join(
            f"{a} ({b:,} bytes)" for a, b in zip(over["file"], over["bytes"], strict=True)
        )
        problems.append(
            f"{len(over)} structure files over the {budget.structure_bytes:,}-byte budget: {listed}"
        )
    total = int(sizes["bytes"].sum()) + int(structures["bytes"].sum())
    if total > budget.total_bytes:
        problems.append(f"bundle {total:,} bytes, over the {budget.total_bytes:,}-byte budget")
    return "; ".join(problems) or None


def _split(inputs: BundleInputs) -> dict[str, dict[str, pd.DataFrame]]:
    """Each table split by assay, once."""

    def split(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
        return {str(k): v for k, v in frame.groupby("assay_id", sort=False)}

    out = {
        "assays": split(inputs.assays),
        "variants": split(inputs.variants),
        "structure": split(inputs.structure_features),
        "landscapes": split(inputs.landscapes),
    }
    missing = sorted(set(inputs.manifest["assay_id"].astype(str)) - set(out["assays"]))
    if missing:
        raise BundleError(f"assays in the manifest but not in the assays table: {missing}")
    return out


def readme_text(
    card: str, version: VersionStamp, sizes: Sequence[int], structure_sizes: Sequence[int]
) -> str:
    """The bundle folder's README: the card, then the version stamp."""
    stamp = version.model_dump()
    supervised = stamp.pop("supervised")
    lines = [f"- `{k}`: `{v}`" for k, v in stamp.items()]
    lines += [f"- `supervised.{k}`: `{v}`" for k, v in supervised.items()]
    return (
        card.rstrip()
        + "\n\n## Version\n\n"
        + "\n".join(lines)
        + f"\n\n{len(sizes)} protein files, {sum(sizes):,} bytes compressed; "
        + f"{len(structure_sizes)} structure files, {sum(structure_sizes):,} bytes.\n"
    )
