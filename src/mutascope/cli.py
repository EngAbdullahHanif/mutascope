"""Typer entry point: ``mutascope <group> <cmd>``.

Every pipeline step is a command here; Nextflow only ever calls these commands.
"""

import csv
from pathlib import Path
from typing import Annotated

import httpx
import typer
from omegaconf import DictConfig

from mutascope import __version__
from mutascope.data.clean import clean_dataset
from mutascope.data.download import DownloadError, fetch_release
from mutascope.data.release import load_release
from mutascope.data.report import write_report
from mutascope.data.structure_features import (
    FeatureParams,
    StructureFeatureError,
    build_structure_features,
)
from mutascope.data.structure_report import (
    StructureReportError,
    structure_report,
    write_structure_report,
)
from mutascope.data.structures import StructureCheck, StructureMappingError, map_structures
from mutascope.data.three_di import FoldseekConfig, FoldseekError, build_three_di
from mutascope.data.validate import VariantValidationError, check_assays
from mutascope.utils.config import load_config
from mutascope.utils.logging import configure_logging

app = typer.Typer(
    name="mutascope",
    help="Functional-effect landscapes for single amino-acid substitutions.",
    no_args_is_help=True,
)
data_app = typer.Typer(help="Acquire and validate ProteinGym data.", no_args_is_help=True)
app.add_typer(data_app, name="data")

ConfigDir = Annotated[
    Path,
    typer.Option("--config-dir", help="Hydra config directory.", exists=True, file_okay=False),
]
Overrides = Annotated[
    list[str] | None,
    typer.Argument(help="Hydra overrides, e.g. data.data_dir=/scratch/data", show_default=False),
]
Reference = Annotated[
    Path, typer.Option(help="ProteinGym reference CSV.", exists=True, dir_okay=False)
]
StructuresDir = Annotated[
    Path, typer.Option(help="Directory of AF2 PDB files.", exists=True, file_okay=False)
]


@app.callback()
def main(
    log_json: Annotated[
        bool, typer.Option("--log-json", help="Emit structured JSON logs.")
    ] = False,
) -> None:
    """Configure logging before any command runs."""
    configure_logging(json=log_json)


@app.command()
def version() -> None:
    """Print the installed MutaScope version."""
    typer.echo(__version__)


