"""Write the whole bundle from loaded inputs, and prove its rounding harmless (ADR 0028).

The CLI (``mutascope export bundle``) loads the inputs; this module does the rest without I/O
other than the bundle folder: every protein file is built, written, read back and decoded,
and the decoded file is what the fidelity check (:mod:`mutascope.export.fidelity`) scores.
The round trip is therefore tested on the real bytes, not on an in-memory copy.

The bundle is swapped into place only if every number passed and every file is within its
budget; the size and fidelity tables are returned either way, so a failure can be read.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

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
)
from mutascope.export.fidelity import Tolerances, assay_values, fidelity_table
from mutascope.export.format import Benchmarks, Manifest, VersionStamp
from mutascope.export.schemas import BundleSizeSchema
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


@dataclass(frozen=True)
class Budget:
    """Size limits (``export.budget``): per protein file, and for all of them."""

    protein_bytes: int
    total_bytes: int


@dataclass(frozen=True)
class BundleResult:
    """What :func:`write_bundle` produced. ``manifest`` is None when it failed."""

    manifest: Manifest | None
    fidelity: pd.DataFrame
    sizes: pd.DataFrame
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
    writer = BundleWriter(out_dir, budget_bytes=budget.protein_bytes)
    source: dict[str, dict[tuple[str, str], float]] = {}
    encoded: dict[str, dict[tuple[str, str], float]] = {}
    sizes: list[dict[str, object]] = []
    try:
        for record in inputs.manifest.sort_values("assay_id").to_dict(orient="records"):
            row = {str(k): v for k, v in record.items()}
            assay_id = str(row["assay_id"])
            assay = by_assay["assays"][assay_id]
            protein, grids = build_protein(
                assay={str(k): v for k, v in assay.iloc[0].to_dict().items()},
                variants=by_assay["variants"].get(assay_id, inputs.variants.iloc[:0]),
                structure=by_assay["structure"].get(assay_id),
                manifest_row=row,
                supervised=by_assay["landscapes"].get(assay_id),
                supervised_info=supervised_info,
                zero_shot=inputs.zero_shot,
                msa_region=inputs.msa_regions.get(assay_id),
                n_assays_for_protein=int(n_for_protein[str(assay.iloc[0]["UniProt_ID"])]),
                encoding=encoding,
                version=version,
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
    failure = _failure(fidelity, size_table, budget)
    if failure is not None:
        writer.abort()
        return BundleResult(None, fidelity, size_table, failure)
    try:
        readme = readme_text(card, version, size_table["bytes"].astype(int).tolist())
        manifest = writer.finish(benchmarks, version, readme)
    except BundleError as exc:
        return BundleResult(None, fidelity, size_table, str(exc))
    return BundleResult(manifest, fidelity, size_table, None)


def _failure(fidelity: pd.DataFrame, sizes: pd.DataFrame, budget: Budget) -> str | None:
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
    total = int(sizes["bytes"].sum())
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


def readme_text(card: str, version: VersionStamp, sizes: Sequence[int]) -> str:
    """The bundle folder's README: the card, then the version stamp."""
    stamp = version.model_dump()
    supervised = stamp.pop("supervised")
    lines = [f"- `{k}`: `{v}`" for k, v in stamp.items()]
    lines += [f"- `supervised.{k}`: `{v}`" for k, v in supervised.items()]
    return (
        card.rstrip()
        + "\n\n## Version\n\n"
        + "\n".join(lines)
        + f"\n\n{len(sizes)} protein files, {sum(sizes):,} bytes compressed.\n"
    )
