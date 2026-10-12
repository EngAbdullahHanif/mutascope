"""Filesystem helpers for writing outputs so a final path is only ever complete."""

import shutil
from pathlib import Path

import duckdb
import pandas as pd
import pandera.pandas as pa

PARTITION_FILE = "data.parquet"


def fresh_tmp_dir(out_dir: Path) -> Path:
    """Create an empty ``<out_dir>.tmp`` sibling to build ``out_dir`` in.

    A leftover from an interrupted run is deleted, never trusted.
    """
    tmp = out_dir.with_name(out_dir.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    return tmp


def replace_dir(tmp: Path, out_dir: Path) -> None:
    """Move a finished ``tmp`` directory to ``out_dir``, replacing any previous output."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    tmp.replace(out_dir)


def write_partition(con: duckdb.DuckDBPyConnection, frame: pd.DataFrame, part_dir: Path) -> None:
    """Write one assay's (already validated) rows to ``part_dir/data.parquet``.

    ``assay_id`` is not stored in the file: it lives in the hive partition path
    (``assay_id=<id>``), which is where DuckDB reads it from.
    """
    part_dir.mkdir()
    con.from_df(frame.drop(columns="assay_id")).write_parquet(str(part_dir / PARTITION_FILE))


def write_table(frame: pd.DataFrame, schema: type[pa.DataFrameModel], path: Path) -> Path:
    """Validate ``frame`` against ``schema`` and write it to one Parquet file; returns ``path``."""
    validated = schema.validate(frame, lazy=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as con:
        con.from_df(validated).write_parquet(str(path))
    return path
