"""ONNX export of the ESM-2 checkpoints for serving: fp32 graph, int8 weights, tokenizer (ADR 0029).

``mutascope onnx export`` writes, per model, into ``onnx.export_dir/<model>/``:

- ``fp32/model.onnx``: optimum's export of the pinned checkpoint (``fill-mask``: logits for
  ``input_ids, attention_mask``). optimum runs in its own locked environment
  (``tools/onnx-export``), because its exporter does not support transformers 5;
  :func:`run_optimum` calls it on a local snapshot of the pinned revision, so the revision is
  never resolved twice.
- ``int8/model.onnx``: :func:`quantize_int8` of the fp32 graph, ONNX Runtime's dynamic
  quantization (int8 weights, activations quantized per call), with the recipe in
  ``onnx.quantization``, fixed before any result (ADR 0029).
- ``tokenizer.json``: :func:`esm_tokenizer`, the ESM vocabulary as a ``tokenizers`` tokenizer.
  transformers' ESM tokenizer imports torch, which a server without torch cannot load.
- ``config.json``: the checkpoint's Hugging Face config, as optimum wrote it.
- ``export.json``: :class:`ExportRecord`, the provenance and the sha256 of every file.

Nothing here imports torch: the export command checks the tokenizer against transformers', and
``check-parity`` the graphs against the PyTorch model, in the main environment.
"""

import hashlib
import json
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tokenizers import Regex, Tokenizer, models, pre_tokenizers, processors

from mutascope.utils.logging import get_logger

log = get_logger(__name__)

PRECISIONS = ("fp32", "int8")
MODEL_FILE = "model.onnx"
TOKENIZER_FILE = "tokenizer.json"
CONFIG_FILE = "config.json"
RECORD_FILE = "export.json"
SPECIAL_TOKENS = ("<cls>", "<pad>", "<eos>", "<unk>", "<mask>")
EXPORT_PACKAGES = ("optimum", "optimum-onnx", "transformers", "torch", "onnx", "onnxruntime")
"""Package versions of the export environment recorded in :class:`ExportRecord`."""


class OnnxExportError(RuntimeError):
    """The exporter failed, or its output is not what the scorer expects."""


@dataclass(frozen=True)
class QuantRecipe:
    """``onnx.quantization``: arguments of ``onnxruntime.quantization.quantize_dynamic``."""

    weight_type: str
    """``QInt8`` or ``QUInt8``."""
    per_channel: bool
    reduce_range: bool
    """7-bit weights. Without it, x86 CPUs without VNNI can saturate in the u8 x s8 kernel, so
    the same file would give different numbers on different machines (ADR 0029)."""
    op_types: tuple[str, ...]

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any]) -> "QuantRecipe":
        return cls(
            weight_type=str(cfg["weight_type"]),
            per_channel=bool(cfg["per_channel"]),
            reduce_range=bool(cfg["reduce_range"]),
            op_types=tuple(str(t) for t in cfg["op_types"]),
        )


@dataclass(frozen=True)
class ExportRecord:
    """``export.json``: where a model's ONNX files came from, and what they are."""

    model: str
    source_hf_id: str
    source_revision: str
    task: str
    opset: int
    quantization: dict[str, Any]
    export_environment: dict[str, str]
    """Package versions of ``tools/onnx-export`` that ran optimum."""
    onnxruntime: str
    """Version that quantized the int8 file (the main environment's)."""
    sha256: dict[str, str]
    """Path relative to the model directory -> digest, for every other file."""

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True) + "\n")

    @classmethod
    def read(cls, path: Path) -> "ExportRecord":
        return cls(**json.loads(path.read_text()))


