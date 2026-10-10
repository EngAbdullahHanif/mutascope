"""EDA summaries and charts over a toy processed dataset (see tests/conftest.py)."""

from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from mutascope.eda import charts
from mutascope.eda.summary import (
    assay_overview,
    assay_score_histogram,
    connect_processed,
    function_type_summary,
    standardized_score_histogram,
)

CONF = Path(__file__).parents[2] / "conf"


@pytest.fixture
def con(
    processed_root: Path, processed_overrides: list[str]
) -> Iterator[duckdb.DuckDBPyConnection]:
    with connect_processed(CONF, processed_root, processed_overrides) as connection:
        yield connection


def test_assay_overview(con: duckdb.DuckDBPyConnection) -> None:
    overview = assay_overview(con).set_index("assay_id")
    assert overview["singles"].to_dict() == {"A": 4, "B": 2}
    assert overview["multi_mutants"].to_dict() == {"A": 0, "B": 1}
    assert overview.loc["A", "coverage"] == pytest.approx(4 / (19 * 5))
    assert overview["frac_fit"].to_dict() == {"A": 0.5, "B": 0.5}
    assert overview["positions_with_structure"].to_dict() == {"A": 5, "B": 0}
    assert overview.loc["B", "score_median"] == pytest.approx(2.0)
    assert not overview["needs_windows"].any()


def test_function_type_summary(con: duckdb.DuckDBPyConnection) -> None:
    summary = function_type_summary(assay_overview(con))
    assert summary.to_dict("records") == [
        {"function_type": "Binding", "assays": 1, "proteins": 1, "singles": 2},
        {"function_type": "Stability", "assays": 1, "proteins": 1, "singles": 4},
    ]


def test_standardized_histogram_is_a_density_per_function_type(
    con: duckdb.DuckDBPyConnection,
) -> None:
    hist = standardized_score_histogram(con, bins=10, clip=2.0)
    hist["mass"] = hist["density"] * (hist["bin_end"] - hist["bin_start"])
    assert hist.groupby("function_type")["mass"].sum().to_dict() == pytest.approx(
        {"Binding": 1.0, "Stability": 1.0}
    )
    assert hist["bin_start"].min() >= -2.0
    assert hist["bin_end"].max() <= 2.0 + 1e-9


def test_assay_score_histogram_counts_every_variant_by_label(
    con: duckdb.DuckDBPyConnection,
) -> None:
    hist = assay_score_histogram(con, "A", bins=4)
    assert hist["count"].sum() == 4
    assert hist.groupby("label")["count"].sum().to_dict() == {"fit (1)": 2, "unfit (0)": 2}
    # The maximum score lands in the last bin, not one past it.
    assert hist["bin_end"].max() == pytest.approx(1.5)
    assert assay_score_histogram(con, "missing").empty


def test_charts_build_valid_specs(con: duckdb.DuckDBPyConnection) -> None:
    overview = assay_overview(con)
    specs = [
        charts.assay_sizes(overview),
        charts.function_types(function_type_summary(overview)),
        charts.standardized_scores(standardized_score_histogram(con)),
        charts.assay_scores(assay_score_histogram(con, "A"), "A", 0.0),
        charts.assay_scores(assay_score_histogram(con, "A"), "A", None),
        charts.fraction_fit(overview),
        charts.sequence_lengths(overview),
        charts.coverage(overview),
    ]
    for chart in specs:
        assert chart.to_dict()["$schema"].startswith("https://vega.github.io/schema/vega-lite/")


def test_connect_processed_does_not_depend_on_cwd(
    processed_root: Path,
    processed_overrides: list[str],
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The notebook passes the repo root; config paths resolve against it, not the cwd."""
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))
    with connect_processed(CONF, processed_root, processed_overrides) as con:
        assert con.sql("SELECT count(*) FROM variants").fetchone() == (6,)
        assert con.sql("SELECT count(*) FROM structure_features").fetchone() == (5,)
