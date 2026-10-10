"""Stage the pipeline's inputs in one fixed layout, from the full release or a few assays.

Every step after the download reads the same three inputs::

    <dir>/DMS_substitutions.csv   reference file, one row per assay
    <dir>/dms/                    <DMS_id>.csv per assay
    <dir>/structures/             AF2 PDBs named in the reference's pdb_file

:func:`link_release` points this layout at the verified raw download; :func:`subset_release`
copies a few named assays into it for the fast ``test`` profile. Nothing downstream can tell
the two apart. Test assays are named explicitly rather than picked by a rule, so a test run
always covers the same data.
"""

import csv
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from mutascope.data.release import Release
from mutascope.utils.logging import get_logger

log = get_logger(__name__)

REFERENCE_NAME = "DMS_substitutions.csv"
DMS_DIRNAME = "dms"
STRUCTURES_DIRNAME = "structures"


# Config keys (conf/data/proteingym.yaml `files`) of the release files the layout points at.
REFERENCE_KEY = "reference"
DMS_KEY = "dms_substitutions"
STRUCTURES_KEY = "af2_structures"


class SubsetError(ValueError):
    """The requested subset cannot be written exactly as asked."""


def link_release(release: Release, out_dir: Path) -> None:
    """Symlink the staged layout in ``out_dir`` to the downloaded release.

    Call only after the release is verified. Links point at absolute paths, so they stay valid
    wherever the pipeline stages them; an existing entry in ``out_dir`` is an error, not replaced.
    """
    raw_dir = release.raw_dir.resolve()
    targets = {
        REFERENCE_NAME: raw_dir / release.file(REFERENCE_KEY).filename,
        DMS_DIRNAME: raw_dir / release.file(DMS_KEY).extract_dirname,
        STRUCTURES_DIRNAME: raw_dir / release.file(STRUCTURES_KEY).extract_dirname,
    }
    missing = [str(t) for t in targets.values() if not t.exists()]
    if missing:
        raise FileNotFoundError(f"release is incomplete: {', '.join(missing)}")
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, target in targets.items():
        (out_dir / name).symlink_to(target, target_is_directory=target.is_dir())
    log.info("release_linked", raw_dir=str(raw_dir), out_dir=str(out_dir))


@dataclass(frozen=True)
class Subset:
    """Where :func:`subset_release` wrote its outputs."""

    reference: Path
    dms_dir: Path
    structures_dir: Path
    pdb_files: int


def subset_release(
    reference_csv: Path,
    dms_dir: Path,
    structures_dir: Path,
    assay_ids: Sequence[str],
    out_dir: Path,
) -> Subset:
    """Write the reference rows, DMS CSVs and PDBs of ``assay_ids`` under ``out_dir``.

    Reference rows keep their original order and every column. A PDB named in ``pdb_file`` is
    copied only if it exists: a missing structure is the structure check's to report, with its
    own context, not this step's.

    Raises:
        SubsetError: An id is not in the reference, is given twice, or has no DMS CSV, or
            ``out_dir`` already has content (a stale subset is never merged into).
    """
    if out_dir.exists() and any(out_dir.iterdir()):
        raise SubsetError(f"{out_dir} is not empty")
    if not assay_ids:
        raise SubsetError("no assays requested")
    duplicates = sorted({a for a in assay_ids if assay_ids.count(a) > 1})
    if duplicates:
        raise SubsetError(f"assays requested more than once: {', '.join(duplicates)}")

    with reference_csv.open(newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = [row for row in reader if row["DMS_id"] in assay_ids]
    unknown = sorted(set(assay_ids) - {row["DMS_id"] for row in rows})
    if unknown:
        raise SubsetError(f"not in {reference_csv.name}: {', '.join(unknown)}")
    no_csv = sorted(a for a in assay_ids if not (dms_dir / f"{a}.csv").is_file())
    if no_csv:
        raise SubsetError(f"no DMS CSV in {dms_dir}: {', '.join(no_csv)}")

    reference_out = out_dir / REFERENCE_NAME
    dms_out = out_dir / DMS_DIRNAME
    structures_out = out_dir / STRUCTURES_DIRNAME
    dms_out.mkdir(parents=True)
    structures_out.mkdir()
    with reference_out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    for assay_id in assay_ids:
        shutil.copyfile(dms_dir / f"{assay_id}.csv", dms_out / f"{assay_id}.csv")

    pdb_names = sorted({row["pdb_file"] for row in rows if row.get("pdb_file")})
    present = [name for name in pdb_names if (structures_dir / name).is_file()]
    for name in present:
        shutil.copyfile(structures_dir / name, structures_out / name)

    log.info("subset_written", assays=len(rows), pdb_files=len(present), out_dir=str(out_dir))
    return Subset(reference_out, dms_out, structures_out, pdb_files=len(present))
