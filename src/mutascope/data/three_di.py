"""Foldseek 3Di state of every residue of each assay's AF2 structure.

3Di (van Kempen et al. 2023, Nat. Biotechnol. 42:243) describes each residue's local tertiary
interaction with one of 20 letters. SaProt combines it with the amino acid into one token, so
these strings are what the structure-aware scorer will consume.

``foldseek structureto3didescriptor`` is run once over the PDBs of all mapped assays, with the
same options as the official SaProt repository's ``get_struc_seq``
(https://github.com/westlake-repl/SaProt, ``utils/foldseek_util.py``): ``--chain-name-mode 1``.
Its output has one line per chain: ``name``, amino-acid sequence, 3Di sequence, then
descriptor values we ignore. Foldseek's own amino-acid sequence must equal the assay's
``target_seq`` range, a third sequence check independent of our PDB parsers.

The 3Di letters depend on the Foldseek build, so the binary's version is pinned in config and
checked before running. Masking low-pLDDT residues (``#``) is SaProt's tokenisation, not
Foldseek's, and is done by the scorer from the pLDDT in the structure-features table.
"""

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from omegaconf import DictConfig, OmegaConf

from mutascope.data.schemas import ThreeDiSchema
from mutascope.data.structures import StructureCheck, StructureMapping
from mutascope.utils.config import as_str_dict
from mutascope.utils.io import fresh_tmp_dir, replace_dir, write_partition
from mutascope.utils.logging import get_logger

log = get_logger(__name__)

CHAIN = "A"
"""ProteinGym AF2 structures are single-chain, chain A (``structures.pdb_sequence`` checks one)."""


class FoldseekError(RuntimeError):
    """Foldseek is missing, is the wrong version, failed, or produced unexpected output."""


@dataclass(frozen=True)
class FoldseekConfig:
    """From the ``structure.foldseek`` config group."""

    bin: str
    version: str
    threads: int

    @classmethod
    def from_config(cls, cfg: DictConfig) -> "FoldseekConfig":
        """Build from the resolved ``structure.foldseek`` config group."""
        values = as_str_dict(OmegaConf.to_container(cfg, resolve=True), "structure.foldseek")
        return cls(str(values["bin"]), str(values["version"]), int(values["threads"]))


@dataclass(frozen=True)
class Descriptor:
    """Foldseek's per-residue output for one chain."""

    sequence: str
    three_di: str


def check_foldseek(config: FoldseekConfig) -> Path:
    """Resolve the Foldseek binary and require the pinned version.

    Returns:
        The absolute path of the binary.
    """
    found = shutil.which(config.bin)
    if found is None:
        raise FoldseekError(
            f"foldseek not found ({config.bin!r}); install release 10-941cd33 or set FOLDSEEK_BIN"
        )
    result = subprocess.run([found, "version"], capture_output=True, text=True, check=False)
    version = result.stdout.strip()
    if result.returncode != 0 or version != config.version:
        raise FoldseekError(
            f"{found} reports version {version!r}, config pins {config.version!r}: 3Di tokens "
            "may differ between builds"
        )
    return Path(found)


def run_foldseek(binary: Path, pdb_paths: list[Path], threads: int) -> dict[str, Descriptor]:
    """Run ``structureto3didescriptor`` on ``pdb_paths``.

    Returns:
        PDB file name -> its chain-A descriptor.
    """
    with tempfile.TemporaryDirectory(prefix="foldseek_") as work:
        out = Path(work) / "descriptors.tsv"
        cmd = [
            str(binary),
            "structureto3didescriptor",
            *(str(p) for p in pdb_paths),
            str(out),
            "--chain-name-mode", "1",
            "--threads", str(threads),
            "-v", "1",
        ]  # fmt: skip
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise FoldseekError(f"foldseek exited {result.returncode}: {result.stderr.strip()}")
        descriptors = parse_descriptors(out.read_text())
    missing = sorted({p.name for p in pdb_paths} - descriptors.keys())
    if missing:
        raise FoldseekError(f"foldseek produced no chain {CHAIN} descriptor for {missing}")
    return descriptors


