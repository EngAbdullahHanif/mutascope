"""ONNX ESM-2 scorer: token offset, windows, caching, agreement with the PyTorch scorer, no torch.

- **Stand-in graphs** (no network): a tiny ONNX graph whose logits are the same known function
  of the input as ``tests/scoring/test_esm2.py``'s PyTorch stand-in, so the two scorers must
  give the same log-probabilities, and the token offset and windows are pinned exactly.
- **Torch-free**: the scorer is built and run in a fresh interpreter, which must end with
  neither torch nor transformers imported (what lets the API image leave them out).
- **The published int8 file** (``slow``): the pinned Hub revision against PyTorch.
"""

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import onnx as onnx_untyped
import pytest
import torch
from omegaconf import OmegaConf
from onnx import TensorProto, helper
from tests.scoring.test_esm2 import (  # the PyTorch scorer's stand-ins
    ESM_VOCAB,
    UBIQUITIN,
    _random_sequence,  # pyright: ignore[reportPrivateUsage]
    _StandIn,  # pyright: ignore[reportPrivateUsage]
)
from tokenizers import Tokenizer
from transformers import EsmTokenizer

from mutascope.alphabet import AA_ORDER
from mutascope.eval.metrics import spearman
from mutascope.export.onnx import esm_tokenizer
from mutascope.scoring.base import Scorer, ScorerConfigError, build_scorer
from mutascope.scoring.cache import LogProbCache
from mutascope.scoring.esm2 import Esm2Scorer, Esm2Settings
from mutascope.scoring.onnx_esm2 import (
    OnnxEsm2Scorer,
    OnnxEsm2Settings,
    from_config,
    make_session,
)
from mutascope.utils.config import as_str_dict, load_config

CONF = Path(__file__).parents[2] / "conf"
onnx: Any = onnx_untyped  # partially typed upstream
IR_VERSION = 10
"""onnx 1.23 writes IR 14 by default, newer than ONNX Runtime 1.31 reads (13)."""


def standin_graph(path: Path, shift: int) -> Path:
    """Logits = 10 x one-hot of the input token ``shift`` places to the left (0 = itself)."""
    nodes: list[Any] = []
    source = "input_ids"
    if shift:
        nodes += [
            helper.make_node("Slice", ["input_ids", "neg", "big", "one"], ["tail"]),
            helper.make_node("Slice", ["input_ids", "zero", "neg", "one"], ["head"]),
            helper.make_node("Concat", ["tail", "head"], ["rolled"], axis=1),
        ]
        source = "rolled"
    nodes += [
        helper.make_node("OneHot", [source, "depth", "values"], ["onehot"], axis=-1),
        # Use the mask so the graph's inputs are exactly the exported model's.
        helper.make_node("Unsqueeze", ["attention_mask", "minus_one"], ["mask3"]),
        helper.make_node("Cast", ["mask3"], ["maskf"], to=TensorProto.FLOAT),
        helper.make_node("Mul", ["onehot", "maskf"], ["logits"]),
    ]
    ints = TensorProto.INT64
    initializers = [
        helper.make_tensor("neg", ints, [1], [-shift]),
        helper.make_tensor("big", ints, [1], [2**62]),
        helper.make_tensor("zero", ints, [1], [0]),
        helper.make_tensor("one", ints, [1], [1]),
        helper.make_tensor("minus_one", ints, [1], [-1]),
        helper.make_tensor("depth", ints, [], [len(ESM_VOCAB)]),
        helper.make_tensor("values", TensorProto.FLOAT, [2], [0.0, 10.0]),
    ]
    graph = helper.make_graph(
        nodes,
        "standin",
        [
            helper.make_tensor_value_info("input_ids", ints, ["batch", "tokens"]),
            helper.make_tensor_value_info("attention_mask", ints, ["batch", "tokens"]),
        ],
        [helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["batch", "tokens", "vocab"])],
        initializer=initializers,
    )
    model = helper.make_model(
        graph, opset_imports=[helper.make_opsetid("", 18)], ir_version=IR_VERSION
    )
    onnx.checker.check_model(model)
    onnx.save(model, str(path))
    return path


@pytest.fixture(scope="module")
def tokenizer() -> Tokenizer:
    return esm_tokenizer(ESM_VOCAB)


@pytest.fixture(scope="module")
def graphs(tmp_path_factory: pytest.TempPathFactory) -> Callable[[int], Path]:
    root = tmp_path_factory.mktemp("graphs")
    return lambda shift: standin_graph(root / f"shift{shift}.onnx", shift)


