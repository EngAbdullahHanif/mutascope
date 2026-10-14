"""Typer entry point: ``mutascope <group> <cmd>``.

Every pipeline step is a command here; Nextflow only ever calls these commands.
"""

import csv
import re
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Annotated

import httpx
import numpy as np
import pandas as pd
import typer
from omegaconf import DictConfig, OmegaConf

from mutascope import __version__
from mutascope.data.clean import clean_dataset
from mutascope.data.db import connect
from mutascope.data.download import DownloadError, fetch_release
from mutascope.data.folds import FoldJoinError, build_folds, load_cv_table
from mutascope.data.manifest import Manifest
from mutascope.data.release import load_release
from mutascope.data.report import write_report
from mutascope.data.schemas import CV_SCHEMES
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
from mutascope.eval.aggregate import AssayGroups, aggregate
from mutascope.eval.benchmark import (
    BootstrapParams,
    assay_metrics_table,
    read_results,
    summarise,
    summary_metrics,
    write_results,
)
from mutascope.eval.compare import paired_bootstrap_diff, shared_assays, summarise_shared
from mutascope.eval.gate import GateThresholds, join_scores, model_gate
from mutascope.eval.landscapes import (
    LandscapeTarget,
    fill_landscapes,
    format_duration,
    landscape_coverage,
    landscape_targets,
)
from mutascope.eval.metrics import SUPERVISED_METRICS
from mutascope.eval.proteingym import (
    PRECOMPUTED_PREFIX,
    RowSet,
    compare_with_published,
    load_precomputed_scores,
    published_assay_level,
    published_summary,
)
from mutascope.eval.region import (
    ScoringRegion,
    ScoringRegionError,
    scoring_regions,
    structure_regions,
)
from mutascope.eval.schemas import (
    AgreementSchema,
    CanarySchema,
    ComparisonSchema,
    LandscapeCoverageSchema,
    LandscapeRunSchema,
    ScoringRunSchema,
    SelectedHyperparameterSchema,
    SummarySchema,
    SupervisedLeaderboardSchema,
    SupervisedMetricSchema,
)
from mutascope.eval.supervised import (
    hyperparameters_path,
    leaderboard_table,
    pooled_assay_metrics,
    results_path,
    supervised_label,
)
from mutascope.eval.zero_shot import (
    load_scores,
    run_label,
    score_assays,
    with_labels,
    write_scores,
)
from mutascope.export.hub import PublishError, push_dataset, stage_dataset
from mutascope.features.base import FEATURE_SETS, KEY_COLUMNS, build_features
from mutascope.features.embeddings import fill_embeddings, hidden_size
from mutascope.features.matrix import FeatureMatrix
from mutascope.features.schemas import EmbeddingRunSchema, FeatureStoreReportSchema
from mutascope.features.sets import (
    FeatureSet,
    FeatureSetError,
    assays_of,
    assemble,
    feature_sets,
    structured_assays,
    width,
)
from mutascope.features.store import (
    FeatureStoreError,
    ModelSource,
    build_store,
    measured_keys,
    read_structure_features,
)
from mutascope.models.canary import canary_record, permutation_canary
from mutascope.models.cv import (
    CrossValidationError,
    DesignBuilder,
    ModelFactory,
    cv_metrics_table,
)
from mutascope.models.registry import model_factory
from mutascope.models.ridge import RidgeError
from mutascope.scoring.base import build_scorer
from mutascope.scoring.cache import CacheError, LogProbCache
from mutascope.scoring.ensemble import zscore_mean
from mutascope.scoring.masked_lm import MaskedLMScorer
from mutascope.scoring.struct_tokens import AssayStructure, StructTokenError, load_struct_tokens
from mutascope.utils.config import as_str_dict, load_config
from mutascope.utils.io import write_table
from mutascope.utils.logging import configure_logging
from mutascope.utils.seeding import seed_all
from mutascope.utils.tracking import TrackedRun, TrackingError, TrackingSettings, log_run

app = typer.Typer(
    name="mutascope",
    help="Functional-effect landscapes for single amino-acid substitutions.",
    no_args_is_help=True,
)
data_app = typer.Typer(help="Acquire and validate ProteinGym data.", no_args_is_help=True)
app.add_typer(data_app, name="data")
eval_app = typer.Typer(help="Benchmark predictions against DMS assays.", no_args_is_help=True)
app.add_typer(eval_app, name="eval")
score_app = typer.Typer(help="Score variants with zero-shot models.", no_args_is_help=True)
app.add_typer(score_app, name="score")
train_app = typer.Typer(help="Cross-validate supervised models.", no_args_is_help=True)
app.add_typer(train_app, name="train")
features_app = typer.Typer(help="Build per-variant features.", no_args_is_help=True)
app.add_typer(features_app, name="features")

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


def _track(
    cfg: DictConfig,
    step: str,
    label: str,
    *,
    fast: bool,
    metrics: dict[str, float],
    artifacts: list[Path],
    tags: dict[str, str] | None = None,
    members: dict[str, DictConfig] | None = None,
) -> None:
    """Log one model's scoring or evaluation run: the model config and the method as params.

    An ensemble (``members``, from :func:`_members`) has no checkpoint of its own, so the run
    records the Hub id and pinned revision of every member instead, and its method is the
    combination rule.
    """
    model = as_str_dict(OmegaConf.to_container(cfg.model, resolve=True), "model")
    params: dict[str, object] = {f"model.{key}": value for key, value in model.items()}
    params |= {"method": "wt" if fast else "masked", "scoring.device": str(cfg.scoring.device)}
    if members:
        params["method"] = ENSEMBLE_METHOD
        for member, member_cfg in members.items():
            params[f"member.{member}.hf_id"] = str(member_cfg.hf_id)
            params[f"member.{member}.revision"] = str(member_cfg.revision)
    _log_run(cfg, step, label, params, metrics, artifacts, {"model": label} | (tags or {}))


def _log_run(
    cfg: DictConfig,
    step: str,
    subject: str,
    params: dict[str, object],
    metrics: dict[str, float],
    artifacts: list[Path],
    tags: dict[str, str],
    *,
    experiment: str | None = None,
) -> None:
    """Log one command's run to the local MLflow store (``tracking`` config; ADR 0014).

    Called after the command has written its outputs, so a tracking failure loses no result;
    it still exits 1, because an untracked run must not look like a tracked one.
    ``experiment`` replaces ``tracking.experiment`` (supervised runs have their own).
    """
    run = TrackedRun(
        name=f"{step} {subject}",
        params=params
        | {
            "data.release.version": str(cfg.data.release.version),
            "data.release.git_commit": str(cfg.data.release.git_commit),
        },
        metrics=metrics,
        tags={"step": step} | tags,
        config=as_str_dict(OmegaConf.to_container(cfg, resolve=True), "config"),
        artifacts=artifacts,
    )
    settings = TrackingSettings.from_config(
        as_str_dict(OmegaConf.to_container(cfg.tracking, resolve=True), "tracking")
    )
    if experiment is not None:
        settings = replace(settings, experiment=experiment)
    try:
        run_id = log_run(settings, run)
    except TrackingError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if run_id is not None:
        typer.echo(f"tracked: run {run_id} in {settings.directory}")


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


