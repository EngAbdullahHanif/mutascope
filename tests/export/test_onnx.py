"""ONNX export helpers: the tokenizer, the quantization, the layout and the record (ADR 0029)."""

from pathlib import Path
from typing import Any

import numpy as np
import onnx as onnx_untyped
import onnxruntime as ort_untyped  # pyright: ignore[reportMissingTypeStubs]
import pytest
from onnx import TensorProto, helper, numpy_helper
from tests.scoring.test_esm2 import ESM_VOCAB
from transformers import EsmTokenizer

from mutascope.alphabet import AA_ORDER
from mutascope.eval.onnx import random_sequence, tokenizer_mismatches
from mutascope.export.onnx import (
    ExportRecord,
    OnnxExportError,
    QuantRecipe,
    esm_tokenizer,
    export_paths,
    model_files,
    quantize_int8,
)

onnx: Any = onnx_untyped  # partially typed upstream
ort: Any = ort_untyped
RECIPE = QuantRecipe("QInt8", per_channel=True, reduce_range=True, op_types=("MatMul",))


def test_tokenizer_json_matches_the_transformers_tokenizer(tmp_path: Path) -> None:
    """The served tokenizer must give the ids the PyTorch scorer was checked with."""
    vocab = tmp_path / "vocab.txt"
    vocab.write_text("\n".join(ESM_VOCAB))
    theirs = EsmTokenizer(vocab_file=str(vocab))
    ours = esm_tokenizer(ESM_VOCAB)
    rng = np.random.default_rng(0)
    sequences = [AA_ORDER, *(random_sequence(int(n), rng) for n in rng.integers(1, 1023, 50))]
    assert (
        tokenizer_mismatches(
            lambda s: ours.encode(s).ids, lambda s: list(theirs(s)["input_ids"]), sequences
        )
        == []
    )
    assert ours.encode("ACD").ids == [0, 5, 23, 13, 2]
    # Saved and reloaded, as the scorer reads it.
    ours.save(str(tmp_path / "tokenizer.json"))
    reloaded = type(ours).from_file(str(tmp_path / "tokenizer.json"))
    assert reloaded.encode(AA_ORDER).ids == ours.encode(AA_ORDER).ids


def test_tokenizer_needs_the_special_tokens() -> None:
    with pytest.raises(OnnxExportError, match="<mask>"):
        esm_tokenizer([t for t in ESM_VOCAB if t != "<mask>"])


def _matmul_graph(path: Path) -> np.ndarray:
    weight = np.random.default_rng(0).normal(size=(16, 8)).astype(np.float32)
    graph = helper.make_graph(
        [helper.make_node("MatMul", ["x", "w"], ["y"])],
        "matmul",
        [helper.make_tensor_value_info("x", TensorProto.FLOAT, ["n", 16])],
        [helper.make_tensor_value_info("y", TensorProto.FLOAT, ["n", 8])],
        initializer=[numpy_helper.from_array(weight, "w")],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)], ir_version=10)
    onnx.save(model, str(path))
    return weight


def test_quantize_int8_quantizes_matmul_weights_and_is_deterministic(tmp_path: Path) -> None:
    weight = _matmul_graph(tmp_path / "fp32.onnx")
    quantize_int8(tmp_path / "fp32.onnx", tmp_path / "a" / "int8.onnx", RECIPE)
    quantize_int8(tmp_path / "fp32.onnx", tmp_path / "b" / "int8.onnx", RECIPE)
    a, b = (tmp_path / d / "int8.onnx" for d in ("a", "b"))
    assert a.read_bytes() == b.read_bytes()
    model = onnx.load(str(a))
    assert "MatMul" not in {n.op_type for n in model.graph.node}
    assert any(i.data_type == TensorProto.INT8 for i in model.graph.initializer)
    x = np.random.default_rng(1).normal(size=(4, 16)).astype(np.float32)
    session = ort.InferenceSession(str(a), providers=["CPUExecutionProvider"])
    (y,) = session.run(["y"], {"x": x})
    np.testing.assert_allclose(np.asarray(y), x @ weight, atol=0.15)


def test_export_layout_and_record_round_trip(tmp_path: Path) -> None:
    assert export_paths("esm2_35m", "int8") == (
        "esm2_35m/int8/model.onnx",
        "esm2_35m/tokenizer.json",
    )
    with pytest.raises(ValueError, match="precision"):
        export_paths("esm2_35m", "fp16")
    (tmp_path / "fp32").mkdir()
    (tmp_path / "fp32" / "model.onnx").write_bytes(b"graph")
    (tmp_path / "tokenizer.json").write_text("{}")
    record = ExportRecord(
        model="esm2_35m",
        source_hf_id="facebook/esm2_t12_35M_UR50D",
        source_revision="0" * 40,
        task="fill-mask",
        opset=18,
        quantization={"weight_type": "QInt8"},
        export_environment={"optimum-onnx": "0.1.0"},
        onnxruntime="1.31.0",
        sha256=model_files(tmp_path),
    )
    record.write(tmp_path / "export.json")
    assert ExportRecord.read(tmp_path / "export.json") == record
    assert sorted(record.sha256) == ["fp32/model.onnx", "tokenizer.json"]
    assert model_files(tmp_path) == record.sha256  # export.json is not in its own digests
