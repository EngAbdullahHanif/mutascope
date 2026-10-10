"""Cross-check the structure outputs and summarise them per assay (Parquet + Markdown).

Before anything is summarised, the two structure tables are checked against each other and
against the structure mapping. Any disagreement raises instead of being reported:

- both tables cover exactly the mapped assays, and no documented exception has a partition;
- they have the same ``(assay_id, position)`` keys with the same ``residue``.

Like the cleaning report, the Markdown has no timestamps or local paths, so a rerun on the same
data and the same Foldseek build gives an identical file on any machine. The Foldseek binary is
identified by version and sha256, which pin it uniquely; its absolute path stays only in the
gitignored ``foldseek_run.json`` next to the 3Di partitions, for local debugging.
"""

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from mutascope.data.db import connect
from mutascope.data.schemas import StructureReportSchema
from mutascope.data.structures import StructureCheck
from mutascope.data.three_di import RUN_FILE, FoldseekBinary

REPORT_PARQUET = "structure_report.parquet"
REPORT_MARKDOWN = "structure_report.md"


class StructureReportError(ValueError):
    """The structure outputs disagree with each other or with the mapping."""


def structure_report(
    check: StructureCheck,
    *,
    target_lengths: dict[str, int],
    variants_dir: Path,
    features_dir: Path,
    three_di_dir: Path,
    low_plddt_threshold: float,
) -> pd.DataFrame:
    """Build the validated per-assay report.

    Args:
        check: The structure mapping the outputs were built from.
        target_lengths: assay_id -> ``len(target_seq)`` for every assay in the reference.
        variants_dir, features_dir, three_di_dir: The processed partition directories.
        low_plddt_threshold: Residues below this pLDDT count towards ``frac_low_plddt``.

    Raises:
        StructureReportError: The outputs are inconsistent (see module docstring).
    """
    with connect(
        variants_dir, structure_features_dir=features_dir, three_di_dir=three_di_dir
    ) as con:
        _cross_check(con, check)
        stats = con.execute(
            """
            SELECT assay_id, count(*) AS residues, avg(plddt) AS mean_plddt,
                   avg((plddt < ?)::INT) AS frac_low_plddt,
                   avg(rel_sasa) AS mean_rel_sasa,
                   avg(contact_count) AS mean_contact_count
            FROM structure_features GROUP BY assay_id
            """,
            [low_plddt_threshold],
        ).df()
        coverage = con.execute(
            """
            SELECT v.assay_id, count(*) AS variants, count(f.position) AS variants_with_structure
            FROM variants v
            LEFT JOIN structure_features f USING (assay_id, position)
            GROUP BY v.assay_id
            """
        ).df()

    mapped = pd.DataFrame(
        {
            "assay_id": [m.assay_id for m in check.mappings],
            "status": "features",
            "pdb_file": [m.pdb_path.name for m in check.mappings],
            "range_start": [m.range_start for m in check.mappings],
            "range_end": [m.range_end for m in check.mappings],
        }
    ).merge(stats, on="assay_id", how="left")
    excepted = pd.DataFrame(
        {
            "assay_id": [a.assay_id for a in check.allowed_exceptions],
            "status": "exception",
            "documented_reason": [a.documented_reason for a in check.allowed_exceptions],
            "observed_failure": [a.observed_failure for a in check.allowed_exceptions],
        }
    )
    report = pd.concat([mapped, excepted], ignore_index=True)
    report["target_length"] = report["assay_id"].map(target_lengths)
    report = report.merge(coverage, on="assay_id", how="left")
    report[["variants", "variants_with_structure"]] = (
        report[["variants", "variants_with_structure"]].fillna(0).astype(np.int64)
    )
    report = report.astype(
        {
            "assay_id": "str",
            "status": "str",
            "pdb_file": "object",
            "range_start": "Int64",
            "range_end": "Int64",
            "target_length": np.int64,
            "residues": "Int64",
            "documented_reason": "object",
            "observed_failure": "object",
        }
    )
    columns = list(StructureReportSchema.to_schema().columns)
    report = report[columns].sort_values("assay_id", ignore_index=True)
    return StructureReportSchema.validate(report, lazy=True)


def _cross_check(con: duckdb.DuckDBPyConnection, check: StructureCheck) -> None:
    mapped = {m.assay_id for m in check.mappings}
    for table in ("structure_features", "three_di"):
        rows = con.execute(f"SELECT DISTINCT assay_id FROM {table}").fetchall()
        present = {str(r[0]) for r in rows}
        leaked = sorted(present & {a.assay_id for a in check.allowed_exceptions})
        if leaked:
            raise StructureReportError(f"{table} has rows for structure exceptions {leaked}")
        if present != mapped:
            missing, extra = sorted(mapped - present), sorted(present - mapped)
            raise StructureReportError(f"{table}: missing assays {missing}, unexpected {extra}")
    row = con.execute(
        """
        SELECT count(*) FROM structure_features f
        FULL OUTER JOIN three_di t USING (assay_id, position)
        WHERE f.residue IS DISTINCT FROM t.residue
        """
    ).fetchone()
    disagreements = 0 if row is None else int(row[0])
    if disagreements:
        raise StructureReportError(
            f"{disagreements} (assay, position) keys differ between structure_features and "
            "three_di, or have different residues"
        )


