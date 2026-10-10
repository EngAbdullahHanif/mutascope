"""Stage the processed dataset as a Hugging Face Hub dataset repo, and push it.

Staging and pushing are separate on purpose: :func:`stage_dataset` writes a folder that is
exactly what the Hub will hold (Parquet tables, reports, ``README.md`` card, checksums), so it
can be inspected before :func:`push_dataset` uploads it unchanged.

The local processed data is partitioned by assay with ``assay_id`` only in the directory name
(ADR 0005). The Hub gets one Parquet file per table with ``assay_id`` as a real column instead,
because the ``datasets`` loader and the Hub viewer do not read hive partition paths. Rows are
sorted, so staging the same data twice gives identical tables.
"""

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pandera.pandas as pa
from huggingface_hub import HfApi
from omegaconf import OmegaConf

from mutascope import __version__
from mutascope.data.schemas import (
    ProcessedVariantSchema,
    PublishedAssaySchema,
    StructureFeatureSchema,
    ThreeDiSchema,
)
from mutascope.utils.io import fresh_tmp_dir, replace_dir
from mutascope.utils.logging import get_logger

log = get_logger(__name__)

CARD_FILE = "README.md"
CHECKSUMS_FILE = "SHA256SUMS"
PROVENANCE_FILE = "provenance.json"
REPORT_FILES = (
    "cleaning_report.parquet",
    "cleaning_report.md",
    "structure_report.parquet",
    "structure_report.md",
)


class PublishError(RuntimeError):
    """The processed data, card or reports are not in a publishable state."""


@dataclass(frozen=True)
class Table:
    """One published table: its Hub config name, file, query and schema."""

    name: str
    path: str
    query: str
    schema: type[pa.DataFrameModel]


TABLES = (
    Table(
        "variants",
        "data/variants.parquet",
        "SELECT assay_id, mutant, position, wt_aa, mt_aa, DMS_score, DMS_score_bin "
        "FROM variants ORDER BY assay_id, position, mt_aa",
        ProcessedVariantSchema,
    ),
    Table(
        "assays",
        "data/assays.parquet",
        """
        SELECT a.assay_id, a.UniProt_ID, a.taxon, a.source_organism, a.molecule_name,
               a.target_seq, a.seq_len, a.coarse_selection_type, a.selection_assay,
               a.selection_type, a.DMS_number_single_mutants, a.DMS_binarization_cutoff,
               a.DMS_binarization_method, a.first_author, a.year, a.jo, a.pdb_file,
               a.pdb_range,
               a.assay_id IN (SELECT DISTINCT assay_id FROM structure_features) AS has_structure
        FROM assays a ORDER BY a.assay_id
        """,
        PublishedAssaySchema,
    ),
    Table(
        "structure_features",
        "data/structure_features.parquet",
        "SELECT assay_id, position, residue, plddt, sasa, rel_sasa, contact_count "
        "FROM structure_features ORDER BY assay_id, position",
        StructureFeatureSchema,
    ),
    Table(
        "three_di",
        "data/three_di.parquet",
        "SELECT assay_id, position, residue, three_di FROM three_di ORDER BY assay_id, position",
        ThreeDiSchema,
    ),
)
"""Published tables; ``variants`` is the default config on the Hub."""


@dataclass(frozen=True)
class StagedDataset:
    """What :func:`stage_dataset` wrote: rows per table and the staged folder."""

    out_dir: Path
    rows: dict[str, int]


def card_front_matter(metadata: dict[str, Any]) -> str:
    """YAML front matter for the Hub card: the configured metadata plus one config per table."""
    configs = [
        {"config_name": t.name, "data_files": t.path, **({"default": True} if i == 0 else {})}
        for i, t in enumerate(TABLES)
    ]
    body = OmegaConf.to_yaml(OmegaConf.create({**metadata, "configs": configs}))
    return f"---\n{body}---\n\n"