def _scorer(
    tokenizer: Tokenizer,
    graph: Path,
    window: int = 1024,
    cache: LogProbCache | None = None,
    file: str = "standin/model.onnx",
) -> OnnxEsm2Scorer:
    settings = OnnxEsm2Settings(
        "standin", "standin/onnx", "0" * 40, batch_size=7, window_tokens=window, file=file
    )
    return OnnxEsm2Scorer(
        settings=settings, session=make_session(graph, 1), tokenizer=tokenizer, cache=cache
    )


def _argmax_residues(log_probs: np.ndarray) -> str:
    return "".join(AA_ORDER[i] for i in log_probs.argmax(axis=1))


@pytest.mark.parametrize("window", [1024, 32], ids=["fits", "windowed"])
def test_masked_marginal_reads_residue_i_at_token_i_plus_1(
    tokenizer: Tokenizer, graphs: Callable[[int], Path], window: int
) -> None:
    """As for the PyTorch scorer: residue i's row must show residue i - 1, through left-edge,
    centred and right-edge windows when ``window=32``."""
    seq = _random_sequence(300, seed=1)
    predicted = _argmax_residues(_scorer(tokenizer, graphs(1), window).log_probs(seq))
    assert predicted[1:] == seq[:-1]
    assert np.mean([a == b for a, b in zip(predicted[1:], seq[1:], strict=True)]) < 0.2


@pytest.mark.parametrize("window", [1024, 32], ids=["fits", "windowed"])
def test_wt_marginal_reads_residue_i_at_token_i_plus_1(
    tokenizer: Tokenizer, graphs: Callable[[int], Path], window: int
) -> None:
    seq = _random_sequence(300, seed=2)
    log_probs = _scorer(tokenizer, graphs(0), window).log_probs(seq, fast=True)
    assert _argmax_residues(log_probs) == seq


@pytest.mark.parametrize("fast", [False, True], ids=["masked", "wt"])
@pytest.mark.parametrize("window", [1024, 32], ids=["fits", "windowed"])
def test_same_log_probs_as_the_pytorch_scorer(
    tokenizer: Tokenizer, graphs: Callable[[int], Path], fast: bool, window: int, tmp_path: Path
) -> None:
    """The same function of the tokens in both runtimes gives the same log-probabilities, so
    the two scorers mask, window, batch and normalise identically."""
    vocab = tmp_path / "vocab.txt"
    vocab.write_text("\n".join(ESM_VOCAB))
    torch_scorer = Esm2Scorer(
        settings=Esm2Settings("standin", "standin/esm", "0" * 40, 7, window),
        model=_StandIn(1),
        tokenizer=EsmTokenizer(vocab_file=str(vocab)),
        device=torch.device("cpu"),
    )
    seq = _random_sequence(150, seed=3)
    np.testing.assert_allclose(
        _scorer(tokenizer, graphs(1), window).log_probs(seq, fast=fast),
        torch_scorer.log_probs(seq, fast=fast),
        rtol=0,
        atol=1e-5,
    )


def test_landscape_shape_and_wild_type_column(
    tokenizer: Tokenizer, graphs: Callable[[int], Path]
) -> None:
    scorer = _scorer(tokenizer, graphs(1))
    assert isinstance(scorer, Scorer)
    landscape = scorer.landscape(UBIQUITIN)
    assert landscape.shape == (len(UBIQUITIN), 20) and landscape.dtype == np.float32
    wt = [AA_ORDER.index(aa) for aa in UBIQUITIN]
    assert (landscape[np.arange(len(UBIQUITIN)), wt] == 0).all()


def test_cache_entries_are_per_file(
    tokenizer: Tokenizer, graphs: Callable[[int], Path], tmp_path: Path
) -> None:
    """fp32 and int8 files share a repo and revision; their landscapes must not share entries."""
    cache = LogProbCache(tmp_path)
    first = _scorer(tokenizer, graphs(1), cache=cache, file="m/fp32/model.onnx")
    second = _scorer(tokenizer, graphs(0), cache=cache, file="m/int8/model.onnx")
    a = first.log_probs(UBIQUITIN, positions=[0, 1])
    b = second.log_probs(UBIQUITIN, positions=[0, 1])
    assert second.positions_computed == 2
    assert not np.array_equal(a[:2], b[:2])
    assert first.cache_id == "standin/onnx/m/fp32/model.onnx"
    again = _scorer(tokenizer, graphs(0), cache=cache, file="m/fp32/model.onnx")
    np.testing.assert_array_equal(again.log_probs(UBIQUITIN, positions=[0, 1]), a)
    assert again.positions_computed == 0


def test_rejects_structure_tokens_and_non_canonical_residues(
    tokenizer: Tokenizer, graphs: Callable[[int], Path]
) -> None:
    scorer = _scorer(tokenizer, graphs(1))
    with pytest.raises(ValueError, match="sequence-only"):
        scorer.landscape("ACD", "abc")
    with pytest.raises(ValueError, match="canonical"):
        scorer.landscape("ACX")


