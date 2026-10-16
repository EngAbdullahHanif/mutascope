"""On-disk cache of per-residue log-probabilities, so a landscape is computed once per model.

An entry is keyed by ``sha256`` of the model id, its pinned revision, the sequence, the
structure tokens, the scoring method and the scoring region (CLAUDE.md, "Scoring rules"). The
method is part of the key because masked and wild-type marginals of the same sequence differ;
without it a fast result could be served for a masked request. The region (``"full"`` or
``"msa:<a>-<b>"``, ADR 0012) is part of it so that benchmark and website landscapes are
separate, labelled entries. The key fields are JSON-encoded before hashing so
that no two different tuples can concatenate to the same string.

Each entry is one ``.npy`` file of shape ``(L, 20)``, float32, columns in ``AA_ORDER``. A row is
NaN until its residue has been scored: the benchmark scores only the positions an assay
measured, and a later request for more positions fills in the missing rows of the same entry
(ADR 0013). Writes go to a temporary file that is renamed into place, so a reader never sees
half an entry, and a run killed at any moment leaves every finished entry intact.

``computations.jsonl`` in the cache root gets one line per computation (entry, positions scored,
seconds). An interrupted run's work is therefore still counted when the total run time is
reported after a resume.
"""

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from mutascope.alphabet import AA_ORDER
from mutascope.utils.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class CacheKey:
    """Everything that determines a model's log-probabilities for one sequence."""

    model_id: str
    revision: str
    sequence: str
    struct_tokens: str | None
    method: str
    """``"masked"`` or ``"wt"`` (wild-type marginal)."""
    region: str
    """Which part of the target ``sequence`` is: ``"full"`` or ``"msa:<a>-<b>"``."""

    def digest(self) -> str:
        """Hex sha256 of the JSON-encoded fields."""
        payload = json.dumps(
            [
                self.model_id,
                self.revision,
                self.sequence,
                self.struct_tokens,
                self.method,
                self.region,
            ]
        )
        return hashlib.sha256(payload.encode()).hexdigest()


class CacheError(RuntimeError):
    """A cache entry exists but does not have the expected shape."""


COMPUTATION_LOG = "computations.jsonl"


class LogProbCache:
    """Directory of ``<sha256>.npy`` entries, sharded by the first two hex digits.

    ``width`` is the number of columns per residue: 20 log-probabilities (the default), or the
    hidden size when the same layout holds per-residue embeddings
    (:mod:`mutascope.features.embeddings`, which keeps them in a cache of their own). None
    accepts any width, for a reader that does not load the model to learn it; the row count is
    still checked.
    """

    def __init__(self, root: Path, width: int | None = len(AA_ORDER)) -> None:
        self.root = root
        self.width = width

    def record(self, key: CacheKey, *, positions: int, seconds: float) -> None:
        """Append one computation (``positions`` residues of ``key``'s entry) to the log."""
        line = {
            "digest": key.digest(),
            "model_id": key.model_id,
            "revision": key.revision,
            "method": key.method,
            "region": key.region,
            "length": len(key.sequence),
            "positions": positions,
            "seconds": round(seconds, 3),
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / COMPUTATION_LOG).open("a") as fh:
            fh.write(json.dumps(line) + "\n")

    def computations(self) -> list[dict[str, Any]]:
        """Every logged computation, oldest first (empty if nothing was computed yet)."""
        path = self.root / COMPUTATION_LOG
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    def path(self, key: CacheKey) -> Path:
        digest = key.digest()
        return self.root / digest[:2] / f"{digest}.npy"

    def get(self, key: CacheKey) -> npt.NDArray[np.float32] | None:
        """The cached ``(L, width)`` array (NaN rows: residues not scored yet), or None if absent.

        Raises:
            CacheError: The entry's shape does not match the key's sequence (never trusted).
        """
        path = self.path(key)
        if not path.exists():
            return None
        array = np.load(path)
        expected = (len(key.sequence), array.shape[-1] if self.width is None else self.width)
        if array.shape != expected or array.dtype != np.float32:
            raise CacheError(f"{path}: {array.dtype}{array.shape}, expected float32{expected}")
        log.debug("cache_hit", model=key.model_id, method=key.method, length=len(key.sequence))
        return array

    def put(self, key: CacheKey, array: npt.NDArray[np.float32]) -> Path:
        """Store ``array`` atomically and return its path."""
        expected = (len(key.sequence), array.shape[-1] if self.width is None else self.width)
        if array.shape != expected:
            raise CacheError(f"refusing to cache shape {array.shape}, expected {expected}")
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".npy.tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                np.save(fh, array.astype(np.float32))
            Path(tmp).replace(path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return path


def embedding_method(layers: int) -> str:
    """The cache key's method for an embedding: the mean over the last ``layers`` layers.

    Here rather than in :mod:`mutascope.features.embeddings`, which imports torch: the feature
    store names its cache entries with it, and training processes must not load torch (ADR
    0022).
    """
    if layers < 1:
        raise ValueError(f"layers must be at least 1, got {layers}")
    return f"embedding_mean_last{layers}"