def parse_descriptors(text: str) -> dict[str, Descriptor]:
    """Parse ``structureto3didescriptor`` output written with ``--chain-name-mode 1``.

    Names are ``<file name>_<chain>``, followed by the PDB's TITLE after a space when it has
    one (e.g. ``AICDA_HUMAN.pdb_A ALPHAFOLD MONOMER V2.0 PREDICTION FOR ...``); like SaProt,
    only the first word is used. Any chain other than A, a repeated name, or sequences of
    unequal length is an error.
    """
    descriptors: dict[str, Descriptor] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        fields = line.split("\t")
        if len(fields) < 3:
            raise FoldseekError(f"line {line_number}: expected >= 3 tab-separated fields")
        description, sequence, three_di = fields[:3]
        name = description.split(" ", 1)[0]
        file_name, sep, chain = name.rpartition("_")
        if not sep or chain != CHAIN:
            raise FoldseekError(f"line {line_number}: {name!r} is not chain {CHAIN}")
        if file_name in descriptors:
            raise FoldseekError(f"line {line_number}: {file_name} appears twice")
        if len(sequence) != len(three_di):
            raise FoldseekError(
                f"{file_name}: {len(sequence)} residues but {len(three_di)} 3Di states"
            )
        descriptors[file_name] = Descriptor(sequence, three_di)
    return descriptors


def assay_three_di(mapping: StructureMapping, descriptor: Descriptor) -> pd.DataFrame:
    """One assay's 3Di states at its ``target_seq`` positions.

    Raises:
        FoldseekError: Foldseek's amino-acid sequence differs from the target range.
    """
    expected = mapping.target_seq[mapping.offset : mapping.range_end]
    if descriptor.sequence != expected:
        raise FoldseekError(
            f"{mapping.assay_id}: foldseek sequence of {mapping.pdb_path.name} "
            f"({len(descriptor.sequence)} residues) differs from target_seq "
            f"{mapping.range_start}-{mapping.range_end}"
        )
    n = len(expected)
    return pd.DataFrame(
        {
            "assay_id": pd.array([mapping.assay_id] * n, dtype="str"),
            "position": np.arange(mapping.offset, mapping.offset + n, dtype=np.int64),
            "residue": pd.array(list(expected), dtype="str"),
            "three_di": pd.array(list(descriptor.three_di), dtype="str"),
        }
    )


def build_three_di(check: StructureCheck, out_dir: Path, config: FoldseekConfig) -> int:
    """Write ``out_dir/assay_id=<id>/data.parquet`` (3Di per residue) for every mapped assay.

    Partitions are validated against :class:`ThreeDiSchema` and built in ``<out_dir>.tmp``,
    which replaces ``out_dir`` only after every assay has succeeded.

    Returns:
        The number of residue rows written.
    """
    binary = check_foldseek(config)
    pdb_paths = sorted({m.pdb_path for m in check.mappings})
    descriptors = run_foldseek(binary, pdb_paths, config.threads)

    tmp = fresh_tmp_dir(out_dir)
    rows = 0
    try:
        with duckdb.connect() as con:
            for mapping in check.mappings:
                table = assay_three_di(mapping, descriptors[mapping.pdb_path.name])
                ThreeDiSchema.validate(table, lazy=True)
                write_partition(con, table, tmp / f"assay_id={mapping.assay_id}")
                rows += len(table)
    except BaseException:
        shutil.rmtree(tmp)
        raise
    replace_dir(tmp, out_dir)
    log.info(
        "three_di_written",
        assays=len(check.mappings),
        pdb_files=len(pdb_paths),
        residues=rows,
        skipped_exceptions=len(check.allowed_exceptions),
        foldseek=str(binary),
        out_dir=str(out_dir),
    )
    return rows