def read_foldseek_run(three_di_dir: Path) -> FoldseekBinary:
    """The binary recorded by ``foldseek-3di``; its absence means the 3Di table is unattributed.

    Raises:
        StructureReportError: ``foldseek_run.json`` is missing.
    """
    path = three_di_dir / RUN_FILE
    if not path.is_file():
        raise StructureReportError(f"{path} is missing: rerun `mutascope data foldseek-3di`")
    return FoldseekBinary.from_json(path.read_text())


def write_structure_report(
    report: pd.DataFrame,
    report_dir: Path,
    *,
    low_plddt_threshold: float,
    foldseek: FoldseekBinary,
) -> tuple[Path, Path]:
    """Validate ``report`` and write it as Parquet and Markdown into ``report_dir``."""
    report = StructureReportSchema.validate(report, lazy=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    parquet, markdown = report_dir / REPORT_PARQUET, report_dir / REPORT_MARKDOWN
    with duckdb.connect() as con:
        con.from_df(report).write_parquet(str(parquet))
    markdown.write_text(
        render_markdown(report, low_plddt_threshold=low_plddt_threshold, foldseek=foldseek)
    )
    return parquet, markdown


def render_markdown(
    report: pd.DataFrame, *, low_plddt_threshold: float, foldseek: FoldseekBinary
) -> str:
    """Totals, the Foldseek binary, exceptions, assays needing a look, then every assay."""
    ok = report[report["status"] == "features"]
    exc = report[report["status"] == "exception"]
    threshold = f"{low_plddt_threshold:g}"
    lines = [
        "# Structure report",
        "",
        "Generated by `mutascope data structure-report` from the outputs of "
        "`mutascope data structure-features` and `mutascope data foldseek-3di`. Each mapped "
        "assay's structure passed three independent sequence checks against its `target_seq` "
        "range (the CA-line parser in `check-structures`, biotite, and Foldseek's own "
        "amino-acid output), and the two tables were checked to have identical keys and "
        "residues. Positions are 0-based `target_seq` indices.",
        "",
        "## Totals",
        "",
        "| | count |",
        "|---|---:|",
        f"| assays | {len(report):,} |",
        f"| with structure features and 3Di | {len(ok):,} |",
        f"| documented exceptions (no structure) | {len(exc):,} |",
        f"| PDB files used | {ok['pdb_file'].nunique():,} |",
        f"| residue rows per table | {int(ok['residues'].sum()):,} |",
        f"| variants in assays with structure | {int(ok['variants'].sum()):,} |",
        f"| of which at a position with structure | {int(ok['variants_with_structure'].sum()):,} |",
        "",
        "## Foldseek binary",
        "",
        "The executable that produced the 3Di table, as recorded by `foldseek-3di`. Its sha256 "
        "identifies it uniquely; the local path is kept out of the report so it is the same on "
        "every machine.",
        "",
        "| | |",
        "|---|---|",
        f"| found via | {foldseek.found_via} |",
        f"| version | `{foldseek.version}` |",
        f"| sha256 | `{foldseek.sha256}` |",
        "",
        "## Documented exceptions",
        "",
        "Listed in `structure_exceptions` (docs/adr/0003). No structure features or 3Di are "
        "written for them; sequence-only models still use them.",
        "",
    ]
    if len(exc):
        lines += [
            "| assay_id | documented reason | observed failure | variants |",
            "|---|---|---|---:|",
        ]
        lines += [
            f"| {r.assay_id} | {r.documented_reason} | {r.observed_failure} | {r.variants:,} |"
            for r in exc.itertuples(index=False)
        ]
    else:
        lines.append("None.")

    partial = ok[ok["variants_with_structure"] < ok["variants"]]
    lines += [
        "",
        "## Assays where some variants fall outside the structure",
        "",
        "The structure models only part of `target_seq` (`pdb_range`), so variants outside it "
        "have no structure features.",
        "",
    ]
    lines += _table(partial) if len(partial) else ["None.", ""]

    low = ok[ok["mean_plddt"] < low_plddt_threshold]
    lines += [f"## Assays with mean pLDDT below {threshold}", ""]
    lines += _table(low) if len(low) else ["None.", ""]

    lines += ["## Per assay", "", f"`low pLDDT` is the share of residues below {threshold}.", ""]
    lines += _table(ok)
    return "\n".join(lines)


def _table(df: pd.DataFrame) -> list[str]:
    rows = [
        "| assay_id | pdb_file | range | length | residues | mean pLDDT | low pLDDT "
        "| mean rel SASA | mean contacts | variants (with structure) |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in df.itertuples(index=False):
        rows.append(
            f"| {r.assay_id} | {r.pdb_file} | {r.range_start}-{r.range_end} | "
            f"{r.target_length:,} | {r.residues:,} | {r.mean_plddt:.1f} | "
            f"{r.frac_low_plddt:.2f} | {r.mean_rel_sasa:.2f} | {r.mean_contact_count:.1f} | "
            f"{r.variants:,} ({r.variants_with_structure:,}) |"
        )
    return [*rows, ""]
