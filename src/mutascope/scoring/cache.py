"""On-disk cache of per-residue log-probabilities, so a landscape is computed once per model.

An entry is keyed by ``sha256`` of the model id, its pinned revision, the sequence, the
structure tokens and the scoring method (CLAUDE.md, "Scoring rules"). The method is part of the
key because masked and wild-type marginals of the same sequence differ; without it a fast
result could be served for a masked request. The key fields are JSON-encoded before hashing so
that no two different tuples can concatenate to the same string.

Each entry is one ``.npy`` file of shape ``(L, 20)``, float32, columns in ``AA_ORDER``. Writes go
to a temporary file that is renamed into place, so a reader never sees half an entry.
"""

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

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

    def digest(self) -> str:
        """Hex sha256 of the JSON-encoded fields."""
        payload = json.dumps(
            [self.model_id, self.revision, self.sequence, self.struct_tokens, self.method]
        )
        return hashlib.sha256(payload.encode()).hexdigest()


class CacheError(RuntimeError):
    """A cache entry exists but does not have the expected shape."""


class LogProbCache:
    """Directory of ``<sha256>.npy`` entries, sharded by the first two hex digits."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, key: CacheKey) -> Path:
        digest = key.digest()
        return self.root / digest[:2] / f"{digest}.npy"

    def get(self, key: CacheKey) -> npt.NDArray[np.float32] | None:
        """The cached ``(L, 20)`` array, or None if absent.

        Raises:
            CacheError: The entry's shape does not match the key's sequence (never trusted).
        """
        path = self.path(key)
        if not path.exists():
            return None
        array = np.load(path)
        expected = (len(key.sequence), len(AA_ORDER))
        if array.shape != expected or array.dtype != np.float32:
            raise CacheError(f"{path}: {array.dtype}{array.shape}, expected float32{expected}")
        log.debug("cache_hit", model=key.model_id, method=key.method, length=len(key.sequence))
        return array

    def put(self, key: CacheKey, array: npt.NDArray[np.float32]) -> Path:
        """Store ``array`` atomically and return its path."""
        expected = (len(key.sequence), len(AA_ORDER))
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
