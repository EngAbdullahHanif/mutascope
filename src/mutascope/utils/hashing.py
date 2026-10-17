"""Streaming checksums for large files, and a stable hash of a resolved config."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CHUNK_SIZE = 1 << 20


@dataclass(frozen=True)
class Digests:
    """Checksums of one byte stream."""

    sha256: str
    md5: str
    size_bytes: int


class DigestAccumulator:
    """Computes sha256 and md5 in a single pass over chunks.

    Both are needed because upstream (Zenodo) publishes md5 while our manifest records sha256;
    hashing once while streaming avoids reading a 1.9 GB download twice.
    """

    def __init__(self) -> None:
        self._sha256 = hashlib.sha256()
        self._md5 = hashlib.md5(usedforsecurity=False)
        self._size = 0

    def update(self, chunk: bytes) -> None:
        """Feed the next chunk of the stream."""
        self._sha256.update(chunk)
        self._md5.update(chunk)
        self._size += len(chunk)

    def result(self) -> Digests:
        """Return the digests of everything fed so far."""
        return Digests(self._sha256.hexdigest(), self._md5.hexdigest(), self._size)


def file_digests(path: Path) -> Digests:
    """Return sha256, md5 and size of a file, reading it in 1 MiB chunks."""
    acc = DigestAccumulator()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK_SIZE):
            acc.update(chunk)
    return acc.result()


def config_sha256(config: Mapping[str, Any]) -> str:
    """sha256 of a resolved config as canonical JSON (sorted keys), to stamp outputs with it."""
    text = json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()
