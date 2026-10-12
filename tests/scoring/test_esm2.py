"""ESM-2 scorer: token offset, windows, caching, and agreement with ProteinGym.

Two kinds of test:

- **Stand-in models** (no network): tiny modules whose logits are a known function of the input,
  run through the real scorer with a real ESM tokenizer built from the vocabulary. They pin the
  token offset and the window arithmetic exactly, including sequences longer than the window.
- **The real ``esm2_t6_8M`` checkpoint** (pinned revision, downloaded once into the Hugging Face
  cache): an off-by-one check that needs no data, and agreement with ProteinGym's precomputed
  ``ESM2_8M`` scores when the release is downloaded.
"""

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest
import torch
from omegaconf import OmegaConf
from scipy.stats import (  # pyright: ignore[reportMissingTypeStubs]
    spearmanr,  # pyright: ignore[reportUnknownVariableType]
)
from transformers import EsmTokenizer

from mutascope.alphabet import AA_ORDER
from mutascope.data.release import load_release
from mutascope.eval.proteingym import load_precomputed_scores
from mutascope.scoring.base import Scorer, build_scorer, score_mutants
from mutascope.scoring.cache import LogProbCache
from mutascope.scoring.esm2 import Esm2Scorer, Esm2Settings
from mutascope.utils.config import as_str_dict, load_config

CONF = Path(__file__).parents[2] / "conf"
ESM_VOCAB = ["<cls>", "<pad>", "<eos>", "<unk>", *"LAGVSERTIDPKQNFYMHWCXBUZO", ".", "-",
             "<null_1>", "<mask>"]  # fmt: skip
"""ESM-2's 33-token vocabulary, in token-id order (identical in fair-esm and on the Hub)."""
UBIQUITIN = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"


@pytest.fixture(scope="module")
def tokenizer(tmp_path_factory: pytest.TempPathFactory) -> EsmTokenizer:
    vocab = tmp_path_factory.mktemp("vocab") / "vocab.txt"
    vocab.write_text("\n".join(ESM_VOCAB))
    return EsmTokenizer(vocab_file=str(vocab))


class _StandIn(torch.nn.Module):
    """Logits = 10 x one-hot of the input token ``shift`` places to the left (0 = itself)."""

    def __init__(self, shift: int) -> None:
        super().__init__()
        self.shift = shift
        self.calls = 0

    def forward(self, input_ids: torch.Tensor, attention_mask: torch.Tensor) -> SimpleNamespace:
        self.calls += 1
        source = torch.roll(input_ids, self.shift, dims=1)
        logits = 10.0 * torch.nn.functional.one_hot(source, len(ESM_VOCAB)).float()
        return SimpleNamespace(logits=logits)


def _scorer(tokenizer: EsmTokenizer, shift: int, window: int = 1024, **kw: Any) -> Esm2Scorer:
    settings = Esm2Settings("standin", "standin/esm", "0" * 40, batch_size=7, window_tokens=window)
    return Esm2Scorer(
        settings=settings,
        model=_StandIn(shift),
        tokenizer=tokenizer,
        device=torch.device("cpu"),
        **kw,
    )


def _argmax_residues(log_probs: np.ndarray) -> str:
    return "".join(AA_ORDER[i] for i in log_probs.argmax(axis=1))


def _random_sequence(length: int, seed: int) -> str:
    rng = np.random.default_rng(seed)
    return "".join(rng.choice(list(AA_ORDER), size=length))


@pytest.mark.parametrize("window", [1024, 32], ids=["fits", "windowed"])
def test_masked_marginal_reads_residue_i_at_token_i_plus_1(
    tokenizer: EsmTokenizer, window: int
) -> None:
    """The stand-in predicts each masked token from its left neighbour, so residue i's row must
    show residue i - 1. Reading token i (forgetting ``<cls>``) or token i + 2, or taking the
    wrong slot in a window, breaks the equality. With ``window=32`` a 300-residue sequence runs
    through left-edge, centred and right-edge windows."""
    seq = _random_sequence(300, seed=1)
    log_probs = _scorer(tokenizer, shift=1, window=window).log_probs(seq)
    predicted = _argmax_residues(log_probs)
    assert predicted[1:] == seq[:-1]
    # The check has power: an off-by-one in either direction would not pass it.
    assert np.mean([a == b for a, b in zip(predicted[1:], seq[1:], strict=True)]) < 0.2
    assert np.mean([a == b for a, b in zip(predicted[2:], seq[:-2], strict=True)]) < 0.2


@pytest.mark.parametrize("window", [1024, 32], ids=["fits", "windowed"])
def test_wt_marginal_reads_residue_i_at_token_i_plus_1(
    tokenizer: EsmTokenizer, window: int
) -> None:
    """The stand-in copies each input token, so the unmasked pass must return the sequence."""
    seq = _random_sequence(300, seed=2)
    assert _argmax_residues(_scorer(tokenizer, 0, window).log_probs(seq, fast=True)) == seq


def test_landscape_shape_and_wild_type_column(tokenizer: EsmTokenizer) -> None:
    scorer = _scorer(tokenizer, shift=1)
    assert isinstance(scorer, Scorer)
    landscape = scorer.landscape(UBIQUITIN)
    assert landscape.shape == (len(UBIQUITIN), 20) and landscape.dtype == np.float32
    wt = [AA_ORDER.index(aa) for aa in UBIQUITIN]
    assert (landscape[np.arange(len(UBIQUITIN)), wt] == 0).all()


