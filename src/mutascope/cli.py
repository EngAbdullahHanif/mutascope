"""Typer entry point: ``mutascope <group> <cmd>``.

Every pipeline step is a command here; Nextflow only ever calls these commands.
"""

import csv
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import httpx
import pandas as pd
import typer
from omegaconf import DictConfig, OmegaConf

from mutascope import __version__
from mutascope.data.clean import clean_dataset
from mutascope.data.db import connect
from mutascope.data.download import DownloadError, fetch_release
from mutascope.data.manifest import Manifest
from mutascope.data.release import load_release
from mutascope.data.report import write_report
from mutascope.data.staging import SubsetError, link_release, subset_release
from mutascope.data.structure_features import (
    FeatureParams,
    StructureFeatureError,
    build_structure_features,
)
from mutascope.data.structure_report import (
    StructureReportError,
    read_foldseek_run,
    structure_report,
    write_structure_report,
)
from mutascope.data.structures import StructureCheck, StructureMappingError, map_structures
from mutascope.data.three_di import FoldseekConfig, FoldseekError, build_three_di, check_foldseek
from mutascope.data.validate import VariantValidationError, check_assays
from mutascope.eval.benchmark import (
    BootstrapParams,
    assay_metrics_table,
    read_results,
    summarise,
    write_results,
)
from mutascope.eval.proteingym import (
    PRECOMPUTED_PREFIX,
    RowSet,
    compare_with_published,
    load_precomputed_scores,
    published_assay_level,
    published_summary,
)
from mutascope.export.hub import PublishError, push_dataset, stage_dataset
from mutascope.utils.config import as_str_dict, load_config
from mutascope.utils.logging import configure_logging
from mutascope.utils.seeding import seed_all

app = typer.Typer(
    name="mutascope",
    help="Functional-effect landscapes for single amino-acid substitutions.",
    no_args_is_help=True,
)
data_app = typer.Typer(help="Acquire and validate ProteinGym data.", no_args_is_help=True)
app.add_typer(data_app, name="data")
eval_app = typer.Typer(help="Benchmark predictions against DMS assays.", no_args_is_help=True)
app.add_typer(eval_app, name="eval")

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


def _accept_overrides(config_dir: Path, overrides: list[str] | None) -> None:
    """Compose the config for a command that reads nothing from it yet.

    Every pipeline command takes the same Hydra overrides, so Nextflow can append one list to
    all of them. Composing still rejects a malformed or unknown override here, instead of
    silently ignoring it.
    """
    load_config(config_dir, overrides)


@data_app.command("download")
def data_download(
    link_dir: Annotated[
        Path | None,
        typer.Option(help="Also symlink the staged input layout (see data.staging) here."),
    ] = None,
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Download the pinned ProteinGym release into the raw dir and verify every checksum.

    The raw dir is a cache shared across runs; ``--link-dir`` gives the pipeline its inputs as
    paths without copying or re-downloading them.
    """
    release = load_release(load_config(config_dir, overrides).data)
    timeout = httpx.Timeout(30.0, read=120.0)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            fetch_release(release, client)
    except (DownloadError, httpx.HTTPError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if link_dir is not None:
        link_release(release, link_dir)
        typer.echo(f"inputs linked in {link_dir}")


@data_app.command("subset")
def data_subset(
    reference: Reference,
    dms_dir: Annotated[
        Path,
        typer.Option(help="Directory of per-assay DMS CSVs.", exists=True, file_okay=False),
    ],
    structures_dir: StructuresDir,
    assay: Annotated[list[str], typer.Option("--assay", help="Assay id to keep; repeat it.")],
    out_dir: Annotated[Path, typer.Option(help="Output dir (new or empty).")],
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Copy the named assays' reference rows, DMS CSVs and PDBs into ``out_dir``.

    Writes ``DMS_substitutions.csv``, ``dms/`` and ``structures/``, the layout every later step
    reads, so the pipeline's ``test`` profile runs the real steps on a couple of assays.
    """
    _accept_overrides(config_dir, overrides)
    try:
        subset = subset_release(reference, dms_dir, structures_dir, assay, out_dir)
    except SubsetError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"{len(assay)} assays, {subset.pdb_files} PDB files -> {out_dir}")


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
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Fail unless every variant's wild-type residues match its assay's target sequence.

    Also validates each CSV against the raw variant schema. Every problem is listed; none is
    fixed or dropped.
    """
    _accept_overrides(config_dir, overrides)
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
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Write the processed single-mutant table and a per-assay cleaning report.

    Removes multi-mutants, rows with missing values and duplicates, each counted per assay.
    Wild-type mismatches and malformed values fail the run; nothing is written then.
    """
    _accept_overrides(config_dir, overrides)
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
    """Write per-residue pLDDT, SASA and contact counts per assay.

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

    Foldseek is looked up first (FOLDSEEK_BIN, else ``foldseek`` on PATH) and must report the
    version pinned in ``structure.foldseek.version``; the binary used is recorded in the
    output directory and the structure report.
    """
    config = FoldseekConfig.from_config(load_config(config_dir, overrides).structure.foldseek)
    try:
        binary = check_foldseek(config)
    except FoldseekError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"foldseek: {binary.path} (via {binary.found_via}, version {binary.version})")
    check, _ = _structure_check(reference, structures_dir, config_dir, overrides)
    try:
        rows = build_three_di(check, out_dir, binary, config.threads)
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
        foldseek = read_foldseek_run(three_di_dir)
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
    parquet, markdown = write_structure_report(
        report, report_dir, low_plddt_threshold=threshold, foldseek=foldseek
    )
    typer.echo(f"report: {parquet}, {markdown}")


