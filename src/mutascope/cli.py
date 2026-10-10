"""Typer entry point: ``mutascope <group> <cmd>``.

Every pipeline step is a command here; Nextflow only ever calls these commands.
"""

from pathlib import Path
from typing import Annotated

import httpx
import typer

from mutascope import __version__
from mutascope.data.clean import clean_dataset
from mutascope.data.download import DownloadError, fetch_release
from mutascope.data.release import load_release
from mutascope.data.report import write_report
from mutascope.data.structures import StructureMappingError, map_structures
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