def stage_dataset(
    con: duckdb.DuckDBPyConnection,
    *,
    card: Path,
    card_metadata: dict[str, Any],
    reports_dir: Path,
    provenance: dict[str, Any],
    out_dir: Path,
) -> StagedDataset:
    """Write the Hub-ready folder to ``out_dir``, replacing any previous staging.

    Args:
        con: Connection from :func:`mutascope.data.db.connect` with the ``variants``,
            ``assays``, ``structure_features`` and ``three_di`` views.
        card: Markdown body of the dataset card.
        card_metadata: Hub front-matter fields (licence, tags, ...).
        reports_dir: Directory holding the cleaning and structure reports.
        provenance: Facts recorded in ``provenance.json`` (release pin, input checksums).
        out_dir: Folder to stage into. Built in ``<out_dir>.tmp`` and swapped in when complete.

    Raises:
        PublishError: A report is missing, or the assay table and variant table disagree.
        pandera.errors.SchemaErrors: A table fails its schema.
    """
    missing = [name for name in REPORT_FILES if not (reports_dir / name).is_file()]
    if missing:
        raise PublishError(f"missing reports in {reports_dir}: {', '.join(missing)}")
    _check_single_counts(con)

    tmp = fresh_tmp_dir(out_dir)
    try:
        (tmp / "data").mkdir()
        rows: dict[str, int] = {}
        for table in TABLES:
            frame = table.schema.validate(con.sql(table.query).df(), lazy=True)
            con.from_df(frame).write_parquet(str(tmp / table.path))
            rows[table.name] = len(frame)
            log.info("table_staged", table=table.name, rows=len(frame), path=table.path)
        (tmp / "reports").mkdir()
        for name in REPORT_FILES:
            shutil.copyfile(reports_dir / name, tmp / "reports" / name)
        (tmp / PROVENANCE_FILE).write_text(
            json.dumps({**provenance, "mutascope_version": __version__, "rows": rows}, indent=2)
            + "\n"
        )
        (tmp / CARD_FILE).write_text(card_front_matter(card_metadata) + card.read_text())
        (tmp / CHECKSUMS_FILE).write_text(checksums(tmp))
    except BaseException:
        shutil.rmtree(tmp)
        raise
    replace_dir(tmp, out_dir)
    return StagedDataset(out_dir, rows)


def _check_single_counts(con: duckdb.DuckDBPyConnection) -> None:
    """Fail unless every assay's published single-mutant count equals its processed rows."""
    mismatched = con.sql(
        """
        SELECT a.assay_id, a.DMS_number_single_mutants AS expected,
               coalesce(v.n, 0) AS actual
        FROM assays a
        FULL JOIN (SELECT assay_id, count(*) AS n FROM variants GROUP BY assay_id) v
            USING (assay_id)
        WHERE a.assay_id IS NULL OR coalesce(v.n, 0) <> a.DMS_number_single_mutants
        ORDER BY a.assay_id
        """
    ).fetchall()
    if mismatched:
        details = ", ".join(f"{a} (reference {e}, processed {n})" for a, e, n in mismatched[:10])
        raise PublishError(f"{len(mismatched)} assays disagree with the reference: {details}")


def checksums(folder: Path) -> str:
    """``sha256sum``-format lines for every file under ``folder``, sorted by path."""
    lines: list[str] = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        if path.name == CHECKSUMS_FILE:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(folder).as_posix()}\n")
    return "".join(lines)


def push_dataset(stage_dir: Path, repo_id: str, *, private: bool, token: str | None = None) -> str:
    """Create the dataset repo if needed and upload the staged folder as one commit.

    Args:
        stage_dir: Output of :func:`stage_dataset`; its checksums are re-verified first.
        repo_id: ``"<user or org>/<name>"``.
        private: Visibility for a *new* repo. An existing repo keeps its visibility.
        token: Hugging Face token; ``None`` uses ``HF_TOKEN`` or the cached ``hf auth login``.

    Returns:
        URL of the upload commit.

    Raises:
        PublishError: The staged folder was changed after staging.
    """
    expected = (stage_dir / CHECKSUMS_FILE).read_text()
    if checksums(stage_dir) != expected:
        raise PublishError(f"{stage_dir} changed since it was staged; stage it again")
    api = HfApi(token=token)
    api.create_repo(repo_id, repo_type="dataset", private=private, exist_ok=True)
    commit = api.upload_folder(
        folder_path=stage_dir,
        repo_id=repo_id,
        repo_type="dataset",
        commit_message=f"Publish processed ProteinGym data (mutascope {__version__})",
        delete_patterns=["data/*", "reports/*"],
    )
    return commit.commit_url