def test_cache_skips_the_model(tokenizer: EsmTokenizer, tmp_path: Path) -> None:
    scorer = _scorer(tokenizer, shift=1, cache=LogProbCache(tmp_path))
    model = scorer.model
    assert isinstance(model, _StandIn)
    first = scorer.log_probs(UBIQUITIN)
    calls = model.calls
    np.testing.assert_array_equal(scorer.log_probs(UBIQUITIN), first)
    assert model.calls == calls
    scorer.log_probs(UBIQUITIN, fast=True)  # a different method is a different entry
    assert model.calls == calls + 1


def test_bad_tokenisation_fails_loudly(tokenizer: EsmTokenizer) -> None:
    class NoCls:
        cls_token_id, eos_token_id = tokenizer.cls_token_id, tokenizer.eos_token_id
        mask_token_id, unk_token_id = tokenizer.mask_token_id, tokenizer.unk_token_id
        convert_tokens_to_ids = staticmethod(tokenizer.convert_tokens_to_ids)

        def __call__(self, text: str) -> dict[str, list[int]]:
            return {"input_ids": tokenizer(text)["input_ids"][1:]}

    settings = Esm2Settings("standin", "standin/esm", "0" * 40, batch_size=4, window_tokens=1024)
    scorer = Esm2Scorer(
        settings=settings, model=_StandIn(1), tokenizer=NoCls(), device=torch.device("cpu")
    )
    with pytest.raises(RuntimeError, match=r"residue i must be token i \+ 1"):
        scorer.log_probs("MKV")


def test_structure_tokens_are_rejected(tokenizer: EsmTokenizer) -> None:
    with pytest.raises(ValueError, match="sequence-only"):
        _scorer(tokenizer, shift=1).landscape("MKV", "abc")


# --- the real esm2_t6_8M checkpoint ------------------------------------------------------------

_cfg = load_config(CONF, ["model=esm2_8m"])


@pytest.fixture(scope="module")
def esm2_8m() -> Iterator[Scorer]:
    model_cfg = as_str_dict(OmegaConf.to_container(_cfg.model, resolve=True), "model")
    yield build_scorer(
        model_cfg,
        device=str(_cfg.scoring.device),
        cache_dir=None,
    )


def test_real_model_predicts_the_unmasked_residue_at_its_own_position(
    esm2_8m: Scorer,
) -> None:
    """An unmasked ESM-2 pass all but copies its input. Aligned, the wild-type marginal's argmax
    is the sequence itself (72 of 76 residues of ubiquitin); shifted by one it matches only by
    chance."""
    assert isinstance(esm2_8m, Esm2Scorer)
    predicted = _argmax_residues(esm2_8m.log_probs(UBIQUITIN, fast=True))
    aligned = np.mean([a == b for a, b in zip(predicted, UBIQUITIN, strict=True)])
    shifted = np.mean([a == b for a, b in zip(predicted[1:], UBIQUITIN[:-1], strict=True)])
    assert aligned >= 0.9
    assert shifted <= 0.2


def test_real_model_landscapes(esm2_8m: Scorer) -> None:
    masked = esm2_8m.landscape(UBIQUITIN)
    fast = esm2_8m.landscape(UBIQUITIN, fast=True)
    assert masked.shape == fast.shape == (len(UBIQUITIN), 20)
    assert np.isfinite(masked).all() and np.isfinite(fast).all()
    assert not np.allclose(masked, fast)


_release = load_release(_cfg.data)
_SCORES = _release.raw_dir / _release.file("zero_shot_scores").extract_dirname
_REFERENCE = _release.raw_dir / _release.file("reference").filename
needs_release = pytest.mark.skipif(
    not (_SCORES.is_dir() and _REFERENCE.is_file()),
    reason="needs the downloaded ProteinGym release (`mutascope data download`)",
)


def _compare_with_proteingym(scorer: Scorer, assay_id: str) -> tuple[float, float]:
    """Spearman and max |difference| between our scores and ProteinGym's ``ESM2_8M``."""
    seq = str(pd.read_csv(_REFERENCE).set_index("DMS_id").loc[assay_id, "target_seq"])
    rows = load_precomputed_scores(_SCORES, ["ESM2_8M"], "single")
    rows = rows[rows["assay_id"] == assay_id]
    ours = score_mutants(scorer.landscape(seq), seq, rows["mutant"].tolist())
    theirs = rows["ESM2_8M"].to_numpy(np.float64)
    rho: Any = spearmanr(ours, theirs)
    return float(rho.statistic), float(np.abs(ours - theirs).max())


@needs_release
@pytest.mark.parametrize("assay_id", ["GCN4_YEAST_Staller_2018", "SPG1_STRSG_Wu_2016"])
def test_masked_marginal_matches_proteingym(esm2_8m: Scorer, assay_id: str) -> None:
    """Same model, same strategy: the scores agree to float precision, not just in rank."""
    rho, max_diff = _compare_with_proteingym(esm2_8m, assay_id)
    assert rho > 0.9999
    assert max_diff < 1e-3


@pytest.mark.slow
@needs_release
@pytest.mark.parametrize(
    "assay_id",
    ["BRCA1_HUMAN_Findlay_2018", "UBE4B_MOUSE_Starita_2013", "BRCA2_HUMAN_Erwood_2022_HEK293T"],
)
def test_long_sequence_windows_match_proteingym(esm2_8m: Scorer, assay_id: str) -> None:
    """Targets of 1,863, 1,173 and 3,418 residues, mutated across left-edge, centred and
    right-edge windows (about 4 minutes on MPS)."""
    rho, max_diff = _compare_with_proteingym(esm2_8m, assay_id)
    assert rho > 0.9999
    assert max_diff < 1e-3