def esm_tokenizer(vocab: Sequence[str]) -> Tokenizer:
    """ESM's tokenizer as a ``tokenizers`` object: one token per character, ``<cls> … <eos>``.

    Args:
        vocab: The checkpoint's ``vocab.txt`` lines, in token-id order.

    Raises:
        OnnxExportError: A special token is missing from the vocabulary.
    """
    ids = {token: i for i, token in enumerate(vocab)}
    missing = [t for t in SPECIAL_TOKENS if t not in ids]
    if missing:
        raise OnnxExportError(f"vocabulary lacks {missing}")
    tokenizer = Tokenizer(models.WordLevel(vocab=ids, unk_token="<unk>"))
    tokenizer.add_special_tokens(list(SPECIAL_TOKENS))  # pyright: ignore[reportUnknownMemberType]
    tokenizer.pre_tokenizer = pre_tokenizers.Split(Regex("."), behavior="isolated")
    tokenizer.post_processor = processors.TemplateProcessing(
        single="<cls> $A <eos>", special_tokens=[("<cls>", ids["<cls>"]), ("<eos>", ids["<eos>"])]
    )
    return tokenizer


def run_optimum(
    project: Path, snapshot: Path, out_dir: Path, *, task: str, opset: int
) -> dict[str, str]:
    """Export ``snapshot`` (a local checkpoint folder) with optimum, in ``project``'s environment.

    Returns:
        The export environment's package versions.

    Raises:
        OnnxExportError: optimum failed or wrote no ``model.onnx``.
    """
    base = ["uv", "run", "--project", str(project), "--frozen", "--no-dev"]
    command = [
        *base,
        "optimum-cli",
        *("export", "onnx", "--model", str(snapshot), "--task", task, "--opset", str(opset)),
        str(out_dir),
    ]
    log.info("optimum_export", snapshot=str(snapshot), out=str(out_dir))
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    if done.returncode != 0 or not (out_dir / MODEL_FILE).is_file():
        raise OnnxExportError(f"optimum export failed ({done.returncode}):\n{done.stderr[-3000:]}")
    probe = (
        "import importlib.metadata as m, json; "
        f"print(json.dumps({{p: m.version(p) for p in {list(EXPORT_PACKAGES)!r}}}))"
    )
    versions = subprocess.run(
        [*base, "python", "-c", probe], capture_output=True, text=True, check=True
    )
    return {str(k): str(v) for k, v in json.loads(versions.stdout).items()}


def quantize_int8(fp32: Path, out: Path, recipe: QuantRecipe) -> None:
    """Dynamic int8 quantization of ``fp32`` into ``out`` (deterministic for one ORT version)."""
    from onnxruntime.quantization import (  # pyright: ignore[reportMissingTypeStubs]
        QuantType,
        quantize_dynamic,  # pyright: ignore[reportUnknownVariableType]
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    quantize_dynamic(
        fp32,
        out,
        weight_type=getattr(QuantType, recipe.weight_type),
        per_channel=recipe.per_channel,
        reduce_range=recipe.reduce_range,
        op_types_to_quantize=list(recipe.op_types),
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def model_files(model_dir: Path) -> dict[str, str]:
    """sha256 of every file of an exported model except ``export.json``, by relative path."""
    return {
        p.relative_to(model_dir).as_posix(): sha256(p)
        for p in sorted(model_dir.rglob("*"))
        if p.is_file() and p.name != RECORD_FILE
    }


def place_export(optimum_dir: Path, model_dir: Path, tokenizer: Tokenizer) -> None:
    """Move optimum's graph and config into ``model_dir`` and write the tokenizer beside them."""
    (model_dir / "fp32").mkdir(parents=True, exist_ok=True)
    shutil.move(optimum_dir / MODEL_FILE, model_dir / "fp32" / MODEL_FILE)
    shutil.copyfile(optimum_dir / CONFIG_FILE, model_dir / CONFIG_FILE)
    tokenizer.save(str(model_dir / TOKENIZER_FILE))


def export_paths(model: str, precision: str) -> tuple[str, str]:
    """The ONNX file and the tokenizer of ``model`` at ``precision``, relative to the export
    directory (and to the Hub repo root, which has the same layout)."""
    if precision not in PRECISIONS:
        raise ValueError(f"precision must be one of {PRECISIONS}, got {precision!r}")
    return f"{model}/{precision}/{MODEL_FILE}", f"{model}/{TOKENIZER_FILE}"
