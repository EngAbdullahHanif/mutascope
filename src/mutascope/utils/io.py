"""Filesystem helpers for writing outputs so a final path is only ever complete."""

import shutil
from pathlib import Path


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
