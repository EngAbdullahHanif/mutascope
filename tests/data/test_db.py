"""DuckDB views over the processed data."""

import csv
from pathlib import Path

from mutascope.data.clean import clean_dataset
from mutascope.data.db import connect
from mutascope.data.report import write_report

TARGET = "MKVAR"
HEADER = ["mutant", "mutated_sequence", "DMS_score", "DMS_score_bin"]


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> Path:
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def test_views_join_on_assay_id(tmp_path: Path) -> None:
    dms = tmp_path / "dms"
    dms.mkdir()
    ref = write_csv(tmp_path / "ref.csv", ["DMS_id", "target_seq"], [["A", TARGET], ["B", TARGET]])
    write_csv(dms / "A.csv", HEADER, [["M1A", "AKVAR", "0.5", "1"], ["A4C", "MKVCR", "0.2", "1"]])
    write_csv(dms / "B.csv", HEADER, [["M1A:K2C", "ACVAR", "0.1", "0"]])
    out = tmp_path / "my data" / "variants"  # a space, as in real checkout paths
    parquet, _ = write_report(clean_dataset(ref, dms, out), tmp_path / "results")

    with connect(out, report_parquet=parquet, reference_csv=ref) as con:
        variants = con.sql(
            "SELECT assay_id, mutant, position, DMS_score_bin FROM variants ORDER BY position"
        ).fetchall()
        assert variants == [("A", "M1A", 0, 1), ("A", "A4C", 3, 1)]
        assert con.sql("SELECT assay_id, target_seq FROM assays").fetchall()[0] == ("A", TARGET)
        joined = con.sql(
            "SELECT r.assay_id, r.rows_out, count(v.mutant) FROM cleaning_report r "
            "LEFT JOIN variants v USING (assay_id) GROUP BY ALL ORDER BY 1"
        ).fetchall()
        assert joined == [("A", 2, 2), ("B", 0, 0)]
