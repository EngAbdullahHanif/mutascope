"""Read ProteinGym's own artefacts: precomputed baseline scores and the published leaderboard.

These are inputs to the reproduction gate (ADR 0010), not to MutaScope's models. The precomputed
scores are the only place multi-mutant rows are read, and only by the harness gate.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import duckdb
import pandas as pd

from mutascope.data.db import literal

RowSet = Literal["all", "single"]
"""``all``: every row ProteinGym scores (singles and multi-mutants), as on the leaderboard.
``single``: rows whose ``mutant`` has no ``:``. On the pinned release these are exactly the
696,311 processed single mutants: same keys and ``DMS_score_bin``. ``DMS_score`` is the same
number printed with different rounding in 3,297 rows of 82 assays (largest difference 3.7e-09),
which can make or break a tie (ADR 0014)."""

PRECOMPUTED_PREFIX = "proteingym:"
"""Model-name prefix for ProteinGym's precomputed scores in results, so they can never be
confused with MutaScope's own scores from the same architecture."""


def load_precomputed_scores(scores_dir: Path, models: list[str], rows: RowSet) -> pd.DataFrame:
    """ProteinGym's precomputed zero-shot scores for ``models``, with the assay labels.

    Args:
        scores_dir: ``assay_id=<id>/*.parquet`` partitions written by ``mutascope data
            download`` from ``zero_shot_substitutions_scores.zip``.
        models: Score columns to read, e.g. ``["ESM2_650M"]``.
        rows: Which rows to keep (:data:`RowSet`).

    Returns:
        Columns ``assay_id, mutant, DMS_score, DMS_score_bin`` and one per model, sorted by
        assay and mutant.
    """
    pattern = literal(str(scores_dir.resolve() / "*" / "*.parquet"))
    columns = ", ".join(f'"{m}"' for m in models)
    where = "WHERE NOT contains(mutant, ':')" if rows == "single" else ""
    query = (
        f"SELECT assay_id, mutant, DMS_score, DMS_score_bin, {columns} "
        f"FROM read_parquet({pattern}, hive_partitioning = true, "
        "hive_types = {'assay_id': 'VARCHAR'}) "
        f"{where} ORDER BY assay_id, mutant"
    )
    with duckdb.connect() as con:
        return con.execute(query).df()


def published_summary(path: Path, names: Mapping[str, str]) -> pd.DataFrame:
    """Leaderboard aggregates from ``Summary_performance_DMS_substitutions_<Metric>.csv``.

    Args:
        path: The published summary file.
        names: Score column -> ``Model_name`` on the leaderboard, e.g.
            ``{"ESM2_650M": "ESM2 (650M)"}``.

    Returns:
        Columns ``model`` (score column), ``level`` (``overall`` / ``function``), ``group``
        (function type, or null) and ``value``, as published (rounded to 3 decimals).
    """
    table = pd.read_csv(path).set_index("Model_name")
    _require(table.index, names, path)
    metric = next(c for c in table.columns if c.startswith("Average_"))
    records: list[dict[str, object]] = []
    for model, name in names.items():
        row = table.loc[name]
        records.append({"model": model, "level": "overall", "group": None, "value": row[metric]})
        for column in table.columns:
            if column.startswith("Function_"):
                group = column.removeprefix("Function_")
                records.append(
                    {"model": model, "level": "function", "group": group, "value": row[column]}
                )
    return pd.DataFrame.from_records(records).astype({"group": "string"})


def published_assay_level(path: Path, names: Mapping[str, str]) -> pd.DataFrame:
    """Per-assay values from ``DMS_substitutions_<Metric>_DMS_level.csv``.

    Returns:
        Columns ``model`` (score column), ``assay_id`` and ``value`` (rounded to 3 decimals
        upstream).
    """
    table = pd.read_csv(path).rename(columns={"DMS ID": "assay_id"})
    _require(table.columns, names, path)
    long = table.melt(
        id_vars="assay_id", value_vars=list(names.values()), var_name="name", value_name="value"
    )
    by_name = {name: model for model, name in names.items()}
    long["model"] = long["name"].map(by_name)
    return long[["model", "assay_id", "value"]]


def compare_with_published(ours: pd.DataFrame, published: pd.DataFrame) -> pd.DataFrame:
    """Join our aggregates with the published ones.

    Args:
        ours: :class:`~mutascope.eval.schemas.SummarySchema` rows for one metric, models named
            ``proteingym:<score column>``.
        published: :func:`published_summary` rows for the same metric.

    Returns:
        Columns ``model, level, group, ours, published, delta`` (``ours - published``), one row
        per published value.
    """
    mine = ours.assign(model=ours["model"].str.removeprefix(PRECOMPUTED_PREFIX))
    mine = mine[["model", "level", "group", "value"]].rename(columns={"value": "ours"})
    theirs = published.rename(columns={"value": "published"})
    for frame in (mine, theirs):
        frame["group"] = frame["group"].astype("string").fillna("")
    joined = theirs.merge(mine, on=["model", "level", "group"], how="left", validate="1:1")
    joined["group"] = joined["group"].replace("", pd.NA)
    joined["delta"] = joined["ours"] - joined["published"]
    return joined[["model", "level", "group", "ours", "published", "delta"]]


def _require(available: pd.Index, names: Mapping[str, str], path: Path) -> None:
    missing = sorted(set(names.values()) - set(available))
    if missing:
        raise KeyError(f"{path.name} has no model named {missing}")
