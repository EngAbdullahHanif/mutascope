"""ProteinGym's supervised CV folds, joined to the processed single mutants (ADR 0019).

ProteinGym ships one ``cv_folds_singles_substitutions/<DMS_id>.csv`` per assay with three fold
columns (``fold_random_5``, ``fold_modulo_5``, ``fold_contiguous_5``). Only the ``mutant`` key
and the folds are read: the files also carry ``DMS_score`` and ``DMS_score_bin``, but those
are printed with one digit fewer than the DMS files and the labels are empty in 6 assays
(ADR 0005), so scores and labels always come from the processed variants.

The join is checked, not trusted: every processed variant must get exactly one fold per
scheme, and the fold files must hold no variant the processed table lacks. A mismatch fails
the run with every offending assay listed; nothing is dropped or filled in.
"""

from collections.abc import Iterable
from pathlib import Path

import duckdb
import pandas as pd

from mutascope.data.db import literal
from mutascope.data.schemas import CV_SCHEMES, FoldAssignmentSchema
from mutascope.utils.io import fresh_tmp_dir, replace_dir, write_partition
from mutascope.utils.logging import get_logger

log = get_logger(__name__)

PROTEINGYM_FOLD_COLUMNS = {f"fold_{scheme}_5": f"fold_{scheme}" for scheme in CV_SCHEMES}
"""ProteinGym's fold column -> ours."""


class FoldJoinError(ValueError):
    """The fold files do not assign exactly one fold per scheme to every processed variant."""


def read_fold_file(path: Path) -> pd.DataFrame:
    """``mutant`` and the three fold columns of one ProteinGym fold file, renamed to ours.

    Read as text and cast, so a malformed fold fails loudly instead of becoming NaN.
    """
    with duckdb.connect() as con:
        columns = ", ".join(
            f'CAST("{theirs}" AS BIGINT) AS {ours}'
            for theirs, ours in PROTEINGYM_FOLD_COLUMNS.items()
        )
        return con.sql(
            f"SELECT mutant, {columns} "
            f"FROM read_csv({literal(str(path))}, header = true, all_varchar = true)"
        ).df()


def join_folds(assay_id: str, variants: pd.DataFrame, folds: pd.DataFrame) -> pd.DataFrame:
    """One assay's fold table: each processed variant with its fold in every scheme.

    Args:
        assay_id: For error messages and the output's ``assay_id`` column.
        variants: The assay's processed variants (``mutant``, ``position``).
        folds: The assay's fold file, as read by :func:`read_fold_file`.

    Returns:
        A frame valid under :class:`FoldAssignmentSchema`, in ``variants`` order.

    Raises:
        FoldJoinError: A mutant is repeated in the fold file, a variant has no fold or a null
            fold, or the fold file has mutants the processed table lacks.
    """
    problems: list[str] = []
    repeated = sorted(set(folds.loc[folds["mutant"].duplicated(), "mutant"]))
    if repeated:
        problems.append(f"{len(repeated)} mutants repeated in the fold file, e.g. {repeated[:3]}")
    missing = sorted(set(variants["mutant"]) - set(folds["mutant"]))
    if missing:
        problems.append(f"{len(missing)} variants without a fold, e.g. {missing[:3]}")
    extra = sorted(set(folds["mutant"]) - set(variants["mutant"]))
    if extra:
        problems.append(f"{len(extra)} fold-file mutants not in the variants, e.g. {extra[:3]}")
    fold_columns = list(PROTEINGYM_FOLD_COLUMNS.values())
    null = folds[folds[fold_columns].isna().any(axis=1)]
    if not null.empty:
        problems.append(f"{len(null)} mutants with a null fold, e.g. {list(null['mutant'][:3])}")
    if problems:
        raise FoldJoinError(f"{assay_id}: " + "; ".join(problems))
    joined = variants[["mutant", "position"]].merge(
        folds, on="mutant", how="left", validate="one_to_one"
    )
    joined.insert(0, "assay_id", assay_id)
    return joined