def test_rejects_a_graph_with_other_inputs(tokenizer: Tokenizer, tmp_path: Path) -> None:
    graph = helper.make_graph(
        [helper.make_node("Identity", ["x"], ["logits"])],
        "other",
        [helper.make_tensor_value_info("x", TensorProto.FLOAT, [1])],
        [helper.make_tensor_value_info("logits", TensorProto.FLOAT, [1])],
    )
    path = tmp_path / "other.onnx"
    model = helper.make_model(
        graph, opset_imports=[helper.make_opsetid("", 18)], ir_version=IR_VERSION
    )
    onnx.save(model, str(path))
    with pytest.raises(ScorerConfigError, match="inputs/outputs"):
        _scorer(tokenizer, path)


def _local_config(
    root: Path, graphs: Callable[[int], Path], tokenizer: Tokenizer
) -> dict[str, Any]:
    (root / "m" / "int8").mkdir(parents=True)
    graphs(1).replace(root / "m" / "int8" / "model.onnx")
    tokenizer.save(str(root / "m" / "tokenizer.json"))
    return {
        "name": "standin_int8",
        "scorer": "esm2_onnx",
        "hf_id": "standin/onnx",
        "revision": None,
        "file": "m/int8/model.onnx",
        "tokenizer_file": "m/tokenizer.json",
        "local_dir": str(root),
        "threads": 1,
        "batch_size": 4,
        "window_tokens": 1024,
    }


def test_from_config_with_local_files_has_no_cache(
    tokenizer: Tokenizer, graphs: Callable[[int], Path], tmp_path: Path
) -> None:
    """Unreleased files have no pinned revision to key cache entries by."""
    cfg = _local_config(tmp_path / "export", graphs, tokenizer)
    scorer = build_scorer(cfg, device="auto", cache_dir=tmp_path / "cache")
    assert isinstance(scorer, OnnxEsm2Scorer) and scorer.cache is None
    with pytest.raises(ScorerConfigError, match="CPU"):
        from_config(cfg, device="mps", cache_dir=None)
    with pytest.raises(ScorerConfigError, match="commit sha"):
        from_config(cfg | {"local_dir": None}, device="cpu", cache_dir=None)


def test_scores_without_importing_torch_or_transformers(
    tokenizer: Tokenizer, graphs: Callable[[int], Path], tmp_path: Path
) -> None:
    """Build and run the scorer in a fresh interpreter: torch and transformers stay unloaded."""
    cfg = _local_config(tmp_path / "export", graphs, tokenizer)
    script = f"""
import sys
from mutascope.scoring.base import build_scorer
scorer = build_scorer({cfg!r}, device="cpu", cache_dir=None)
landscape = scorer.landscape("{UBIQUITIN}")
assert landscape.shape == ({len(UBIQUITIN)}, 20), landscape.shape
loaded = sorted(m for m in ("torch", "transformers") if m in sys.modules)
assert not loaded, loaded
"""
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_onnx_configs_name_their_source_and_the_export_layout() -> None:
    cfg = load_config(CONF)
    exported = [str(m) for m in cfg.onnx.models]
    for name in cfg.onnx.scorers:
        onnx_cfg = load_config(CONF, [f"model={name}"]).model
        assert onnx_cfg.scorer == "esm2_onnx" and onnx_cfg.source_model in exported
        assert onnx_cfg.file == f"{onnx_cfg.source_model}/{onnx_cfg.precision}/model.onnx"
        assert onnx_cfg.tokenizer_file == f"{onnx_cfg.source_model}/tokenizer.json"
        source = load_config(CONF, [f"model={onnx_cfg.source_model}"]).model
        assert (onnx_cfg.batch_size, onnx_cfg.window_tokens) == (
            source.batch_size,
            source.window_tokens,
        )
        assert onnx_cfg.hf_id == cfg.onnx.publish.repo_id
        assert onnx_cfg.revision == cfg.onnx.publish.revision


@pytest.mark.slow
def test_published_int8_35m_agrees_with_pytorch() -> None:
    """The pinned Hub file: fast-mode landscape of ubiquitin against PyTorch on the CPU."""

    def model(name: str) -> dict[str, Any]:
        cfg = load_config(CONF, [f"model={name}"]).model
        return as_str_dict(OmegaConf.to_container(cfg, resolve=True), "model")

    onnx_scorer = build_scorer(model("esm2_35m_int8"), device="cpu", cache_dir=None)
    torch_scorer = build_scorer(model("esm2_35m"), device="cpu", cache_dir=None)
    a = torch_scorer.landscape(UBIQUITIN, fast=True).ravel()
    b = onnx_scorer.landscape(UBIQUITIN, fast=True).ravel()
    off = a != 0
    assert spearman(a[off].astype(np.float64), b[off].astype(np.float64)) >= 0.99
