"""ESM-2 zero-shot scorer on ONNX Runtime (CPU), for serving without PyTorch (ADR 0029).

The same model and the same two marginals as :mod:`mutascope.scoring.esm2`, from an ONNX export
of the pinned checkpoint (``mutascope onnx export``): fp32, or int8 with dynamically quantized
weights. The cache, the windows and the batching are the shared ones in
:mod:`mutascope.scoring.marginals`, so a landscape differs from the PyTorch scorer's only by
what the runtime and the quantization change in the logits.

Nothing here imports torch or transformers (``tests/scoring/test_onnx_esm2.py`` checks it in a
fresh interpreter): transformers' ESM tokenizer pulls torch in on import, so the tokenizer is a
``tokenizers`` ``tokenizer.json`` written beside the model at export time and checked there
against the Hugging Face tokenizer. :meth:`OnnxEsm2Scorer.encode` checks the token layout on
every sequence, as the PyTorch scorer does.

The files are read from a Hub model repository at a pinned commit (``hf_id``, ``revision``,
``file``, ``tokenizer_file``). ``local_dir`` reads them from an export directory instead, for
the checks run before a release exists; such a scorer has no cache, so an unreleased file can
never fill entries keyed by a released revision.
"""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Self, cast

import numpy as np
import numpy.typing as npt
import onnxruntime  # pyright: ignore[reportMissingTypeStubs]
from tokenizers import Tokenizer

from mutascope.alphabet import AA_ORDER
from mutascope.scoring.base import FloatArray, ScorerConfigError, check_sequence
from mutascope.scoring.cache import LogProbCache
from mutascope.scoring.marginals import (
    RESIDUE_OFFSET,
    MarginalScorer,
    ModelSettings,
    cache_model_id,
)
from mutascope.scoring.windows import tiled_windows
from mutascope.utils.logging import get_logger

log = get_logger(__name__)

ort = cast(Any, onnxruntime)  # untyped upstream; the scorer checks the graph it is given

IntArray = npt.NDArray[np.int64]

INPUTS = ("input_ids", "attention_mask")
"""The exported graph's inputs, ``(batch, tokens)`` int64 each."""
OUTPUT = "logits"


@dataclass(frozen=True)
class OnnxEsm2Settings(ModelSettings):
    """``conf/model/esm2_*_int8.yaml`` and ``esm2_*_onnx.yaml``."""

    file: str = ""
    """The ``.onnx`` file, relative to the repository root."""
    tokenizer_file: str = ""
    """The ``tokenizer.json``, relative to the repository root."""
    threads: int | None = None
    """ONNX Runtime intra-op threads; None: one per core."""

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any], *, pinned: bool = True) -> Self:
        base = ModelSettings.from_config(cfg, pinned=pinned)
        try:
            file, tokenizer_file = str(cfg["file"]), str(cfg["tokenizer_file"])
        except KeyError as exc:
            raise ScorerConfigError(f"model config lacks {exc}") from exc
        threads = cfg.get("threads")
        return cls(
            **asdict(base),
            file=file,
            tokenizer_file=tokenizer_file,
            threads=None if threads is None else int(threads),
        )


def make_session(path: Path, threads: int | None) -> Any:
    """A CPU inference session over ``path`` with ``threads`` intra-op threads.

    One inter-op thread: the graph is a chain, and ``threads`` is then the whole budget (the
    free Hugging Face Space has 2 vCPUs).
    """
    options = ort.SessionOptions()
    options.intra_op_num_threads = 0 if threads is None else threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


