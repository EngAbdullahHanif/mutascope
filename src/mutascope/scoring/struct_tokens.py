"""Structure tokens for structure-aware scorers, read from the processed structure tables.

A structure-token string has one character per residue of a target sequence: the residue's
Foldseek 3Di state in lowercase, or ``#`` for "no structure information". ``#`` is used in two
cases:

- the residue's pLDDT is below the threshold (SaProt's rule for AF2 structures: ``plddts <
  plddt_threshold`` in ``utils/foldseek_util.py`` of https://github.com/westlake-repl/SaProt;
  the threshold, 70, comes from the config);
- the residue is outside the range the structure models (three assays have a structure of only
  part of the target).

The 3Di states come from ``processed/three_di`` and the pLDDT from
``processed/structure_features``, both written by the pipeline with the pinned Foldseek build.
Nothing here runs Foldseek. An assay without a structure (the four of ADR 0003) has no entry.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import duckdb

from mutascope.data.db import literal
from mutascope.utils.logging import get_logger

log = get_logger(__name__)

UNKNOWN = "#"
STRUCT_ALPHABET = "pynwrqhgdlvtmfsaeikc" + UNKNOWN
"""SaProt's structure letters in vocabulary order (``foldseek_struc_vocab`` upstream)."""


class StructTokenError(ValueError):
    """The structure tables disagree with each other or with the target sequence."""


@dataclass(frozen=True)
class AssayStructure:
    """Structure tokens of one assay's full target sequence."""

    assay_id: str
    tokens: str
    """One character per target residue; ``#`` outside ``[start, end)`` and at low pLDDT."""
    start: int
    """0-based target index of the first residue the structure models."""
    end: int
    """One past the last residue the structure models."""

    @property
    def low_plddt(self) -> int:
        """Residues inside the structure whose 3Di state is hidden for low pLDDT."""
        return self.tokens[self.start : self.end].count(UNKNOWN)


def check_struct_tokens(tokens: str, length: int) -> None:
    """Fail unless ``tokens`` is one character of :data:`STRUCT_ALPHABET` per residue."""
    if len(tokens) != length:
        raise ValueError(f"{len(tokens)} structure tokens for {length} residues")
    bad = sorted(set(tokens) - set(STRUCT_ALPHABET))
    if bad:
        raise ValueError(f"structure tokens must be lowercase 3Di letters or '#'; found {bad}")


def build_struct_tokens(
    target_length: int,
    positions: Sequence[int],
    three_di: Sequence[str],
    plddt: Sequence[float],
    plddt_threshold: float,
) -> str:
    """Structure tokens of a ``target_length``-residue target from per-residue structure rows.

    Args:
        target_length: Residues in the target sequence.
        positions: 0-based target index of each structure residue.
        three_di: Foldseek 3Di state of each (uppercase, as Foldseek writes it).
        plddt: AF2 pLDDT of each.
        plddt_threshold: A residue strictly below this gets ``#``.

    Raises:
        StructTokenError: A position is outside the target or repeated.
    """
    tokens = [UNKNOWN] * target_length
    seen: set[int] = set()
    for position, state, confidence in zip(positions, three_di, plddt, strict=True):
        if not 0 <= position < target_length or position in seen:
            raise StructTokenError(f"structure residue {position} is outside the target or twice")
        seen.add(position)
        if confidence >= plddt_threshold:
            tokens[position] = state.lower()
    result = "".join(tokens)
    check_struct_tokens(result, target_length)
    return result


def load_struct_tokens(
    three_di_dir: Path,
    structure_features_dir: Path,
    targets: dict[str, str],
    plddt_threshold: float,
) -> dict[str, AssayStructure]:
    """Structure tokens of every assay in ``targets`` that has a structure.

    Args:
        three_di_dir: ``assay_id=<id>/data.parquet`` written by ``mutascope data foldseek-3di``.
        structure_features_dir: The same layout from ``mutascope data structure-features``.
        targets: Assay id -> target sequence.
        plddt_threshold: See :func:`build_struct_tokens`.

    Returns:
        Assay id -> its structure; assays without one are absent.

    Raises:
        StructTokenError: The two tables do not hold the same residues of an assay, or a
            residue differs from the target sequence. Nothing is dropped or repaired.
    """
    query = (
        "SELECT assay_id, position, t.residue AS residue, t.three_di AS three_di, "
        "f.residue AS feature_residue, f.plddt AS plddt "
        f"FROM {_partitions(three_di_dir)} t FULL OUTER JOIN "
        f"{_partitions(structure_features_dir)} f USING (assay_id, position) "
        "ORDER BY assay_id, position"
    )
    with duckdb.connect() as con:
        rows = con.execute(query).df()
    structures: dict[str, AssayStructure] = {}
    for assay_id, table in rows.groupby("assay_id", sort=True):
        assay_id = str(assay_id)
        if assay_id not in targets:
            continue
        target = targets[assay_id]
        if table[["three_di", "plddt"]].isna().to_numpy().any():
            raise StructTokenError(
                f"{assay_id}: 3Di and structure features cover different residues"
            )
        positions = [int(p) for p in table["position"]]
        residues = "".join(table["residue"].astype(str))
        start, end = positions[0], positions[-1] + 1
        if (
            positions != list(range(start, end))
            or end > len(target)
            or residues != target[start:end]
            or residues != "".join(table["feature_residue"].astype(str))
        ):
            raise StructTokenError(
                f"{assay_id}: structure residues {start + 1}-{end} do not match the target sequence"
            )
        tokens = build_struct_tokens(
            len(target),
            positions,
            table["three_di"].astype(str).tolist(),
            [float(p) for p in table["plddt"]],
            plddt_threshold,
        )
        structures[assay_id] = AssayStructure(assay_id, tokens, start, end)
    log.info(
        "struct_tokens_loaded",
        assays=len(structures),
        without_structure=len(targets) - len(structures),
        plddt_threshold=plddt_threshold,
    )
    return structures


def _partitions(directory: Path) -> str:
    """``read_parquet`` over ``assay_id=<id>/*.parquet``, ``assay_id`` read from the path."""
    pattern = literal(str(directory.resolve() / "*" / "*.parquet"))
    return (
        f"read_parquet({pattern}, hive_partitioning = true, hive_types = {{'assay_id': 'VARCHAR'}})"
    )