@data_app.command("publish")
def data_publish(
    push: Annotated[
        bool,
        typer.Option(
            "--push",
            help="Upload the staged folder to publish.repo_id. Without it, only stage locally.",
        ),
    ] = False,
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Stage the processed dataset for the Hugging Face Hub, and optionally push it.

    Staging writes ``publish.stage_dir``: one Parquet file per table (validated against its
    schema), the cleaning and structure reports, the dataset card, ``provenance.json`` and
    ``SHA256SUMS``. ``--push`` uploads that folder as one commit; it needs ``publish.repo_id``
    and a write token in ``HF_TOKEN`` (or a cached ``hf auth login``).
    """
    cfg = load_config(config_dir, overrides)
    data, publish = cfg.data, cfg.publish
    if push and not publish.repo_id:
        typer.echo("error: --push needs publish.repo_id=<user>/<name>", err=True)
        raise typer.Exit(code=1)
    release = load_release(data)
    processed, reports = Path(data.processed_dir), Path(data.reports_dir)
    manifest = Manifest.load(release.manifest_path)
    inputs = {
        name: asdict(entry)
        for key in ("reference", "dms_substitutions", "af2_structures")
        if (entry := manifest.get(release.manifest_key, name := release.file(key).filename))
    }
    provenance = {
        "proteingym_release": as_str_dict(
            OmegaConf.to_container(data.release, resolve=True), "data.release"
        ),
        "input_checksums": inputs,
        "foldseek_version": str(cfg.structure.foldseek.version),
        "structure_exceptions": release.structure_exceptions,
    }
    with connect(
        processed / "variants",
        reference_csv=release.raw_dir / release.file("reference").filename,
        structure_features_dir=processed / "structure_features",
        three_di_dir=processed / "three_di",
    ) as con:
        try:
            staged = stage_dataset(
                con,
                card=Path(publish.card),
                card_metadata=as_str_dict(
                    OmegaConf.to_container(publish.card_metadata, resolve=True),
                    "publish.card_metadata",
                ),
                reports_dir=reports,
                provenance=provenance,
                out_dir=Path(publish.stage_dir),
            )
        except PublishError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    for name, rows in staged.rows.items():
        typer.echo(f"  {name}: {rows:,} rows")
    typer.echo(f"staged -> {staged.out_dir}")
    if not push:
        typer.echo("not pushed (pass --push to upload)")
        return
    try:
        url = push_dataset(staged.out_dir, str(publish.repo_id), private=bool(publish.private))
    except PublishError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"pushed -> {url}")


@eval_app.command("precomputed")
def eval_precomputed(
    scores_dir: Annotated[
        Path,
        typer.Option(help="ProteinGym zero-shot score partitions.", exists=True, file_okay=False),
    ],
    reference: Reference,
    rows: Annotated[
        str, typer.Option(help="'all' (singles + multi-mutants, as the leaderboard) or 'single'.")
    ],
    out_dir: Annotated[Path, typer.Option(help="Results directory.")],
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Benchmark ProteinGym's precomputed scores for ``eval.precomputed_models``.

    Writes ``proteingym_precomputed_<rows>.parquet`` (per assay, long format) and
    ``..._summary.parquet`` (aggregates with bootstrap CIs) to ``out_dir``. Models are named
    ``proteingym:<score column>`` so they are never mistaken for MutaScope's own scores.
    """
    if rows not in ("all", "single"):
        typer.echo(f"error: --rows must be 'all' or 'single', got {rows!r}", err=True)
        raise typer.Exit(code=1)
    row_set: RowSet = "all" if rows == "all" else "single"
    cfg = load_config(config_dir, overrides).eval
    columns = [str(c) for c in cfg.precomputed_models]
    variants = load_precomputed_scores(scores_dir, columns, row_set)
    names = {c: PRECOMPUTED_PREFIX + c for c in columns}
    per_assay = assay_metrics_table(variants, names, float(cfg.top_percent))
    bootstrap = BootstrapParams(int(cfg.bootstrap.n_resamples), float(cfg.bootstrap.confidence))
    summary = summarise(
        per_assay, pd.read_csv(reference), bootstrap, seed_all(int(cfg.bootstrap.seed))
    )
    paths = write_results(per_assay, summary, out_dir, f"proteingym_precomputed_{rows}")
    typer.echo(
        f"{variants['assay_id'].nunique()} assays, {len(variants):,} rows, {len(columns)} models"
    )
    overall = summary[summary["level"] == "overall"]
    for record in overall.itertuples(index=False):
        typer.echo(
            f"  {record.model:<28} {record.metric:<10} {record.value:.4f} "
            f"[{record.ci_low:.4f}, {record.ci_high:.4f}] ({record.n_assays} assays)"
        )
    typer.echo(f"results: {paths[0]}, {paths[1]}")


@eval_app.command("compare-leaderboard")
def eval_compare_leaderboard(
    results: Annotated[
        Path,
        typer.Option(
            help="Per-assay results of `eval precomputed --rows all`.",
            exists=True,
            dir_okay=False,
        ),
    ],
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Print our aggregates and per-assay values next to the published leaderboard.

    One block per metric in ``eval.published``. Reads ``<results>`` and the matching
    ``_summary.parquet``. This is a report; the pass/fail gate is ``tests/test_reproduction.py``.
    """
    cfg = load_config(config_dir, overrides).eval
    names = {str(k): str(v) for k, v in cfg.precomputed_models.items()}
    summary = read_results(results.with_name(results.stem + "_summary.parquet"))
    per_assay = read_results(results).assign(
        model=lambda f: f["model"].str.removeprefix(PRECOMPUTED_PREFIX)
    )
    for metric in [str(m) for m in cfg.published]:
        files = cfg.published[metric]
        typer.echo(f"{metric}:")
        ours = summary[(summary["metric"] == metric) & summary["split"].isna()]
        table = compare_with_published(ours, published_summary(Path(files.summary), names))
        for record in table.itertuples(index=False):
            group = "" if pd.isna(record.group) else record.group
            typer.echo(
                f"  {record.model:<16} {record.level:<8} {group:<18} ours {record.ours:.4f}  "
                f"published {record.published:.3f}  delta {record.delta:+.4f}"
            )
        published = published_assay_level(Path(files.assay_level), names)
        mine = per_assay[per_assay["metric"] == metric]
        joined = published.merge(mine, on=["model", "assay_id"], suffixes=("_pub", ""))
        for model, rows in joined.groupby("model"):
            gap = (rows["value"] - rows["value_pub"]).abs()
            typer.echo(
                f"  {model}: {len(rows)} assays, max |ours - published| per assay {gap.max():.5f}"
            )