@data_app.command("folds")
def data_folds(
    variants_dir: Annotated[
        Path, typer.Option(help="Processed variant partitions.", exists=True, file_okay=False)
    ],
    folds_dir: Annotated[
        Path,
        typer.Option(
            help="ProteinGym's cv_folds_singles_substitutions directory.",
            exists=True,
            file_okay=False,
        ),
    ],
    out_dir: Annotated[Path, typer.Option(help="Output dir for assay_id=<id>/ partitions.")],
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Join ProteinGym's CV folds to the processed variants on ``(assay_id, mutant)``.

    Every variant must get exactly one fold per scheme (random, modulo, contiguous), and the
    fold files must hold no other mutant; any mismatch fails with every offending assay and
    nothing is written. Only keys and folds are written: labels stay in the variants table
    (ADR 0005, ADR 0019).
    """
    _accept_overrides(config_dir, overrides)
    try:
        report = build_folds(variants_dir, folds_dir, out_dir)
    except FoldJoinError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"{len(report)} assays, {int(report['variants'].sum()):,} variants -> {out_dir}")
    for scheme in CV_SCHEMES:
        counts = report[f"folds_{scheme}"].value_counts().sort_index()
        summary = ", ".join(f"{n} assays with {k} folds" for k, n in counts.items())
        typer.echo(f"  {scheme}: {summary}")


StoreDir = Annotated[
    Path | None,
    typer.Option(
        help="Feature store (`features build`); needed for any feature set but `none`.",
        exists=True,
        file_okay=False,
    ),
]
TRAIN_ERRORS = (KeyError, FoldJoinError, CrossValidationError, FeatureSetError, FeatureStoreError)


def _design(cfg: DictConfig, store_dir: Path | None) -> tuple[DesignBuilder, list[str] | None]:
    """Feature builder for ``train.feature_set`` and the assays it covers (None: every assay).

    A key-only set (``none``) is computed from the keys. Any other name is a feature-store set
    in ``features.sets``, assembled one assay at a time (ADR 0020); a structure set covers
    only the assays the store has structure features for.
    """
    name = str(cfg.train.feature_set)
    if name in FEATURE_SETS:
        return (lambda assay_id, keys: build_features(name, keys)), None
    sets = feature_sets(
        as_str_dict(OmegaConf.to_container(cfg.features.sets, resolve=True), "features.sets")
    )
    if name not in sets:
        raise KeyError(f"unknown feature set {name!r}; known: {sorted([*FEATURE_SETS, *sets])}")
    if store_dir is None:
        raise FeatureSetError(f"feature set {name!r} is read from the store: pass --store-dir")
    feature_set, store = sets[name], store_dir
    covered = sorted(structured_assays(store)) if feature_set.structure else None

    def design(assay_id: str, keys: pd.DataFrame) -> FeatureMatrix:
        return assemble(store, feature_set, assay_id, keys).feature_matrix()

    return design, covered


def _model(cfg: DictConfig) -> ModelFactory:
    """``train.model`` configured with ``train.params.<model>`` (if any)."""
    name = str(cfg.train.model)
    params = cfg.train.params.get(name)
    config = (
        {} if params is None else as_str_dict(OmegaConf.to_container(params, resolve=True), name)
    )
    return model_factory(name, config)


@train_app.command("cv")
def train_cv(
    variants_dir: Annotated[
        Path, typer.Option(help="Processed variant partitions.", exists=True, file_okay=False)
    ],
    folds_dir: Annotated[
        Path,
        typer.Option(help="Fold partitions (`data folds`).", exists=True, file_okay=False),
    ],
    reference: Reference,
    out_dir: Annotated[Path, typer.Option(help="Results directory.")],
    store_dir: StoreDir = None,
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Cross-validate ``train.model`` on ``train.feature_set`` in every assay (ADR 0019, 0021).

    Per assay and scheme, each fold is predicted by a model trained on the others. Features
    are assembled from the store one assay at a time. Writes ``cv_<model>_<feature_set>
    .parquet`` to ``out_dir``: Spearman and MSE over the pooled out-of-fold predictions
    (``fold`` null, ProteinGym's number) and per fold; for a model that tunes itself, also the
    hyperparameters its inner CV chose per fold. Prints the ProteinGym-style aggregate of the
    pooled values per scheme, and logs the run to MLflow in ``train.experiment``.
    """
    cfg = load_config(config_dir, overrides)
    model, feature_set = str(cfg.train.model), str(cfg.train.feature_set)
    label = supervised_label(model, feature_set)
    schemes = [str(s) for s in cfg.train.schemes]
    unknown = sorted(set(schemes) - set(CV_SCHEMES))
    if unknown:
        typer.echo(f"error: train.schemes: unknown {unknown}; use {list(CV_SCHEMES)}", err=True)
        raise typer.Exit(code=1)
    seed_all(int(cfg.train.seed))
    started = time.monotonic()
    try:
        make_model = _model(cfg)
        design, covered = _design(cfg, store_dir)
        data = load_cv_table(variants_dir, folds_dir, covered)
        result = cv_metrics_table(
            data, design, make_model, model=model, feature_set=feature_set, schemes=schemes
        )
    except TRAIN_ERRORS as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    wall = time.monotonic() - started
    per_assay = result.metrics
    paths = [write_table(per_assay, SupervisedMetricSchema, results_path(out_dir, label))]
    if not result.hyperparameters.empty:
        paths.append(
            write_table(
                result.hyperparameters,
                SelectedHyperparameterSchema,
                hyperparameters_path(out_dir, label),
            )
        )
    assays = sorted(set(per_assay["assay_id"]))
    typer.echo(
        f"{model} on {feature_set}: {len(assays)} assays, {len(data):,} variants, {wall:.0f} s"
    )
    groups = AssayGroups.from_reference(pd.read_csv(reference), assays)
    pooled = per_assay[per_assay["fold"].isna()]
    metrics: dict[str, float] = {"assays": float(len(assays)), "variants": float(len(data))}
    for scheme in schemes:
        for metric in SUPERVISED_METRICS:
            values = (
                pooled[(pooled["scheme"] == scheme) & (pooled["metric"] == metric)]
                .set_index("assay_id")
                .loc[assays, "value"]
                .to_numpy(np.float64)
            )
            n_nan = int(np.isnan(values).sum())
            overall = aggregate(values, groups).overall
            metrics[f"undefined.{scheme}.{metric}"] = float(n_nan)
            metrics[f"{metric}.{scheme}"] = overall
            typer.echo(
                f"  {scheme:<10} {metric:<8} {overall:.4f} ({len(assays) - n_nan} assays defined)"
            )
    _echo_edges(result.hyperparameters, cfg)
    for path in paths:
        typer.echo(f"results: {path}")
    _log_run(
        cfg,
        "cross-validate",
        f"{model} {feature_set}",
        {
            "train.model": model,
            "train.feature_set": feature_set,
            "train.schemes": schemes,
            "train.params": as_str_dict(
                OmegaConf.to_container(cfg.train.params, resolve=True), "train.params"
            ).get(model, {}),
        },
        metrics,
        paths,
        {"model": model, "feature_set": feature_set, "kind": "supervised"},
        experiment=str(cfg.train.experiment),
    )


def _echo_edges(chosen: pd.DataFrame, cfg: DictConfig) -> None:
    """Print how often the inner CV chose the smallest or largest penalty on the grid."""
    alphas = chosen[chosen["name"] == "alpha"]
    if alphas.empty:
        return
    log10 = np.log10(alphas["value"].to_numpy(np.float64))
    grid = cfg.train.params[str(cfg.train.model)].alphas
    low, high = float(grid.log10_min), float(grid.log10_max)
    typer.echo(
        f"  alpha: median 10^{np.median(log10):.1f}; at the grid's low end in "
        f"{int(np.isclose(log10, low).sum())} of {log10.size} fits, high end in "
        f"{int(np.isclose(log10, high).sum())}"
    )


@train_app.command("canary")
def train_canary(
    variants_dir: Annotated[
        Path, typer.Option(help="Processed variant partitions.", exists=True, file_okay=False)
    ],
    folds_dir: Annotated[
        Path,
        typer.Option(help="Fold partitions (`data folds`).", exists=True, file_okay=False),
    ],
    out_dir: Annotated[Path, typer.Option(help="Results directory.")],
    store_dir: StoreDir = None,
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Label-permutation canary of ``train.model`` on real assays (ADR 0019, 0021).

    On each assay of ``train.canary.assays`` and each scheme, the CV loop runs once with true
    labels and ``n_permutations`` times with shuffled training labels. Passes when the mean
    permuted pooled Spearman is within ``max_standard_errors`` standard errors of 0. A case
    whose true-label Spearman is below ``min_observed`` is marked uninformative: a model that
    learns nothing there passes without proving anything. Writes ``canary_<label>.parquet``;
    exits 1 if any case fails.
    """
    cfg = load_config(config_dir, overrides)
    model, feature_set = str(cfg.train.model), str(cfg.train.feature_set)
    canary = cfg.train.canary
    label = supervised_label(model, feature_set)
    seed_all(int(cfg.train.seed))
    rng = np.random.default_rng(int(cfg.train.seed))
    assays = [str(a) for a in canary.assays]
    records: list[dict[str, object]] = []
    try:
        make_model = _model(cfg)
        design, covered = _design(cfg, store_dir)
        outside = sorted(set(assays) - set(covered or assays))
        if outside:
            raise FeatureSetError(f"{feature_set} does not cover canary assays {outside}")
        data = load_cv_table(variants_dir, folds_dir, assays)
        for assay_id in assays:
            assay = data[data["assay_id"] == assay_id].reset_index(drop=True)
            x = design(assay_id, assay[list(KEY_COLUMNS)])
            for scheme in [str(s) for s in cfg.train.schemes]:
                result = permutation_canary(
                    x,
                    assay["DMS_score"].to_numpy(np.float64),
                    assay[f"fold_{scheme}"].to_numpy(np.int64),
                    make_model,
                    n_permutations=int(canary.n_permutations),
                    rng=rng,
                )
                records.append(
                    {"model": model, "feature_set": feature_set, "assay_id": assay_id}
                    | canary_record(
                        result,
                        scheme=scheme,
                        max_standard_errors=float(canary.max_standard_errors),
                        min_observed=float(canary.min_observed),
                    )
                )
    except (*TRAIN_ERRORS, RidgeError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    table = pd.DataFrame.from_records(records)
    path = write_table(table, CanarySchema, out_dir / f"canary_{label}.parquet")
    typer.echo(f"canary {label}: {int(canary.n_permutations)} permutations per case")
    for row in table.itertuples(index=False):
        verdict = "pass" if row.passed else "FAIL"
        note = "" if row.informative else "  (uninformative: model learns little here)"
        typer.echo(
            f"  {row.assay_id:<34} {row.scheme:<10} observed {row.observed:+.3f}  permuted "
            f"{row.permuted_mean:+.3f} ± {row.standard_error:.3f} (z {row.z:+.2f})  {verdict}{note}"
        )
    typer.echo(f"results: {path}")
    failed = int((~table["passed"]).sum())
    _log_run(
        cfg,
        "canary",
        f"{model} {feature_set}",
        {
            "train.model": model,
            "train.feature_set": feature_set,
            "canary.assays": ", ".join(assays),
            "canary.n_permutations": int(canary.n_permutations),
            "canary.max_standard_errors": float(canary.max_standard_errors),
        },
        {
            "cases": float(len(table)),
            "failed": float(failed),
            "uninformative": float((~table["informative"]).sum()),
            "max_abs_z": float(table["z"].abs().max()),
        },
        [path],
        {"model": model, "feature_set": feature_set, "kind": "supervised"},
        experiment=str(cfg.train.experiment),
    )
    if failed:
        typer.echo(f"error: {failed} canary case(s) failed", err=True)
        raise typer.Exit(code=1)


def _pooled_runs(results_dir: Path, labels: list[str]) -> dict[str, pd.DataFrame]:
    """Pooled values of each run, read from ``cv_<label>.parquet``; exits 1 if one is missing."""
    missing = [label for label in labels if not results_path(results_dir, label).exists()]
    if missing:
        typer.echo(f"error: no results for {missing} in {results_dir}", err=True)
        raise typer.Exit(code=1)
    return {
        label: pooled_assay_metrics(read_results(results_path(results_dir, label)))
        for label in labels
    }


@train_app.command("compare")
def train_compare(
    results_dir: Annotated[
        Path,
        typer.Option(help="Directory of cv_<label>.parquet.", exists=True, file_okay=False),
    ],
    reference: Reference,
    out_dir: Annotated[Path, typer.Option(help="Where summary/comparisons Parquet go.")],
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Bootstrap summaries and paired comparisons of pooled Spearman (ADR 0014, 0021).

    For every run in ``train.comparisons``: the ProteinGym aggregate with a bootstrap CI over
    its own assays, per scheme (``supervised_summary.parquet``). For every pair ``[A, B]``:
    the paired bootstrap of ``A - B`` on the same resampled assays, per scheme
    (``supervised_comparisons.parquet``). A structure set lacks
    ``data.structure_exceptions``; a pair with one is compared on the shared assays.
    """
    cfg = load_config(config_dir, overrides)
    pairs = [(str(a), str(b)) for a, b in cfg.train.comparisons]
    labels = sorted({label for pair in pairs for label in pair})
    if not pairs:
        typer.echo("error: train.comparisons is empty", err=True)
        raise typer.Exit(code=1)
    pooled = _pooled_runs(results_dir, labels)
    table = pd.read_csv(reference)
    bootstrap = BootstrapParams(
        int(cfg.eval.bootstrap.n_resamples), float(cfg.eval.bootstrap.confidence)
    )
    seed = int(cfg.eval.bootstrap.seed)
    seed_all(seed)
    exceptions = cfg.data.structure_exceptions
    no_structure = [] if exceptions is None else [str(a) for a in exceptions]
    try:
        summary = pd.concat(
            [
                summarise(pooled[label], table, bootstrap, np.random.default_rng(seed))
                for label in labels
            ],
            ignore_index=True,
        )
        frames = [
            paired_bootstrap_diff(
                pooled[a],
                pooled[b],
                table,
                bootstrap,
                np.random.default_rng(seed),
                may_be_absent=no_structure,
            )
            for a, b in pairs
        ]
    except (ValueError, KeyError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    summary_path = write_table(summary, SummarySchema, out_dir / "supervised_summary.parquet")
    comparisons = pd.concat(frames, ignore_index=True)
    comparison_path = write_table(
        comparisons, ComparisonSchema, out_dir / "supervised_comparisons.parquet"
    )
    metrics: dict[str, float] = {}
    overall = summary[(summary["level"] == "overall") & (summary["metric"] == "spearman")]
    typer.echo(f"pooled Spearman, {bootstrap.n_resamples:,} resamples:")
    for row in overall.itertuples(index=False):
        typer.echo(
            f"  {row.model:<34} {row.split:<10} {row.value:.4f} "
            f"[{row.ci_low:.4f}, {row.ci_high:.4f}] ({row.n_assays} assays)"
        )
    for model, split, value in zip(
        overall["model"], overall["split"], overall["value"].to_numpy(np.float64), strict=True
    ):
        metrics[f"spearman/{model}/{split}"] = float(value)
    typer.echo("paired, A - B:")
    headline = comparisons[
        (comparisons["level"] == "overall") & (comparisons["metric"] == "spearman")
    ]
    for row in headline.itertuples(index=False):
        typer.echo(
            f"  {row.model_a} - {row.model_b} ({row.split}): {row.diff:+.4f} "
            f"[{row.ci_low:+.4f}, {row.ci_high:+.4f}], A better in {row.share_a_better:.1%} "
            f"({row.n_assays} assays)"
        )
    for a, b, split, diff, low, high in zip(
        headline["model_a"],
        headline["model_b"],
        headline["split"],
        headline["diff"].to_numpy(np.float64),
        headline["ci_low"].to_numpy(np.float64),
        headline["ci_high"].to_numpy(np.float64),
        strict=True,
    ):
        key = f"spearman_diff/{a}-vs-{b}/{split}"
        metrics |= {key: float(diff), f"{key}/ci_low": float(low), f"{key}/ci_high": float(high)}
    typer.echo(f"summary: {summary_path}\ncomparisons: {comparison_path}")
    _log_run(
        cfg,
        "compare",
        "supervised",
        {
            "pairs": ", ".join(f"{a} - {b}" for a, b in pairs),
            "bootstrap.n_resamples": bootstrap.n_resamples,
            "bootstrap.confidence": bootstrap.confidence,
            "bootstrap.seed": seed,
        },
        metrics,
        [summary_path, comparison_path],
        {"kind": "supervised"},
        experiment=str(cfg.train.experiment),
    )


@train_app.command("leaderboard")
def train_leaderboard(
    results_dir: Annotated[
        Path,
        typer.Option(help="Directory of cv_<label>.parquet.", exists=True, file_okay=False),
    ],
    reference: Reference,
    out: Annotated[Path, typer.Option(help="Output Parquet file.")],
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Our pooled Spearman next to ProteinGym's supervised baselines of the same type.

    A sanity range, not a gate (ADR 0021): ProteinGym's one-hot baselines use other zero-shot
    models and another learner. Reads ``train.leaderboard``; writes one table and logs a run.
    """
    cfg = load_config(config_dir, overrides)
    board = cfg.train.leaderboard
    baselines = {str(label): [str(n) for n in names] for label, names in board.baselines.items()}
    pooled = _pooled_runs(results_dir, sorted(baselines))
    try:
        assay_level = {
            scheme: pd.read_csv(Path(str(board.assay_level[scheme]))) for scheme in CV_SCHEMES
        }
        table = leaderboard_table(
            pooled,
            baselines,
            pd.read_csv(Path(str(board.summary))),
            assay_level,
            pd.read_csv(reference),
        )
    except (FileNotFoundError, KeyError) as exc:
        typer.echo(f"error: {exc} (run `mutascope data download`)", err=True)
        raise typer.Exit(code=1) from exc
    path = write_table(table, SupervisedLeaderboardSchema, out)
    typer.echo("pooled Spearman: ours vs ProteinGym's published supervised baselines")
    metrics: dict[str, float] = {}
    for row in table.itertuples(index=False):
        typer.echo(
            f"  {row.run:<34} {row.scheme:<10} ours {row.ours:.3f}  {row.published_model:<36} "
            f"{row.published:.3f}  diff {row.diff:+.3f}  per-assay rho {row.assay_spearman:.2f}"
        )
    for run, name, scheme, diff in zip(
        table["run"],
        table["published_model"],
        table["scheme"],
        table["diff"].to_numpy(np.float64),
        strict=True,
    ):
        # MLflow metric names allow letters, digits, "_-. /" only ("ESM-1v + One-Hot ...").
        safe = re.sub(r"[^\w\-. /]", "_", str(name))
        metrics[f"diff/{run}/{safe}/{scheme}"] = float(diff)
    typer.echo(f"results: {path}")
    _log_run(
        cfg,
        "leaderboard",
        "supervised",
        {"runs": ", ".join(baselines)},
        metrics,
        [path],
        {"kind": "supervised"},
        experiment=str(cfg.train.experiment),
    )


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


ENSEMBLE_METHOD = "zscore_mean"
"""The ``method`` param of an ensemble's tracked runs (:mod:`mutascope.scoring.ensemble`)."""


def _members(cfg: DictConfig, config_dir: Path) -> dict[str, DictConfig]:
    """Score-set label -> model config of every member of an ensemble; empty for one model.

    A member is ``{model, method}`` in ``model.members``: its config is
    ``conf/model/<model>.yaml`` and its label comes from
    :func:`~mutascope.eval.zero_shot.run_label`, like every other score set's.

    Raises:
        ValueError: A member's method is not ``masked`` or ``wt``, or a member is listed twice.
    """
    members: dict[str, DictConfig] = {}
    model = as_str_dict(OmegaConf.to_container(cfg.model, resolve=True), "model")
    listed: list[object] = model.get("members") or []
    for member in listed:
        entry = as_str_dict(member, "model.members[]")
        name, method = str(entry["model"]), str(entry["method"])
        if method not in ("masked", "wt"):
            raise ValueError(f"member {name}: method must be masked or wt, got {method!r}")
        label = run_label(name, fast=method == "wt")
        if label in members:
            raise ValueError(f"member {label} is listed twice")
        members[label] = load_config(config_dir, [f"model={name}"]).model
    return members


def _model_assays(cfg: DictConfig, reference: pd.DataFrame, config_dir: Path) -> list[str]:
    """The assays the configured model is scored and evaluated on.

    Every assay of the reference for a sequence-only model. A structure-aware model (its
    config has ``plddt_threshold``) leaves out ``data.structure_exceptions``, the assays
    without a usable structure (ADR 0003). An ensemble exists where all its members do, so it
    is structure-aware if any member is. Every command derives the set here, so scoring, the
    model gate and the benchmark cannot disagree on it, and none of them accepts any other set.
    """
    assays = reference["DMS_id"].astype(str).tolist()
    exceptions = cfg.data.structure_exceptions  # null on a subset (the test profile)
    models = list(_members(cfg, config_dir).values()) or [cfg.model]
    structure_aware = any(model.get("plddt_threshold") is not None for model in models)
    if not structure_aware or exceptions is None:
        return assays
    excluded = {str(a) for a in exceptions}
    return [a for a in assays if a not in excluded]


def _structure_inputs(
    cfg: DictConfig,
    reference: pd.DataFrame,
    three_di_dir: Path | None,
    structure_features_dir: Path | None,
    config_dir: Path,
) -> tuple[dict[str, AssayStructure], dict[str, ScoringRegion]]:
    """Structure tokens and scoring regions of a structure-aware model's assays.

    Raises:
        ValueError: A directory is missing, the tables are inconsistent, or the assays with a
            structure are not exactly :func:`_model_assays`.
    """
    if three_di_dir is None or structure_features_dir is None:
        raise ValueError(
            f"{cfg.model.name} is structure-aware: pass --three-di-dir and --structure-features-dir"
        )
    targets = dict(
        zip(reference["DMS_id"].astype(str), reference["target_seq"].astype(str), strict=True)
    )
    structures = load_struct_tokens(
        three_di_dir, structure_features_dir, targets, float(cfg.model.plddt_threshold)
    )
    expected = set(_model_assays(cfg, reference, config_dir))
    if set(structures) != expected:
        raise ValueError(
            f"assays with a structure differ from the reference minus "
            f"data.structure_exceptions: no structure for {sorted(expected - set(structures))}, "
            f"unexpected structure for {sorted(set(structures) - expected)}"
        )
    return structures, structure_regions(reference, structures)


ThreeDiDir = Annotated[
    Path | None,
    typer.Option(help="Processed 3Di partitions (structure-aware models).", file_okay=False),
]
StructureFeaturesDir = Annotated[
    Path | None,
    typer.Option(
        help="Processed structure-feature partitions (structure-aware models).", file_okay=False
    ),
]


def _benchmark_inputs(
    cfg: DictConfig,
    reference: pd.DataFrame,
    three_di_dir: Path | None,
    structure_features_dir: Path | None,
    config_dir: Path,
) -> tuple[dict[str, AssayStructure] | None, dict[str, ScoringRegion]]:
    """The configured model's structure tokens (None if sequence-only) and benchmark regions.

    A sequence-only model has a region for every assay: the MSA region where it is not the
    full target (ADR 0012). A structure-aware model has one for the assays with a structure:
    the residues the structure models (ADR 0015).

    Raises:
        ValueError: The MSA-region assays differ from the config, or see
            :func:`_structure_inputs`.
    """
    if cfg.model.get("plddt_threshold") is None:
        # null on a subset of assays (the pipeline's test profile), like structure_exceptions.
        configured = cfg.eval.scoring_region.msa_region_assays
        expected = [] if configured is None else [str(a) for a in configured]
        return None, scoring_regions(reference, "benchmark", expected)
    return _structure_inputs(cfg, reference, three_di_dir, structure_features_dir, config_dir)


@score_app.command("zero-shot")
def score_zero_shot(
    reference: Reference,
    variants_dir: Annotated[
        Path, typer.Option(help="Processed variant partitions.", exists=True, file_okay=False)
    ],
    out_dir: Annotated[
        Path, typer.Option(help="Root for <label>/assay_id=<id>/ partitions (label: see below).")
    ],
    report_dir: Annotated[Path, typer.Option(help="Directory for zero_shot_run_<label>.parquet.")],
    fast: Annotated[
        bool, typer.Option("--fast", help="Wild-type marginal instead of the masked marginal.")
    ] = False,
    three_di_dir: ThreeDiDir = None,
    structure_features_dir: StructureFeaturesDir = None,
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Score every assay's single mutants with the configured model (``model=<name>``).

    Only the positions an assay measures are scored, on its benchmark scoring region. The
    run is resumable: every finished sequence is in ``scoring.cache_dir``, so a rerun after a
    crash scores only what is missing and reports ``positions_computed = 0`` for the rest.

    A structure-aware model (SaProt) also reads the processed 3Di and structure-feature
    tables. It scores only the assays with a structure, on the residues the structure models,
    and the 3Di letter of a residue below ``model.plddt_threshold`` is replaced by ``#``.

    The label of a score set is the model name, plus ``_wt`` with ``--fast``
    (:func:`~mutascope.eval.zero_shot.run_label`): the two methods of one model go to
    different directories and report files. The run is logged to MLflow (``tracking``).
    """
    cfg = load_config(config_dir, overrides)
    if cfg.model.get("members"):
        typer.echo(f"error: {cfg.model.name} is an ensemble: use `score ensemble`", err=True)
        raise typer.Exit(code=1)
    table = pd.read_csv(reference)
    try:
        structures, regions = _benchmark_inputs(
            cfg, table, three_di_dir, structure_features_dir, config_dir
        )
    except (ScoringRegionError, StructTokenError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    cache_dir = Path(cfg.scoring.cache_dir)
    scorer = build_scorer(
        as_str_dict(OmegaConf.to_container(cfg.model, resolve=True), "model"),
        device=str(cfg.scoring.device),
        cache_dir=cache_dir,
    )
    started = time.monotonic()
    label = run_label(scorer.model_id, fast=fast)
    report = score_assays(
        scorer, table, variants_dir, regions, out_dir / label, fast=fast, structures=structures
    )
    wall = time.monotonic() - started
    path = write_table(report, ScoringRunSchema, report_dir / f"zero_shot_run_{label}.parquet")
    computed = report[report["positions_computed"] > 0]
    typer.echo(
        f"{label}: {len(report)} assays, {int(report['variants'].sum()):,} variants "
        f"-> {out_dir / label}"
    )
    if len(report) < len(table):
        typer.echo(f"not scored (no structure): {len(table) - len(report)} assays")
    typer.echo(
        f"this run: {wall:.0f} s; {int(report['positions_computed'].sum()):,} positions scored "
        f"in {len(computed)} assays, {len(report) - len(computed)} assays read from the cache"
    )
    logged = [
        c
        for c in LogProbCache(cache_dir).computations()
        if c["model_id"] == str(cfg.model.hf_id)
        and c["revision"] == str(cfg.model.revision)
        and c["method"] == ("wt" if fast else "masked")
    ]
    model_seconds = float(sum(c["seconds"] for c in logged))
    model_positions = int(sum(c["positions"] for c in logged))
    typer.echo(
        f"all runs (cache log): {model_seconds:.0f} s of model time, {model_positions:,} positions"
    )
    typer.echo(f"report: {path}")
    _track(
        cfg,
        "score",
        label,
        fast=fast,
        metrics={
            "assays": float(len(report)),
            "variants": float(report["variants"].sum()),
            "positions": float(report["positions"].sum()),
            # This invocation only: 0 positions when everything came from the cache.
            "positions_computed": float(report["positions_computed"].sum()),
            "wall_seconds": wall,
            # Every invocation that filled this cache, interrupted ones included (ADR 0013).
            "model_seconds_all_runs": model_seconds,
            "positions_all_runs": float(model_positions),
        },
        artifacts=[path],
    )


def _landscape_targets(
    cfg: DictConfig,
    reference: pd.DataFrame,
    three_di_dir: Path | None,
    structure_features_dir: Path | None,
    config_dir: Path,
) -> list[LandscapeTarget]:
    """Every landscape the configured model owes: see :mod:`mutascope.eval.landscapes`."""
    structures, regions = _benchmark_inputs(
        cfg, reference, three_di_dir, structure_features_dir, config_dir
    )
    return landscape_targets(reference, regions, structures)


@score_app.command("landscapes")
def score_landscapes(
    reference: Reference,
    report_dir: Annotated[Path, typer.Option(help="Directory for landscape_run_<model>.parquet.")],
    three_di_dir: ThreeDiDir = None,
    structure_features_dir: StructureFeaturesDir = None,
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Masked marginals at every residue of every assay, with the configured model.

    Fills the cache entries of ``mutascope score zero-shot``: only the residues an entry lacks
    are scored, so the scores the benchmark used stay as they are. An assay whose benchmark
    region is part of its target (MSA region, or the residues a structure models) gets a
    second landscape of the full target, a separate entry with the region label ``full``.

    The cache (``scoring.cache_dir``) is written every ``scoring.landscapes.checkpoint_tokens``
    of model work, also inside a long protein, so the run can be killed and started again at
    any time. Progress and the estimated time remaining are logged after every write. The run
    is logged to MLflow.
    """
    cfg = load_config(config_dir, overrides)
    if cfg.model.get("members"):
        typer.echo(f"error: {cfg.model.name} is an ensemble: it has no landscape", err=True)
        raise typer.Exit(code=1)
    table = pd.read_csv(reference)
    try:
        targets = _landscape_targets(cfg, table, three_di_dir, structure_features_dir, config_dir)
    except (ScoringRegionError, StructTokenError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    cache_dir = Path(cfg.scoring.cache_dir)
    scorer = build_scorer(
        as_str_dict(OmegaConf.to_container(cfg.model, resolve=True), "model"),
        device=str(cfg.scoring.device),
        cache_dir=cache_dir,
    )
    window = cfg.model.window_tokens
    started = time.monotonic()
    report = fill_landscapes(
        scorer,
        targets,
        window_tokens=None if window is None else int(window),
        checkpoint_tokens=int(cfg.scoring.landscapes.checkpoint_tokens),
    )
    wall = time.monotonic() - started
    name = scorer.model_id
    path = write_table(report, LandscapeRunSchema, report_dir / f"landscape_run_{name}.parquet")
    extra = report[~report["benchmark"]]
    computed = int(report["positions_computed"].sum())
    typer.echo(
        f"{name}: {len(report)} complete landscapes of {report['assay_id'].nunique()} assays "
        f"({len(extra)} full-target landscapes beside a partial benchmark region)"
    )
    if report["assay_id"].nunique() < len(table):
        missing = len(table) - report["assay_id"].nunique()
        typer.echo(f"no landscape (no structure): {missing} assays")
    typer.echo(f"this run: {format_duration(wall)}; {computed:,} positions scored")
    typer.echo(f"report: {path}")
    _track(
        cfg,
        "landscapes",
        name,
        fast=False,
        metrics={
            "assays": float(report["assay_id"].nunique()),
            "landscapes": float(len(report)),
            "full_target_landscapes": float(len(extra)),
            # This invocation only: 0 positions when the landscapes were complete already.
            "positions_computed": float(computed),
            "wall_seconds": wall,
        },
        artifacts=[path],
    )


@score_app.command("check-landscapes")
def score_check_landscapes(
    reference: Reference,
    out: Annotated[Path, typer.Option(help="Output Parquet file: one row per landscape.")],
    three_di_dir: ThreeDiDir = None,
    structure_features_dir: StructureFeaturesDir = None,
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Fail unless every model in ``scoring.landscapes.models`` has every landscape complete.

    For each model and each of its assays: the benchmark-region landscape, and the full-target
    one where that differs, must hold all 20 values at every residue in the cache
    (``scoring.cache_dir``). No model is loaded. A structure-aware model is checked on the
    assays with a structure. Writes the coverage table whether or not the check passes.
    """
    base = [o for o in overrides or [] if not o.startswith("model=")]
    cfg = load_config(config_dir, base)
    table = pd.read_csv(reference)
    cache = LogProbCache(Path(cfg.scoring.cache_dir))
    frames: list[pd.DataFrame] = []
    try:
        for name in cfg.scoring.landscapes.models:
            model_cfg = load_config(config_dir, [*base, f"model={name}"])
            targets = _landscape_targets(
                model_cfg, table, three_di_dir, structure_features_dir, config_dir
            )
            frames.append(
                landscape_coverage(
                    cache,
                    targets,
                    model=str(model_cfg.model.name),
                    hf_id=str(model_cfg.model.hf_id),
                    revision=str(model_cfg.model.revision),
                )
            )
        if not frames:
            raise ValueError("scoring.landscapes.models is empty")
    except (ScoringRegionError, StructTokenError, CacheError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    coverage = pd.concat(frames, ignore_index=True)
    path = write_table(coverage, LandscapeCoverageSchema, out)
    for model, rows in coverage.groupby("model", sort=False):
        typer.echo(
            f"{model}: {int(rows['complete'].sum())}/{len(rows)} landscapes complete "
            f"({rows['assay_id'].nunique()} assays, {int((~rows['benchmark']).sum())} "
            f"full-target beside a partial benchmark region), "
            f"{int(rows['positions_scored'].sum()):,}/{int(rows['region_length'].sum()):,} residues"
        )
    typer.echo(f"coverage: {path}")
    incomplete = coverage[~coverage["complete"]]
    if len(incomplete):
        for row in incomplete.head(20).itertuples():
            typer.echo(
                f"  incomplete: {row.model} {row.assay_id} ({row.region}): "
                f"{row.positions_scored}/{row.region_length}",
                err=True,
            )
        typer.echo(f"FAIL: {len(incomplete)} of {len(coverage)} landscapes incomplete", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"PASS: all {len(coverage)} landscapes are complete (L, 20) arrays")


@score_app.command("ensemble")
def score_ensemble(
    scores_root: Annotated[
        Path,
        typer.Option(
            help="Root of the members' <label>/ score sets (`mutascope score zero-shot`).",
            exists=True,
            file_okay=False,
        ),
    ],
    reference: Reference,
    out_dir: Annotated[Path, typer.Option(help="Root for <name>/assay_id=<id>/ partitions.")],
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Build the configured ensemble (``model=<ensemble>``) from its members' written scores.

    Each member's scores are z-scored within an assay and the z-scores are averaged
    (:mod:`mutascope.scoring.ensemble`). No model runs. The ensemble covers the assays every
    member has: with a structure-aware member, the assays with a structure (ADR 0003). The
    members must hold exactly the same variants there. Benchmark it with
    ``mutascope eval zero-shot`` like any other score set. The run is logged to MLflow.
    """
    cfg = load_config(config_dir, overrides)
    name = str(cfg.model.name)
    table = pd.read_csv(reference)
    exceptions = cfg.data.structure_exceptions  # null on a subset (the test profile)
    no_structure = [] if exceptions is None else [str(a) for a in exceptions]
    try:
        members = _members(cfg, config_dir)
        if not members:
            raise ValueError(f"{name} is not an ensemble: its config has no members")
        scores: dict[str, pd.DataFrame] = {}
        for label in members:
            if not (scores_root / label).is_dir():
                raise ValueError(f"no scores for member {label} in {scores_root}")
            scores[label] = load_scores(scores_root / label)
            if set(scores[label]["model"]) != {label}:
                found = sorted(set(scores[label]["model"]))
                raise ValueError(f"{scores_root / label} holds {found}, not {label}")
        assays = shared_assays({k: set(v["assay_id"]) for k, v in scores.items()}, no_structure)
        expected = _model_assays(cfg, table, config_dir)
        if set(assays) != set(expected):
            raise ValueError(
                f"the members share {len(assays)} assays, the reference gives {len(expected)}: "
                f"missing {sorted(set(expected) - set(assays))}, "
                f"unexpected {sorted(set(assays) - set(expected))}"
            )
        shared = {k: v[v["assay_id"].isin(assays)] for k, v in scores.items()}
        ensemble = zscore_mean(shared, name)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    write_scores(ensemble, out_dir / name)
    typer.echo(f"{name}: mean of within-assay z-scores of {', '.join(members)}")
    typer.echo(f"{len(assays)} assays, {len(ensemble):,} variants -> {out_dir / name}")
    for label, frame in scores.items():
        left_out = frame["assay_id"].nunique() - len(assays)
        typer.echo(f"  {label}: {frame['assay_id'].nunique()} assays, {left_out} not shared")
    _track(
        cfg,
        "score",
        name,
        fast=False,
        metrics={"assays": float(len(assays)), "variants": float(len(ensemble))},
        artifacts=[],
        members=members,
    )


@eval_app.command("model-gate")
def eval_model_gate(
    scores_dir: Annotated[
        Path,
        typer.Option(
            help="Our scores (`mutascope score zero-shot`).", exists=True, file_okay=False
        ),
    ],
    precomputed_dir: Annotated[
        Path,
        typer.Option(help="ProteinGym zero-shot score partitions.", exists=True, file_okay=False),
    ],
    reference: Reference,
    out_dir: Annotated[Path, typer.Option(help="Results directory.")],
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Model gate (ADR 0010): our scores against ProteinGym's precomputed ones, single mutants.

    Writes ``model_gate_<model>.parquet`` (per-assay agreement) to ``out_dir`` and logs the
    outcome to MLflow. Exits 1 if any assay is below ``eval.model_gate.min_assay_spearman`` or
    the aggregate differs by more than ``eval.model_gate.aggregate_tolerance``. Masked
    marginals only: ProteinGym's ``ESM2_*`` columns are masked marginals, so a wild-type
    score set has nothing to be compared with. Benchmark results are written by
    ``mutascope eval zero-shot``.

    The comparison covers the model's assays (:func:`_model_assays`): all of the reference,
    or for a structure-aware model those with a structure. Our scores must hold exactly the
    single mutants ProteinGym has for those assays.

    Assays in ``eval.model_gate.exceptions.<model>`` (ADR 0015: ProteinGym's scores for them
    come from wrong inputs) are compared and written out but left out of both checks. The
    gate also fails if such an assay reaches the threshold, so the list cannot go stale.
    """
    cfg = load_config(config_dir, overrides)
    column, name = str(cfg.model.proteingym_column), str(cfg.model.name)
    ours = load_scores(scores_dir)
    if set(ours["model"]) != {name}:
        typer.echo(f"error: {scores_dir} holds {sorted(set(ours['model']))}, not {name}", err=True)
        raise typer.Exit(code=1)
    table = pd.read_csv(reference)
    theirs = load_precomputed_scores(precomputed_dir, [column], "single")
    theirs = theirs[theirs["assay_id"].isin(_model_assays(cfg, table, config_dir))]
    try:
        joined = join_scores(ours, theirs, column)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    thresholds = GateThresholds(
        float(cfg.eval.model_gate.min_assay_spearman),
        float(cfg.eval.model_gate.aggregate_tolerance),
    )
    documented = cfg.eval.model_gate.exceptions.get(name)  # none for most models
    exceptions = [] if documented is None else [str(a) for a in documented]
    try:
        gate = model_gate(joined, table, thresholds, exceptions)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    agreement = write_table(gate.per_assay, AgreementSchema, out_dir / f"model_gate_{name}.parquet")
    gated = gate.per_assay[~gate.per_assay["exception"]]

    rho, diff = gated["spearman"], gated["max_abs_diff"]
    typer.echo(
        f"{name} vs proteingym:{column}: {len(gate.per_assay)} assays, "
        f"{len(joined):,} single mutants; gated: {len(gated)} assays, "
        f"{int(gated['variants'].sum()):,} single mutants"
    )
    typer.echo(
        f"  per-variant Spearman: min {rho.min():.6f}, median {rho.median():.6f}; "
        f"max |ours - theirs| {diff.max():.2e}"
    )
    typer.echo(
        f"  aggregate Spearman vs DMS_score: ours {gate.ours:.4f}, theirs {gate.theirs:.4f}, "
        f"delta {gate.aggregate_delta:+.4f} (tolerance {thresholds.aggregate_tolerance})"
    )
    typer.echo(f"  assays below {thresholds.min_assay_spearman}: {len(gate.failing)}")
    for record in gate.failing.itertuples(index=False):
        typer.echo(
            f"    {record.assay_id}: Spearman {record.spearman:.4f}, "
            f"max |diff| {record.max_abs_diff:.3g}, {record.variants} variants"
        )
    if len(gate.exceptions):
        typer.echo(
            f"  documented exceptions (not gated): {len(gate.exceptions)}; aggregate with them "
            f"included: ours {gate.ours_all:.4f}, theirs {gate.theirs_all:.4f}"
        )
        for record in gate.exceptions.itertuples(index=False):
            stale = " STALE: reaches the threshold, remove the entry" if record.passed else ""
            typer.echo(
                f"    {record.assay_id}: Spearman {record.spearman:.4f}, "
                f"{record.variants} variants{stale}"
            )
    typer.echo(f"agreement: {agreement}")
    _track(
        cfg,
        "model-gate",
        name,
        fast=False,
        metrics={
            "assays": float(len(gate.per_assay)),
            "variants": float(len(joined)),
            "min_assay_spearman": float(rho.min()),
            "max_abs_diff": float(diff.max()),
            "assays_failing": float(len(gate.failing)),
            "aggregate_spearman_ours": gate.ours,
            "aggregate_spearman_theirs": gate.theirs,
            "aggregate_delta": gate.aggregate_delta,
            "assays_gated": float(len(gated)),
            "exceptions": float(len(gate.exceptions)),
            "exceptions_stale": float(len(gate.stale)),
            "aggregate_spearman_ours_all_assays": gate.ours_all,
            "aggregate_spearman_theirs_all_assays": gate.theirs_all,
        },
        artifacts=[agreement],
        tags={"gate_passed": str(gate.passed).lower(), "proteingym_column": column},
    )
    if not gate.passed:
        typer.echo("error: model gate FAILED", err=True)
        raise typer.Exit(code=1)
    typer.echo("model gate passed")


@eval_app.command("zero-shot")
def eval_zero_shot(
    scores_dir: Annotated[
        Path,
        typer.Option(
            help="One score set: <label>/ written by `mutascope score zero-shot`.",
            exists=True,
            file_okay=False,
        ),
    ],
    variants_dir: Annotated[
        Path, typer.Option(help="Processed variant partitions.", exists=True, file_okay=False)
    ],
    reference: Reference,
    out_dir: Annotated[Path, typer.Option(help="Results directory.")],
    fast: Annotated[
        bool, typer.Option("--fast", help="The score set is the wild-type marginal.")
    ] = False,
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Benchmark one of our zero-shot score sets (``model=<name>``, ``--fast``) on single mutants.

    Writes ``zero_shot_<label>_single.parquet`` (per assay, long format) and
    ``..._summary.parquet`` (ProteinGym-style aggregates with bootstrap CIs) to ``out_dir``,
    and logs the aggregates to MLflow. The labels come from the processed variants, and the
    scores must cover exactly those variants. Exits 1 if the score set is not the one the
    config and ``--fast`` name, so results can never be tracked under the wrong revision.

    A structure-aware model is benchmarked on its assays with a structure (213 of 217 on the
    pinned release); the assay count is printed and is ``n_assays`` in the summary. So is an
    ensemble with a structure-aware member (``mutascope score ensemble`` writes its scores).
    """
    cfg = load_config(config_dir, overrides)
    try:
        members = _members(cfg, config_dir)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if members and fast:
        typer.echo(
            "error: --fast does not apply to an ensemble; its members name methods", err=True
        )
        raise typer.Exit(code=1)
    label = run_label(str(cfg.model.name), fast=fast)
    scores = load_scores(scores_dir)
    if set(scores["model"]) != {label}:
        typer.echo(
            f"error: {scores_dir} holds {sorted(set(scores['model']))}, not {label}", err=True
        )
        raise typer.Exit(code=1)
    table = pd.read_csv(reference)
    try:
        variants = with_labels(scores, variants_dir, _model_assays(cfg, table, config_dir))
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    per_assay = assay_metrics_table(variants, {"score": label}, float(cfg.eval.top_percent))
    bootstrap = BootstrapParams(
        int(cfg.eval.bootstrap.n_resamples), float(cfg.eval.bootstrap.confidence)
    )
    summary = summarise(per_assay, table, bootstrap, seed_all(int(cfg.eval.bootstrap.seed)))
    results = write_results(per_assay, summary, out_dir, f"zero_shot_{label}_single")
    typer.echo(
        f"{label}: {variants['assay_id'].nunique()} assays, {len(variants):,} single mutants"
    )
    overall = summary[summary["level"] == "overall"]
    for record in overall.itertuples(index=False):
        typer.echo(
            f"  {record.metric:<10} {record.value:.4f} "
            f"[{record.ci_low:.4f}, {record.ci_high:.4f}] ({record.n_assays} assays)"
        )
    typer.echo(f"results: {results[0]}, {results[1]}")
    _track(
        cfg,
        "benchmark",
        label,
        fast=fast,
        metrics=summary_metrics(summary, label)
        | {
            "assays": float(variants["assay_id"].nunique()),
            "variants": float(len(variants)),
        },
        artifacts=list(results),
        tags={"rows": "single", "kind": "zero-shot"},
        members=members,
    )


@eval_app.command("compare")
def eval_compare(
    results_dir: Annotated[
        Path,
        typer.Option(
            help="Directory of zero_shot_<label>_single.parquet (`mutascope eval zero-shot`).",
            exists=True,
            file_okay=False,
        ),
    ],
    reference: Reference,
    out: Annotated[Path, typer.Option(help="Output Parquet file, e.g. <results>/zero_shot/…")],
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Paired bootstrap comparison of every pair in ``eval.comparisons`` (ADR 0014).

    For each pair ``[A, B]`` the same resampled assays are applied to both score sets and the
    full ProteinGym aggregate is recomputed, giving ``A - B`` with a CI and the share of
    resamples where A is better, for every metric, overall and per function type. Every pair
    starts from the same seed, so a pair's numbers do not depend on which other pairs are
    listed. Writes one table (:class:`ComparisonSchema`) and logs one MLflow run.

    A structure-aware model has no scores for ``data.structure_exceptions``; a pair with one
    is compared on the assays both have (ADR 0003), and the count is printed and stored.
    """
    cfg = load_config(config_dir, overrides)
    pairs = [(str(a), str(b)) for a, b in cfg.eval.comparisons]
    labels = sorted({label for pair in pairs for label in pair})
    missing = [
        label
        for label in labels
        if not (results_dir / f"zero_shot_{label}_single.parquet").exists()
    ]
    if not pairs or missing:
        problem = (
            f"no results for {missing} in {results_dir}" if pairs else "eval.comparisons is empty"
        )
        typer.echo(f"error: {problem}", err=True)
        raise typer.Exit(code=1)
    results = {
        label: read_results(results_dir / f"zero_shot_{label}_single.parquet") for label in labels
    }
    table = pd.read_csv(reference)
    bootstrap = BootstrapParams(
        int(cfg.eval.bootstrap.n_resamples), float(cfg.eval.bootstrap.confidence)
    )
    seed = int(cfg.eval.bootstrap.seed)
    seed_all(seed)
    exceptions = cfg.data.structure_exceptions  # null on a subset (the test profile)
    no_structure = [] if exceptions is None else [str(a) for a in exceptions]
    frames: list[pd.DataFrame] = []
    try:
        for a, b in pairs:
            frames.append(
                paired_bootstrap_diff(
                    results[a],
                    results[b],
                    table,
                    bootstrap,
                    np.random.default_rng(seed),
                    may_be_absent=no_structure,
                )
            )
    except (ValueError, KeyError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    path = write_table(pd.concat(frames, ignore_index=True), ComparisonSchema, out)

    metrics: dict[str, float] = {}
    typer.echo(f"{len(pairs)} paired comparisons, {bootstrap.n_resamples:,} resamples, A - B:")
    for (a, b), frame in zip(pairs, frames, strict=True):
        headline = frame[(frame["metric"] == "spearman") & (frame["level"] == "overall")]
        row = headline.iloc[0]
        typer.echo(
            f"  {a} - {b}: Spearman {row['diff']:+.4f} "
            f"[{row['ci_low']:+.4f}, {row['ci_high']:+.4f}], "
            f"A better in {row['share_a_better']:.1%} of resamples ({row['n_assays']} assays)"
        )
        overall = frame[frame["level"] == "overall"]
        for metric, diff, low, high, share in zip(
            overall["metric"],
            overall["diff"],
            overall["ci_low"],
            overall["ci_high"],
            overall["share_a_better"],
            strict=True,
        ):
            key = f"{metric}_diff/{a}-vs-{b}"
            metrics |= {
                key: float(diff),
                f"{key}/ci_low": float(low),
                f"{key}/ci_high": float(high),
                f"{key}/share_a_better": float(share),
            }
    typer.echo(f"comparisons: {path}")
    _log_run(
        cfg,
        "compare",
        "zero-shot",
        {
            "pairs": ", ".join(f"{a} - {b}" for a, b in pairs),
            "bootstrap.n_resamples": bootstrap.n_resamples,
            "bootstrap.confidence": bootstrap.confidence,
            "bootstrap.seed": seed,
        },
        metrics,
        [path],
        {"kind": "zero-shot", "rows": "single"},
    )


@eval_app.command("shared")
def eval_shared(
    results_dir: Annotated[
        Path,
        typer.Option(
            help="Directory of zero_shot_<label>_single.parquet (`mutascope eval zero-shot`).",
            exists=True,
            file_okay=False,
        ),
    ],
    reference: Reference,
    out: Annotated[Path, typer.Option(help="Output Parquet file, e.g. <results>/zero_shot/…")],
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Summarise the score sets of ``eval.shared_summary`` on the assays all of them have.

    A structure-aware model has no scores for ``data.structure_exceptions``, so its summary
    file covers fewer assays than a sequence-only model's and the two files cannot be shown
    side by side. This writes every listed score set's ProteinGym aggregates and bootstrap
    CIs on the shared assays (:class:`SummarySchema`; ``n_assays`` says how many) and logs one
    MLflow run. The per-assay values are read from the results files, never recomputed.
    """
    cfg = load_config(config_dir, overrides)
    labels = [str(label) for label in cfg.eval.shared_summary]
    missing = [
        label
        for label in labels
        if not (results_dir / f"zero_shot_{label}_single.parquet").exists()
    ]
    if not labels or missing:
        problem = (
            f"no results for {missing} in {results_dir}"
            if labels
            else "eval.shared_summary is empty"
        )
        typer.echo(f"error: {problem}", err=True)
        raise typer.Exit(code=1)
    bootstrap = BootstrapParams(
        int(cfg.eval.bootstrap.n_resamples), float(cfg.eval.bootstrap.confidence)
    )
    seed = int(cfg.eval.bootstrap.seed)
    seed_all(seed)
    exceptions = cfg.data.structure_exceptions  # null on a subset (the test profile)
    no_structure = [] if exceptions is None else [str(a) for a in exceptions]
    try:
        summary = summarise_shared(
            {
                label: read_results(results_dir / f"zero_shot_{label}_single.parquet")
                for label in labels
            },
            pd.read_csv(reference),
            bootstrap,
            seed,
            may_be_absent=no_structure,
        )
    except (ValueError, KeyError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    path = write_table(summary, SummarySchema, out)
    metrics: dict[str, float] = {}
    typer.echo(f"{len(labels)} score sets on their shared assays:")
    for label in labels:
        metrics |= {f"{label}/{k}": v for k, v in summary_metrics(summary, label).items()}
        row = summary[
            (summary["model"] == label)
            & (summary["metric"] == "spearman")
            & (summary["level"] == "overall")
        ].iloc[0]
        typer.echo(
            f"  {label:<36} Spearman {row['value']:.4f} "
            f"[{row['ci_low']:.4f}, {row['ci_high']:.4f}] ({row['n_assays']} assays)"
        )
    typer.echo(f"summary: {path}")
    _log_run(
        cfg,
        "shared-summary",
        "zero-shot",
        {
            "score_sets": ", ".join(labels),
            "bootstrap.n_resamples": bootstrap.n_resamples,
            "bootstrap.confidence": bootstrap.confidence,
            "bootstrap.seed": seed,
        },
        metrics,
        [path],
        {"kind": "zero-shot", "rows": "single"},
    )


@eval_app.command("figures")
def eval_figures(
    results_dir: Annotated[
        Path,
        typer.Option(
            help="Directory of zero_shot_<label>_single_summary.parquet.",
            exists=True,
            file_okay=False,
        ),
    ],
    comparisons: Annotated[
        Path,
        typer.Option(help="Paired comparisons (`eval compare`).", exists=True, dir_okay=False),
    ],
    shared_summary: Annotated[
        Path,
        typer.Option(help="Shared-assay summary (`eval shared`).", exists=True, dir_okay=False),
    ],
    out_dir: Annotated[Path, typer.Option(help="Directory for the figure files.")],
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Draw the benchmark figures of ``eval.figures`` from the results files (ADR 0016).

    Model size against the metric, bars per function type, the ensemble's gain over its
    members and the masked against the wild-type marginal, each in every format of
    ``eval.figures.formats``. Every number is read from a results file; a missing one fails
    the command. Differences between score sets are shown with their paired-bootstrap CI.
    """
    # matplotlib is the optional `figures` extra; no other command needs it.
    from mutascope.eval.figures import (
        FigureContext,
        FigureDataError,
        difference_figure,
        function_type_figure,
        model_size_figure,
        save_figure,
    )

    cfg = load_config(config_dir, overrides)
    spec = cfg.eval.figures
    context = FigureContext(
        metric=str(spec.metric),
        names={str(k): str(v) for k, v in spec.names.items()},
        dataset=f"ProteinGym {cfg.data.release.version}",
        confidence=float(cfg.eval.bootstrap.confidence),
    )
    sizes = {str(k): float(v) for k, v in spec.model_size.items()}
    gains = [(str(a), str(b)) for a, b in spec.ensemble_gain]
    fast = [(str(a), str(b)) for a, b in spec.fast_vs_masked]
    try:
        missing = [
            label
            for label in sizes
            if not (results_dir / f"zero_shot_{label}_single_summary.parquet").exists()
        ]
        not_wt = [(a, b) for a, b in fast if b != run_label(a, fast=True)]
        if missing or not_wt:
            raise FigureDataError(
                f"no summary for {missing} in {results_dir}"
                if missing
                else f"eval.figures.fast_vs_masked: not [masked, wild-type] pairs: {not_wt}"
            )
        summaries = pd.concat(
            [
                read_results(results_dir / f"zero_shot_{label}_single_summary.parquet")
                for label in sizes
            ],
            ignore_index=True,
        )
        paired, shared = read_results(comparisons), read_results(shared_summary)
        figures = {
            f"model_size_vs_{context.metric}": model_size_figure(summaries, paired, sizes, context),
            f"function_type_{context.metric}": function_type_figure(
                shared, [str(label) for label in spec.function_types], context
            ),
            "ensemble_gain": difference_figure(
                paired,
                gains,
                context,
                title=f"Ensemble gain in zero-shot {context.metric_name}",
                panel_titles=[
                    f"{context.name(a)} \N{MINUS SIGN} {context.name(b)}" for a, b in gains
                ],
                setting="single mutants",
            ),
            "fast_vs_masked": difference_figure(
                paired,
                fast,
                context,
                title=f"Masked minus wild-type marginal, zero-shot {context.metric_name}",
                panel_titles=[context.name(a) for a, _ in fast],
                setting="single mutants",
            ),
        }
        formats, dpi = [str(f) for f in spec.formats], int(spec.dpi)
        for name, figure in figures.items():
            for path in save_figure(figure, out_dir, name, formats, dpi):
                typer.echo(f"figure: {path}")
    except FigureDataError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@features_app.command("embed")
def features_embed(
    reference: Reference,
    report_dir: Annotated[Path, typer.Option(help="Directory for embedding_run_<model>.parquet.")],
    three_di_dir: ThreeDiDir = None,
    structure_features_dir: StructureFeaturesDir = None,
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Per-residue embeddings of every landscape of the configured model (``model=<name>``).

    One unmasked pass per distinct sequence, region and structure-token string (assays of one
    protein share it): the benchmark region of every assay, and the full target where that
    region is only part of it, as for ``score landscapes``. The embedding of a residue is the
    mean of the last ``features.embedding_layers`` hidden states (ADR 0020). Each pass is
    written to ``features.embedding_cache_dir`` before the next starts, so a rerun computes
    only what is missing. Logged to MLflow in the ``features.experiment`` experiment.
    """
    cfg = load_config(config_dir, overrides)
    if cfg.model.get("members"):
        typer.echo(f"error: {cfg.model.name} is an ensemble: it has no embedding", err=True)
        raise typer.Exit(code=1)
    table = pd.read_csv(reference)
    try:
        targets = _landscape_targets(cfg, table, three_di_dir, structure_features_dir, config_dir)
    except (ScoringRegionError, StructTokenError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    scorer = build_scorer(
        as_str_dict(OmegaConf.to_container(cfg.model, resolve=True), "model"),
        device=str(cfg.scoring.device),
        cache_dir=None,
    )
    if not isinstance(scorer, MaskedLMScorer):
        typer.echo(f"error: {cfg.model.name} is not a masked language model", err=True)
        raise typer.Exit(code=1)
    layers = int(cfg.features.embedding_layers)
    cache = LogProbCache(Path(cfg.features.embedding_cache_dir), width=hidden_size(scorer))
    started = time.monotonic()
    report = fill_embeddings(scorer, cache, targets, layers=layers)
    wall = time.monotonic() - started
    name = scorer.model_id
    path = write_table(report, EmbeddingRunSchema, report_dir / f"embedding_run_{name}.parquet")
    computed = report[report["computed"]]
    entries = {t.entry for t in targets}
    typer.echo(
        f"{name}: {len(report)} landscapes of {report['assay_id'].nunique()} assays, "
        f"{len(entries)} distinct sequences (one pass each), width {cache.width}"
    )
    typer.echo(
        f"this run: {format_duration(wall)}; {len(computed)} passes over "
        f"{int(computed['region_length'].sum()):,} residues "
        f"({float(computed['seconds'].sum()):.0f} s of model time)"
    )
    typer.echo(f"report: {path}")
    _log_run(
        cfg,
        "embed",
        name,
        {
            "model.name": name,
            "model.hf_id": str(cfg.model.hf_id),
            "model.revision": str(cfg.model.revision),
            "features.embedding_layers": layers,
        },
        {
            "assays": float(report["assay_id"].nunique()),
            "landscapes": float(len(report)),
            "cache_entries": float(len(entries)),
            # This invocation only: 0 passes when every embedding was cached already.
            "passes": float(len(computed)),
            "residues_computed": float(computed["region_length"].sum()),
            "model_seconds": float(computed["seconds"].sum()),
            "wall_seconds": wall,
        },
        [path],
        {"model": name, "kind": "embedding"},
        experiment=str(cfg.features.experiment),
    )


@features_app.command("build")
def features_build(
    reference: Reference,
    variants_dir: Annotated[
        Path,
        typer.Option(
            help="Processed variant partitions (keys only are read).", exists=True, file_okay=False
        ),
    ],
    three_di_dir: Annotated[
        Path, typer.Option(help="Processed 3Di partitions.", exists=True, file_okay=False)
    ],
    structure_features_dir: Annotated[
        Path,
        typer.Option(help="Processed structure-feature partitions.", exists=True, file_okay=False),
    ],
    out_dir: Annotated[Path, typer.Option(help="The feature store (replaced whole).")],
    report: Annotated[Path, typer.Option(help="Output Parquet file: one row per assay.")],
    config_dir: ConfigDir = Path("conf"),
    overrides: Overrides = None,
) -> None:
    """Write every possible substitution of every target with its features (ADR 0020).

    No model is loaded and no label is read. Scores and log-probabilities come from
    ``scoring.cache_dir`` (``score check-landscapes`` must pass), embeddings from
    ``features.embedding_cache_dir`` (``features embed``), structure features from the
    processed tables. Fails, writing nothing, if any input is missing or a measured variant
    is not in the store with the same wild type.
    """
    base = [o for o in overrides or [] if not o.startswith("model=")]
    cfg = load_config(config_dir, base)
    table = pd.read_csv(reference)
    started = time.monotonic()
    try:
        sets = feature_sets(
            as_str_dict(OmegaConf.to_container(cfg.features.sets, resolve=True), "features.sets")
        )
        models = [str(m) for m in cfg.features.models]
        unknown = sorted({m for s in sets.values() for m in s.models} - set(models))
        if unknown:
            raise FeatureSetError(f"feature sets use {unknown}, which features.models lacks")
        sources: list[ModelSource] = []
        for name in models:
            model_cfg = load_config(config_dir, [*base, f"model={name}"])
            targets = _landscape_targets(
                model_cfg, table, three_di_dir, structure_features_dir, config_dir
            )
            by_assay: dict[str, list[LandscapeTarget]] = {}
            for target in targets:
                by_assay.setdefault(target.assay_id, []).append(target)
            sources.append(
                ModelSource(
                    name=str(model_cfg.model.name),
                    hf_id=str(model_cfg.model.hf_id),
                    revision=str(model_cfg.model.revision),
                    targets=by_assay,
                    embedding_cache=LogProbCache(Path(cfg.features.embedding_cache_dir), None),
                    embedding_layers=int(cfg.features.embedding_layers),
                )
            )
        store = build_store(
            table,
            sources,
            LogProbCache(Path(cfg.scoring.cache_dir)),
            read_structure_features(structure_features_dir),
            measured_keys(variants_dir),
            out_dir,
        )
    except (
        FeatureSetError,
        FeatureStoreError,
        ScoringRegionError,
        StructTokenError,
        CacheError,
        ValueError,
    ) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    wall = time.monotonic() - started
    path = write_table(store, FeatureStoreReportSchema, report)
    typer.echo(
        f"{len(store)} assays, {int(store['variants'].sum()):,} variants "
        f"({int(store['measured'].sum()):,} measured), {int(store['target_length'].sum()):,} "
        f"residues -> {out_dir}"
    )
    for part in ("variants", "positions", "arrays"):
        size = sum(f.stat().st_size for f in (out_dir / part).rglob("*") if f.is_file())
        typer.echo(f"  {part:<9} {size / 1e6:,.1f} MB")
    typer.echo(f"  total     {int(store['bytes'].sum()) / 1e6:,.1f} MB, built in {wall:.0f} s")
    hidden = {s.name: _embedding_width(out_dir, store, s.name) for s in sources}
    structured = set(store.loc[store["structure"], "assay_id"])
    for feature_set in sets.values():
        assays = assays_of(feature_set, store["assay_id"].tolist(), structured)
        rows = int(store.set_index("assay_id").loc[assays, "variants"].sum())
        if "site_onehot" in feature_set.blocks:
            dense = FeatureSet(
                feature_set.name,
                tuple(b for b in feature_set.blocks if b != "site_onehot"),
                feature_set.structure,
            )
            typer.echo(
                f"  set {feature_set.name}: {len(assays)} assays, {rows:,} variants x "
                f"(20 x L site one-hot, 2 entries per row, + {width(dense, hidden)} features)"
            )
            continue
        columns = width(feature_set, hidden)
        typer.echo(
            f"  set {feature_set.name}: {len(assays)} assays, {rows:,} variants x {columns:,} "
            f"features ({rows * columns * 4 / 1e9:.1f} GB if assembled at once in float32)"
        )
    typer.echo(f"report: {path}")


def _embedding_width(store_dir: Path, store: pd.DataFrame, model: str) -> int:
    """Width of ``model``'s embeddings, read from the store; the same in every assay."""
    widths = {
        int(np.load(p, mmap_mode="r").shape[1])
        for p in (store_dir / "arrays").glob(f"*/{model}.embedding.npy")
    }
    if len(widths) != 1:
        raise FeatureStoreError(f"{model}: embedding widths {sorted(widths)} in the store")
    return widths.pop()
