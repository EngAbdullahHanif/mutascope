"""A tiny processed dataset (two assays, one with structure) for the EDA and publish tests.

The ``processed_root`` fixture returns a directory laid out like ``make pipeline`` output, with
every config path relative to it (see the ``processed_overrides`` fixture):

- ``DMS_substitutions.csv``: the reference file (``data.raw_dir=.``)
- ``processed/{variants,structure_features,three_di}/assay_id=<id>/data.parquet``
- ``results/``: cleaning report and a stub structure report
"""

import csv
import sys
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from mutascope.data.clean import clean_dataset
from mutascope.data.report import write_report
from mutascope.data.schemas import StructureFeatureSchema, ThreeDiSchema
from mutascope.utils.io import write_partition

TARGET = "MKVAR"
REFERENCE_COLUMNS = {
    "UniProt_ID": "P1",
    "taxon": "Human",
    "source_organism": "Homo sapiens",
    "molecule_name": "Toy",
    "selection_assay": "growth",
    "selection_type": "Growth",
    "DMS_binarization_method": "median",
    "first_author": "Doe",
    "year": "2020",
    "jo": "Journal",
    "pdb_file": "TOY.pdb",
    "pdb_range": "1-5",
}


@pytest.fixture
def processed_overrides() -> list[str]:
    """Hydra overrides pointing every data path into a ``processed_root`` directory."""
    return ["data.data_dir=.", "data.raw_dir=.", "data.processed_dir=processed",
            "data.reports_dir=results"]  # fmt: skip


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> Path:
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    return path


@pytest.fixture
def processed_root(tmp_path: Path) -> Path:
    """Assay ``A`` (Stability): 4 single mutants and a structure; ``B`` (Binding): 2 single
    mutants, 1 multi-mutant and no structure (like a documented exception)."""
    dms = tmp_path / "dms"
    dms.mkdir()
    header = ["mutant", "mutated_sequence", "DMS_score", "DMS_score_bin"]
    _write_csv(
        dms / "A.csv",
        header,
        [
            ["M1A", "AKVAR", "-2.0", "0"],
            ["K2C", "MCVAR", "-1.0", "0"],
            ["V3G", "MKGAR", "0.5", "1"],
            ["A4C", "MKVCR", "1.5", "1"],
        ],
    )
    _write_csv(
        dms / "B.csv",
        header,
        [
            ["M1A", "AKVAR", "3.0", "1"],
            ["R5W", "MKVAW", "1.0", "0"],
            ["M1A:K2C", "ACVAR", "0", "0"],
        ],
    )
    ref_header = [
        "DMS_id",
        "target_seq",
        "seq_len",
        "coarse_selection_type",
        "DMS_number_single_mutants",
        "DMS_binarization_cutoff",
        *REFERENCE_COLUMNS,
    ]
    reference = _write_csv(
        tmp_path / "DMS_substitutions.csv",
        ref_header,
        [
            ["A", TARGET, "5", "Stability", "4", "0.0", *REFERENCE_COLUMNS.values()],
            ["B", TARGET, "5", "Binding", "2", "2.0", *REFERENCE_COLUMNS.values()],
        ],
    )
    processed_dir = tmp_path / "processed"
    variants = processed_dir / "variants"
    reports = tmp_path / "results"
    write_report(clean_dataset(reference, dms, variants), reports)
    (reports / "structure_report.parquet").write_bytes(b"stub")
    (reports / "structure_report.md").write_text("# stub\n")

    positions = list(range(len(TARGET)))
    features = pd.DataFrame(
        {
            "assay_id": ["A"] * len(TARGET),
            "position": positions,
            "residue": list(TARGET),
            "plddt": [90.0, 85.0, 60.0, 95.0, 50.0],
            "sasa": [10.0] * len(TARGET),
            "rel_sasa": [0.1] * len(TARGET),
            "contact_count": [3, 4, 5, 4, 2],
        }
    )
    three_di = pd.DataFrame(
        {"assay_id": ["A"] * 5, "position": positions, "residue": list(TARGET)}
    ).assign(three_di=list("DDVVL"))
    with duckdb.connect() as con:
        for name, frame, schema in (
            ("structure_features", features, StructureFeatureSchema),
            ("three_di", three_di, ThreeDiSchema),
        ):
            (processed_dir / name).mkdir(parents=True)
            write_partition(con, schema.validate(frame), processed_dir / name / "assay_id=A")
    return tmp_path


@pytest.fixture(autouse=True)
def one_openmp_runtime(request: pytest.FixtureRequest) -> Iterator[None]:
    """LightGBM and torch never share a test process (ADR 0022).

    On macOS their OpenMP runtimes crash when both are loaded, in either order, as a segfault
    somewhere later. This turns that into a clear failure at the test that broke the rule.
    """
    marked = "lightgbm" in request.keywords
    if marked and "torch" in sys.modules:
        pytest.fail("torch is loaded: run LightGBM tests in their own process (`make test`)")
    yield
    if not marked and "lightgbm" in sys.modules:
        pytest.fail("this test loaded LightGBM: mark it @pytest.mark.lightgbm (ADR 0022)")