class OnnxEsm2Scorer(MarginalScorer[IntArray]):
    """Masked- and wild-type-marginal landscapes from an ONNX export of ESM-2.

    Construct with :func:`from_config`. The constructor takes the session and tokenizer
    directly so tests can pass small stand-ins.
    """

    def __init__(
        self,
        *,
        settings: OnnxEsm2Settings,
        session: Any,
        tokenizer: Tokenizer,
        cache: LogProbCache | None = None,
    ) -> None:
        super().__init__(
            settings=settings, cache=cache, cache_id=cache_model_id(settings.hf_id, settings.file)
        )
        if settings.window_tokens is None:
            raise ScorerConfigError(f"{settings.name}: ESM-2 needs window_tokens")
        self.session = session  # an onnxruntime.InferenceSession (untyped upstream)
        self.tokenizer = tokenizer
        names = {i.name for i in session.get_inputs()}, {o.name for o in session.get_outputs()}
        if names != (set(INPUTS), {OUTPUT}):
            raise ScorerConfigError(f"{settings.name}: graph has inputs/outputs {names}")
        vocab = tokenizer.get_vocab(with_added_tokens=True)
        try:
            self.cls_id, self.eos_id = vocab["<cls>"], vocab["<eos>"]
            self.mask_id = vocab["<mask>"]
            self.aa_ids = np.array([vocab[aa] for aa in AA_ORDER], dtype=np.int64)
        except KeyError as exc:
            raise ScorerConfigError(f"tokenizer lacks {exc}") from exc

    def encode(self, seq: str, struct_tokens: str | None) -> IntArray:
        """Token ids ``<cls> residues <eos>``; fails unless residue ``i`` is token ``i + 1``.

        ESM-2 is sequence-only, so ``struct_tokens`` must be None.
        """
        if struct_tokens is not None:
            raise ValueError("ESM-2 is sequence-only; struct_tokens must be None")
        check_sequence(seq)
        ids = np.array(self.tokenizer.encode(seq).ids, dtype=np.int64)
        aa = dict(zip(AA_ORDER, self.aa_ids.tolist(), strict=True))
        expected = [self.cls_id, *(aa[r] for r in seq), self.eos_id]
        if ids.tolist() != expected:
            raise RuntimeError(
                f"unexpected tokenisation of a {len(seq)}-residue sequence ({len(ids)} tokens): "
                f"residue i must be token i + {RESIDUE_OFFSET}"
            )
        return ids

    def forward(self, input_ids: IntArray) -> FloatArray:
        """Logits ``(batch, tokens, vocab)``, float32."""
        feeds = {"input_ids": input_ids, "attention_mask": np.ones_like(input_ids)}
        (logits,) = self.session.run([OUTPUT], feeds)
        return np.asarray(logits, dtype=np.float32)

    def aa_log_probs(self, logits: FloatArray) -> FloatArray:
        """Log-softmax over the whole vocabulary, then the 20 amino-acid columns in AA_ORDER."""
        shifted = logits - logits.max(axis=-1, keepdims=True)
        log_probs = shifted - np.log(np.exp(shifted).sum(axis=-1, keepdims=True))
        return log_probs[:, self.aa_ids].astype(np.float32)

    def _masked_marginal(self, tokens: IntArray, residues: Sequence[int]) -> FloatArray:
        rows: list[FloatArray] = []
        for batch in self.masked_batches(len(tokens), residues):
            inputs = np.stack([tokens[p.window.start : p.window.end] for p in batch])
            inputs[np.arange(len(batch)), [p.local for p in batch]] = self.mask_id
            logits = self.forward(inputs)
            rows.append(self.aa_log_probs(logits[np.arange(len(batch)), [p.local for p in batch]]))
        return np.concatenate(rows)

    def _wt_marginal(self, tokens: IntArray) -> FloatArray:
        n_tokens = len(tokens)
        window = self.settings.window_tokens
        tiles, owner = tiled_windows(n_tokens, n_tokens if window is None else window)
        logits = self.forward(np.stack([tokens[t.start : t.end] for t in tiles]))
        positions = range(RESIDUE_OFFSET, n_tokens - 1)
        tile = [owner[p] for p in positions]
        local = [p - tiles[owner[p]].start for p in positions]
        return self.aa_log_probs(logits[tile, local])


def from_config(
    model_cfg: Mapping[str, Any], *, device: str, cache_dir: Path | None
) -> OnnxEsm2Scorer:
    """Load the pinned ONNX file named by ``model_cfg`` into a CPU session.

    Args:
        model_cfg: ``conf/model/esm2_*_int8.yaml`` (or ``_onnx``), resolved.
        device: ``"auto"`` or ``"cpu"``; ONNX Runtime runs on the CPU here.
        cache_dir: Root of the log-probability cache, or None to disable it. Ignored with
            ``local_dir``: files read from an export have no pinned revision to key entries by.

    Raises:
        ScorerConfigError: ``device`` is not the CPU, or the config lacks a field.
    """
    local_dir = model_cfg.get("local_dir")
    settings = OnnxEsm2Settings.from_config(model_cfg, pinned=local_dir is None)
    if device not in ("auto", "cpu"):
        raise ScorerConfigError(f"{settings.name}: the ONNX scorer runs on the CPU, not {device}")
    if local_dir is not None:
        root = Path(str(local_dir))
        model_path, tokenizer_path = root / settings.file, root / settings.tokenizer_file
        cache_dir = None
        log.warning("onnx_local_files", model=settings.name, dir=str(root), cache="off")
    else:
        from huggingface_hub import (
            hf_hub_download,  # pyright: ignore[reportUnknownVariableType]
        )

        def fetch(name: str) -> Path:
            return Path(hf_hub_download(settings.hf_id, name, revision=settings.revision))

        model_path, tokenizer_path = fetch(settings.file), fetch(settings.tokenizer_file)
    session = make_session(model_path, settings.threads)
    log.info(
        "model_loaded",
        model=settings.name,
        hf_id=settings.hf_id,
        revision=settings.revision,
        file=settings.file,
        threads=settings.threads,
    )
    return OnnxEsm2Scorer(
        settings=settings,
        session=session,
        tokenizer=Tokenizer.from_file(str(tokenizer_path)),
        cache=LogProbCache(cache_dir) if cache_dir is not None else None,
    )