@data_app.command("download")
def data_download(config_dir: ConfigDir = Path("conf"), overrides: Overrides = None) -> None:
    """Download the pinned ProteinGym release into the raw dir and verify every checksum."""
    release = load_release(load_config(config_dir, overrides).data)
    timeout = httpx.Timeout(30.0, read=120.0)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            fetch_release(release, client)
    except (DownloadError, httpx.HTTPError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@data_app.command("check-structures")
def data_check_structures(
    reference: Annotated[
        Path, typer.Option(help="ProteinGym reference CSV.", exists=True, dir_okay=False)
    ],
    structures_dir: Annotated[
        Path, typer.Option(help="Directory of AF2 PDB files.", exists=True, file_okay=False)
    ],
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Fail unless every assay maps to a PDB whose sequence matches its target range.

    Assays listed under ``data.structure_exceptions`` are reported as allowed exceptions.
    """
    exceptions = load_release(load_config(config_dir, overrides).data).structure_exceptions
    try:
        check = map_structures(reference, structures_dir, exceptions)
    except StructureMappingError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"{len(check.mappings)} assays mapped to structures")
    if check.allowed_exceptions:
        typer.echo(f"{len(check.allowed_exceptions)} allowed exceptions (no structure):")
        for allowed in check.allowed_exceptions:
            typer.echo(f"  {allowed.assay_id}: {allowed.documented_reason}")


@data_app.command("check-variants")
def data_check_variants(
    reference: Annotated[
        Path, typer.Option(help="ProteinGym reference CSV.", exists=True, dir_okay=False)
    ],
    dms_dir: Annotated[
        Path,
        typer.Option(help="Directory of per-assay DMS CSVs.", exists=True, file_okay=False),
    ],
) -> None:
    """Fail unless every variant's wild-type residues match its assay's target sequence.

    Also validates each CSV against the raw variant schema. Every problem is listed; none is
    fixed or dropped.
    """
    try:
        check = check_assays(reference, dms_dir)
    except VariantValidationError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"{len(check.assays)} assays, {check.rows} variants "
        f"({check.singles} single, {check.multis} multi): 0 problems"
    )
    for assay in check.assays:
        if assay.extra_columns:
            typer.echo(f"  {assay.assay_id}: extra columns {', '.join(assay.extra_columns)}")


@data_app.command("clean")
def data_clean(
    reference: Annotated[
        Path, typer.Option(help="ProteinGym reference CSV.", exists=True, dir_okay=False)
    ],
    dms_dir: Annotated[
        Path,
        typer.Option(help="Directory of per-assay DMS CSVs.", exists=True, file_okay=False),
    ],
    out_dir: Annotated[
        Path, typer.Option(help="Output directory for assay_id=<id>/ Parquet partitions.")
    ],
    report_dir: Annotated[
        Path, typer.Option(help="Directory for cleaning_report.parquet and .md.")
    ],
) -> None:
    """Write the processed single-mutant table and a per-assay cleaning report.

    Removes multi-mutants, rows with missing values and duplicates, each counted per assay.
    Wild-type mismatches and malformed values fail the run; nothing is written then.
    """
    try:
        report = clean_dataset(reference, dms_dir, out_dir)
    except VariantValidationError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    parquet, markdown = write_report(report, report_dir)
    typer.echo(
        f"{len(report)} assays: {int(report['rows_in'].sum())} rows in, "
        f"{int(report['rows_out'].sum())} single mutants out -> {out_dir}"
    )
    typer.echo(f"report: {parquet}, {markdown}")


def _structure_check(
    reference: Path, structures_dir: Path, config_dir: Path, overrides: list[str] | None
) -> tuple[StructureCheck, DictConfig]:
    """Map assays to structures (exceptions skipped), or exit 1 listing every problem."""
    cfg = load_config(config_dir, overrides)
    exceptions = load_release(cfg.data).structure_exceptions
    try:
        return map_structures(reference, structures_dir, exceptions), cfg
    except StructureMappingError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@data_app.command("structure-features")
def data_structure_features(
    reference: Reference,
    structures_dir: StructuresDir,
    out_dir: Annotated[Path, typer.Option(help="Output dir for assay_id=<id>/ partitions.")],
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Write per-residue pLDDT, SASA, secondary structure and contact counts per assay.

    Only assays whose structure matches their target range get rows; documented
    ``structure_exceptions`` get none.
    """
    check, cfg = _structure_check(reference, structures_dir, config_dir, overrides)
    params = FeatureParams.from_config(cfg.structure)
    try:
        rows = build_structure_features(check, out_dir, params)
    except StructureFeatureError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"{len(check.mappings)} assays, {rows} residues -> {out_dir}")
    typer.echo(f"{len(check.allowed_exceptions)} documented exceptions skipped")


@data_app.command("foldseek-3di")
def data_foldseek_3di(
    reference: Reference,
    structures_dir: StructuresDir,
    out_dir: Annotated[Path, typer.Option(help="Output dir for assay_id=<id>/ partitions.")],
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Write the Foldseek 3Di state of every structure residue per assay.

    Requires the Foldseek version pinned in ``structure.foldseek.version``.
    """
    check, cfg = _structure_check(reference, structures_dir, config_dir, overrides)
    config = FoldseekConfig.from_config(cfg.structure.foldseek)
    try:
        rows = build_three_di(check, out_dir, config)
    except FoldseekError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"{len(check.mappings)} assays, {rows} residues -> {out_dir}")
    typer.echo(f"{len(check.allowed_exceptions)} documented exceptions skipped")


@data_app.command("structure-report")
def data_structure_report(
    reference: Reference,
    structures_dir: StructuresDir,
    variants_dir: Annotated[
        Path, typer.Option(help="Processed variant partitions.", exists=True, file_okay=False)
    ],
    features_dir: Annotated[
        Path, typer.Option(help="Structure feature partitions.", exists=True, file_okay=False)
    ],
    three_di_dir: Annotated[
        Path, typer.Option(help="3Di partitions.", exists=True, file_okay=False)
    ],
    report_dir: Annotated[Path, typer.Option(help="Directory for structure_report.*.")],
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Cross-check the structure outputs and write a per-assay report (Parquet + Markdown)."""
    check, cfg = _structure_check(reference, structures_dir, config_dir, overrides)
    threshold = FeatureParams.from_config(cfg.structure).low_plddt_threshold
    with reference.open(newline="") as fh:
        lengths = {r["DMS_id"]: len(r["target_seq"]) for r in csv.DictReader(fh)}
    try:
        report = structure_report(
            check,
            target_lengths=lengths,
            variants_dir=variants_dir,
            features_dir=features_dir,
            three_di_dir=three_di_dir,
            low_plddt_threshold=threshold,
        )
    except StructureReportError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    parquet, markdown = write_structure_report(report, report_dir, low_plddt_threshold=threshold)
    typer.echo(f"report: {parquet}, {markdown}")
