"""DuckDB views over the processed data, for notebooks and later pipeline steps.

Views, not tables: they read the Parquet files at query time, so nothing is copied and a view
always reflects what is on disk. Paths are resolved to absolute ones because DuckDB resolves
relative paths against the process's working directory at query time.

Views created by :func:`connect`:

- ``variants``: the processed single mutants, ``assay_id`` taken from the hive partition path.
- ``cleaning_report``: one row per assay (if ``report_parquet`` is given).
- ``assays``: every column of the ProteinGym reference file (if ``reference_csv`` is given),
  with ``DMS_id`` renamed to ``assay_id`` so it joins with the other views. This carries the
  grouping keys for ProteinGym-style aggregation (``UniProt_ID``, ``coarse_selection_type``).
"""

from pathlib import Path

import duckdb


def connect(
    variants_dir: Path,
    *,
    report_parquet: Path | None = None,
    reference_csv: Path | None = None,
) -> duckdb.DuckDBPyConnection:
    """Open an in-memory DuckDB connection with views over the processed data.

    Args:
        variants_dir: Directory of ``assay_id=<id>/data.parquet`` partitions.
        report_parquet: The cleaning report written by ``mutascope data clean``.
        reference_csv: ProteinGym ``DMS_substitutions.csv``.
    """
    con = duckdb.connect()
    pattern = _literal(str(variants_dir.resolve() / "*" / "*.parquet"))
    con.execute(
        f"CREATE VIEW variants AS SELECT * FROM read_parquet({pattern}, "
        "hive_partitioning = true, hive_types = {'assay_id': 'VARCHAR'})"
    )
    if report_parquet is not None:
        report = _literal(str(report_parquet.resolve()))
        con.execute(f"CREATE VIEW cleaning_report AS SELECT * FROM read_parquet({report})")
    if reference_csv is not None:
        ref = _literal(str(reference_csv.resolve()))
        con.execute(
            "CREATE VIEW assays AS SELECT * RENAME (DMS_id AS assay_id) "
            f"FROM read_csv({ref}, header = true)"
        )
    return con


def _literal(text: str) -> str:
    """Quote a string as a SQL literal (paths may contain spaces or quotes)."""
    return "'" + text.replace("'", "''") + "'"