def build_folds(
    variants_dir: Path, folds_dir: Path, out_dir: Path, assay_ids: Iterable[str] | None = None
) -> pd.DataFrame:
    """Write ``out_dir/assay_id=<id>/data.parquet`` fold tables for every processed assay.

    Every assay is checked before anything is written; a failure lists all failing assays.
    The partitions are built in ``<out_dir>.tmp`` and swapped in at the end, so ``out_dir``
    only ever holds a complete, validated table.

    Args:
        variants_dir: Processed variant partitions (``mutascope data clean``).
        folds_dir: ProteinGym's ``cv_folds_singles_substitutions`` directory.
        out_dir: Where the fold partitions go.
        assay_ids: Restrict to these assays (default: every processed assay).

    Returns:
        Per assay: variants and the number of distinct folds in each scheme.

    Raises:
        FoldJoinError: Any assay fails the checks of :func:`join_folds`, or has no fold file.
    """
    pattern = literal(str(variants_dir.resolve() / "*" / "*.parquet"))
    with duckdb.connect() as con:
        variants = con.sql(
            f"SELECT assay_id, mutant, position FROM read_parquet({pattern}, "
            "hive_partitioning = true, hive_types = {'assay_id': 'VARCHAR'}) "
            "ORDER BY assay_id, position, mutant"
        ).df()
    wanted = sorted(set(variants["assay_id"])) if assay_ids is None else sorted(set(assay_ids))
    tables: dict[str, pd.DataFrame] = {}
    errors: list[str] = []
    for assay_id in wanted:
        path = folds_dir / f"{assay_id}.csv"
        assay = variants[variants["assay_id"] == assay_id]
        if assay.empty:
            errors.append(f"{assay_id}: no processed variants")
            continue
        if not path.is_file():
            errors.append(f"{assay_id}: no fold file {path}")
            continue
        try:
            tables[assay_id] = join_folds(assay_id, assay, read_fold_file(path))
        except FoldJoinError as exc:
            errors.append(str(exc))
    if errors:
        raise FoldJoinError(f"{len(errors)} assays failed the fold join:\n" + "\n".join(errors))

    tmp = fresh_tmp_dir(out_dir)
    with duckdb.connect() as con:
        for assay_id, table in tables.items():
            validated = FoldAssignmentSchema.validate(table, lazy=True)
            write_partition(con, validated, tmp / f"assay_id={assay_id}")
    replace_dir(tmp, out_dir)
    report = pd.DataFrame(
        [
            {"assay_id": assay_id, "variants": len(table)}
            | {f"folds_{scheme}": int(table[f"fold_{scheme}"].nunique()) for scheme in CV_SCHEMES}
            for assay_id, table in tables.items()
        ]
    )
    log.info("folds_written", assays=len(tables), variants=int(report["variants"].sum()))
    return report


def load_cv_table(
    variants_dir: Path, folds_dir: Path, assay_ids: Iterable[str] | None = None
) -> pd.DataFrame:
    """Processed variants (keys and labels) with their folds, for cross-validation.

    Labels come from ``variants_dir``, folds from ``folds_dir`` (written by
    :func:`build_folds`). The two must cover the same variants; that was checked when the
    folds were written and is checked again here, so a stale fold table cannot silently drop
    or duplicate variants.

    Returns:
        One row per variant, sorted by ``assay_id, position, mutant``: ``assay_id, mutant,
        position, wt_aa, mt_aa, DMS_score, DMS_score_bin`` and ``fold_<scheme>`` per scheme.

    Raises:
        FoldJoinError: The two tables do not hold the same variants.
    """
    hive = "hive_partitioning = true, hive_types = {'assay_id': 'VARCHAR'}"
    variants = literal(str(variants_dir.resolve() / "*" / "*.parquet"))
    folds = literal(str(folds_dir.resolve() / "*" / "*.parquet"))
    fold_columns = ", ".join(f"f.fold_{scheme}" for scheme in CV_SCHEMES)
    where = ""
    if assay_ids is not None:
        # USING merges the two key columns into one unqualified assay_id.
        where = "WHERE assay_id IN (" + ", ".join(literal(a) for a in sorted(assay_ids)) + ")"
    with duckdb.connect() as con:
        con.execute(f"CREATE VIEW v AS SELECT * FROM read_parquet({variants}, {hive})")
        con.execute(f"CREATE VIEW f AS SELECT * FROM read_parquet({folds}, {hive})")
        table = con.sql(
            "SELECT assay_id, mutant, v.position, v.wt_aa, v.mt_aa, v.DMS_score, "
            f"v.DMS_score_bin, {fold_columns}, f.position AS fold_position "
            f"FROM v FULL OUTER JOIN f USING (assay_id, mutant) {where} "
            "ORDER BY assay_id, v.position, mutant"
        ).df()
    unmatched = table[table["DMS_score"].isna() | table["fold_random"].isna()]
    if not unmatched.empty:
        by_assay = unmatched.groupby("assay_id").size().to_dict()
        raise FoldJoinError(f"variants and folds disagree (unmatched rows per assay): {by_assay}")
    if not (table["position"] == table["fold_position"]).all():
        raise FoldJoinError("fold table positions differ from the processed variants")
    if assay_ids is not None:
        missing = sorted(set(assay_ids) - set(table["assay_id"]))
        if missing:
            raise FoldJoinError(f"assays without variants: {missing}")
    table = table.drop(columns="fold_position")
    for scheme in CV_SCHEMES:
        table[f"fold_{scheme}"] = table[f"fold_{scheme}"].astype("int64")
    table["position"] = table["position"].astype("int64")
    return table.reset_index(drop=True)
