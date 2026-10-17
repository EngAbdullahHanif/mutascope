"""Disk cache of live landscapes (diskcache), so an identical request is computed once.

A key is the sha256 of the JSON-encoded model id (Hub repo and file, ``cache_model_id``), its
revision, the method (``"wt"`` fast, ``"masked"`` precise) and the sha256 of the sequence: the
same fields as the pipeline's log-probability cache (``mutascope.scoring.cache``), minus the
structure tokens and region that a live ESM-2 request never has. JSON encoding keeps two
different tuples from concatenating to the same string.

A value is the ``(L, 20)`` float32 landscape as bytes; its length is checked against the
sequence when read, so a corrupt entry is recomputed rather than served. The cache is bounded
(``score_cache_bytes``) with least-recently-used eviction. Entries never expire: a pinned model
gives the same landscape for the same sequence forever.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, cast

import diskcache  # pyright: ignore[reportMissingTypeStubs]
import numpy as np

from mutascope.alphabet import AA_ORDER
from mutascope.scoring.base import FloatArray
from mutascope.utils.logging import get_logger

log = get_logger(__name__)


def cache_key(model_id: str, revision: str, method: str, sequence: str) -> str:
    """Hex sha256 of ``[model_id, revision, method, sha256(sequence)]``."""
    seq_hash = hashlib.sha256(sequence.encode()).hexdigest()
    return hashlib.sha256(json.dumps([model_id, revision, method, seq_hash]).encode()).hexdigest()


class ScoreCache:
    """Landscapes by :func:`cache_key`, in a size-bounded diskcache directory."""

    def __init__(self, directory: Path, *, size_limit: int) -> None:
        self._cache = cast(
            Any,
            diskcache.Cache(  # pyright: ignore[reportUnknownMemberType]
                str(directory), size_limit=size_limit, eviction_policy="least-recently-used"
            ),
        )
        log.info("score_cache_opened", path=str(directory), size_limit=size_limit)

    def get(self, key: str, length: int) -> FloatArray | None:
        """The landscape stored under ``key``, or None (absent, or not ``(length, 20)``)."""
        raw = self._cache.get(key)
        if not isinstance(raw, bytes) or len(raw) != length * len(AA_ORDER) * 4:
            if raw is not None:
                log.warning("score_cache_bad_entry", key=key[:12])
            return None
        return np.frombuffer(raw, dtype=np.float32).reshape(length, len(AA_ORDER)).copy()

    def put(self, key: str, landscape: FloatArray) -> None:
        self._cache.set(key, np.ascontiguousarray(landscape, dtype=np.float32).tobytes())

    def __len__(self) -> int:
        return len(self._cache)

    def close(self) -> None:
        self._cache.close()
