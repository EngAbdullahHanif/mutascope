"""SaProt scorer: combined tokens, the masking rule, the summed probabilities, no windows.

Three kinds of test:

- **Stand-in models** (no network): modules whose logits are a known function of the input, run
  through the real scorer with a real ESM tokenizer built from SaProt's vocabulary. They pin
  the token offset, which token hides a residue, and how many copies share a forward call.
- **The official ``predict_mut``** (:mod:`tests.scoring.saprot_reference`) on a small randomly
  initialised ESM model with SaProt's vocabulary: every single substitution of a sequence must
  get the same score from our scorer.
- **The real 650M checkpoint** (``slow``; needs the release and the processed tables): agreement
  with ProteinGym's ``SaProt_650M_AF2`` scores through the same functions as the benchmark run.
"""

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
from tests.scoring.saprot_reference import predict_mut
from transformers import EsmConfig, EsmForMaskedLM, EsmTokenizer

from mutascope.alphabet import AA_ORDER
from mutascope.data.release import load_release
from mutascope.eval.proteingym import load_precomputed_scores
from mutascope.eval.region import score_assay, structure_regions
from mutascope.scoring.base import SCORERS, Scorer, ScorerConfigError, build_scorer
from mutascope.scoring.cache import LogProbCache
from mutascope.scoring.saprot import SAPROT_AA, SaprotScorer, SaprotSettings
from mutascope.scoring.struct_tokens import STRUCT_ALPHABET, load_struct_tokens
from mutascope.utils.config import as_str_dict, load_config
from mutascope.utils.seeding import seed_all

CONF = Path(__file__).parents[2] / "conf"
SPECIAL = ["<cls>", "<pad>", "<eos>", "<unk>", "<mask>"]
SAPROT_VOCAB = [*SPECIAL, *(aa + s for aa in SAPROT_AA for s in STRUCT_ALPHABET)]
"""SaProt's 446-token vocabulary, in token-id order (``vocab.txt`` on the Hub)."""
THREE_DI = STRUCT_ALPHABET[:-1]


@pytest.fixture(scope="module")
def tokenizer(tmp_path_factory: pytest.TempPathFactory) -> EsmTokenizer:
    vocab = tmp_path_factory.mktemp("vocab") / "vocab.txt"
    vocab.write_text("\n".join(SAPROT_VOCAB))
    return EsmTokenizer(vocab_file=str(vocab))


class _StandIn(torch.nn.Module):
    """Logits = 10 x one-hot of the input token ``shift`` places to the left (0 = itself)."""

    def __init__(self, shift: int) -> None:
        super().__init__()
        self.shift = shift
        self.inputs: list[torch.Tensor] = []

    def forward(self, input_ids: torch.Tensor, attention_mask: torch.Tensor) -> SimpleNamespace:
        self.inputs.append(input_ids.clone())
        source = torch.roll(input_ids, self.shift, dims=1)
        logits = 10.0 * torch.nn.functional.one_hot(source, len(SAPROT_VOCAB)).float()
        return SimpleNamespace(logits=logits)


def _settings(window: int | None = None, cap: int | None = None) -> SaprotSettings:
    return SaprotSettings(
        "standin", "standin/saprot", "0" * 40, batch_size=7, window_tokens=window,
        max_batch_tokens=cap,
    )  # fmt: skip


def _scorer(tokenizer: EsmTokenizer, shift: int, **kw: Any) -> SaprotScorer:
    cache = kw.pop("cache", None)
    return SaprotScorer(
        settings=_settings(**kw),
        model=_StandIn(shift),
        tokenizer=tokenizer,
        device=torch.device("cpu"),
        cache=cache,
    )


def _random(alphabet: str, length: int, seed: int) -> str:
    rng = np.random.default_rng(seed)
    return "".join(rng.choice(list(alphabet), size=length))


def _argmax_residues(log_probs: np.ndarray) -> str:
    return "".join(AA_ORDER[i] for i in log_probs.argmax(axis=1))


def test_vocabulary_is_five_specials_then_21_by_21() -> None:
    assert len(SAPROT_VOCAB) == 446 and SAPROT_AA == AA_ORDER + "#"
    assert "saprot" in SCORERS


def test_masked_marginal_reads_residue_i_at_token_i_plus_1(tokenizer: EsmTokenizer) -> None:
    """The stand-in predicts each token from its left neighbour, so residue i's row must show
    residue i - 1 whatever the structure letters are (the scorer sums over them). Reading token
    i or i + 2 breaks the equality."""
    seq, struct = _random(AA_ORDER, 300, seed=1), _random(STRUCT_ALPHABET, 300, seed=2)
    scorer = _scorer(tokenizer, shift=1)
    assert isinstance(scorer, Scorer)
    predicted = _argmax_residues(scorer.log_probs(seq, struct))
    assert predicted[1:] == seq[:-1]
    assert np.mean([a == b for a, b in zip(predicted[1:], seq[1:], strict=True)]) < 0.2
    assert np.mean([a == b for a, b in zip(predicted[2:], seq[:-2], strict=True)]) < 0.2


def test_a_masked_residue_keeps_its_structure_letter(tokenizer: EsmTokenizer) -> None:
    """In the pass for residue p, token p + 1 is ``#`` + residue p's structure letter (``##``
    when that is already ``#``) and every other token is unchanged. ``<mask>`` never appears."""
    seq, struct = "MKVAR", "dv#pa"
    scorer = _scorer(tokenizer, shift=1)
    scorer.log_probs(seq, struct)
    model = scorer.model
    assert isinstance(model, _StandIn)
    (batch,) = model.inputs
    vocab = tokenizer.get_vocab()
    plain = [vocab["<cls>"], *(vocab[a + s] for a, s in zip(seq, struct, strict=True))]
    plain.append(vocab["<eos>"])
    for p in range(len(seq)):
        expected = list(plain)
        expected[p + 1] = vocab["#" + struct[p]]
        assert [int(i) for i in batch[p]] == expected
    assert vocab["<mask>"] not in batch


def test_amino_acid_probability_sums_its_21_structure_tokens(tokenizer: EsmTokenizer) -> None:
    """Against upstream's slice-and-sum on random logits; and mass on two tokens of one amino
    acid adds up instead of the larger one being taken."""
    scorer = _scorer(tokenizer, shift=0)
    seed_all(0)
    logits = torch.randn(4, len(SAPROT_VOCAB))
    ours = scorer.aa_log_probs(logits)
    probs = logits.softmax(dim=-1)
    vocab = tokenizer.get_vocab()
    for column, aa in enumerate(AA_ORDER):
        start = vocab[aa + STRUCT_ALPHABET[0]]
        upstream = probs[:, start : start + len(STRUCT_ALPHABET)].sum(dim=-1).log()
        torch.testing.assert_close(ours[:, column], upstream, atol=1e-5, rtol=0)
    two = torch.full((1, len(SAPROT_VOCAB)), -1e9)
    two[0, vocab["Ad"]] = two[0, vocab["A#"]] = 0.0
    assert scorer.aa_log_probs(two)[0, AA_ORDER.index("A")].item() == pytest.approx(0.0, abs=1e-6)


def test_matches_the_official_predict_mut_on_every_substitution(tokenizer: EsmTokenizer) -> None:
    """A two-layer ESM model with random weights and SaProt's vocabulary: no trained model is
    needed to check that the two implementations compute the same function of the logits."""
    seed_all(0)
    config = EsmConfig(
        vocab_size=len(SAPROT_VOCAB), hidden_size=32, num_hidden_layers=2, num_attention_heads=4,
        intermediate_size=64, position_embedding_type="rotary", token_dropout=True,
        mask_token_id=4, pad_token_id=1, initializer_range=0.5,
    )  # fmt: skip
    model = EsmForMaskedLM(config).eval()  # wide initial weights, so the scores are not tiny
    seq, struct = _random(AA_ORDER, 24, seed=3), _random(STRUCT_ALPHABET, 24, seed=4)
    scorer = SaprotScorer(
        settings=_settings(), model=model, tokenizer=tokenizer, device=torch.device("cpu")
    )
    landscape = scorer.landscape(seq, struct)
    combined = "".join(a + s for a, s in zip(seq, struct, strict=True))
    worst = 0.0
    for position, wt in enumerate(seq):
        for column, mt in enumerate(AA_ORDER):
            official = predict_mut(model, tokenizer, combined, f"{wt}{position + 1}{mt}")
            worst = max(worst, abs(official - float(landscape[position, column])))
    assert worst < 1e-4
    assert float(np.abs(landscape).max()) > 1  # against scores of this size


def test_long_sequences_are_not_windowed_and_the_token_cap_limits_a_batch(
    tokenizer: EsmTokenizer,
) -> None:
    """1,500 residues: every pass sees all 1,502 tokens, and with a cap of 4,000 tokens two
    masked copies share a forward call (7 without the cap)."""
    seq, struct = _random(AA_ORDER, 1500, seed=5), _random(STRUCT_ALPHABET, 1500, seed=6)
    positions = [0, 1, 700, 1498, 1499]
    for cap, batches in ((4000, [2, 2, 1]), (None, [5])):
        scorer = _scorer(tokenizer, shift=1, cap=cap)
        log_probs = scorer.log_probs(seq, struct, positions=positions)
        model = scorer.model
        assert isinstance(model, _StandIn)
        assert [tuple(b.shape) for b in model.inputs] == [(n, 1502) for n in batches]
        assert _argmax_residues(log_probs[positions[1:]]) == "".join(
            seq[p - 1] for p in positions[1:]
        )
    assert _scorer(tokenizer, shift=1, cap=100)._rows_per_batch(1502) == 1  # pyright: ignore[reportPrivateUsage]


def test_structure_tokens_are_required_and_checked(tokenizer: EsmTokenizer) -> None:
    scorer = _scorer(tokenizer, shift=1)
    with pytest.raises(ValueError, match="needs struct_tokens"):
        scorer.landscape("MKV")
    with pytest.raises(ValueError, match="2 structure tokens for 3 residues"):
        scorer.landscape("MKV", "dv")
    with pytest.raises(ValueError, match="lowercase 3Di letters"):
        scorer.landscape("MKV", "DVa")


def test_cache_entries_differ_by_structure_tokens(tokenizer: EsmTokenizer, tmp_path: Path) -> None:
    """Same sequence, different structure tokens: two entries; the same pair again: no pass."""
    scorer = _scorer(tokenizer, shift=1, cache=LogProbCache(tmp_path))
    model = scorer.model
    assert isinstance(model, _StandIn)
    scorer.log_probs("MKVAR", "dvvpa")
    scorer.log_probs("MKVAR", "dv#pa")
    assert len(list(tmp_path.rglob("*.npy"))) == 2 and len(model.inputs) == 2
    scorer.log_probs("MKVAR", "dv#pa")
    assert len(model.inputs) == 2 and scorer.positions_computed == 10


def test_a_vocabulary_without_the_combined_tokens_is_rejected(
    tmp_path: Path,
) -> None:
    vocab = tmp_path / "vocab.txt"
    vocab.write_text("\n".join([*SPECIAL, *AA_ORDER]))
    with pytest.raises(ScorerConfigError, match="combined tokens"):
        SaprotScorer(
            settings=_settings(),
            model=_StandIn(1),
            tokenizer=EsmTokenizer(vocab_file=str(vocab)),
            device=torch.device("cpu"),
        )


# --- the real 650M checkpoint ------------------------------------------------------------------

_cfg = load_config(CONF, ["model=saprot_650m_af2"])
_release = load_release(_cfg.data)
_SCORES = _release.raw_dir / _release.file("zero_shot_scores").extract_dirname
_REFERENCE = _release.raw_dir / _release.file("reference").filename
_PROCESSED = Path(_cfg.data.processed_dir)


@pytest.mark.slow
@pytest.mark.skipif(
    not (_SCORES.is_dir() and _REFERENCE.is_file() and (_PROCESSED / "three_di").is_dir()),
    reason="needs the downloaded release and the processed structure tables (`make pipeline`)",
)
@pytest.mark.parametrize(
    "assay_id",
    [
        "B2L11_HUMAN_Dutta_2010_binding-Mcl-1",  # 198 residues, 142 of them below pLDDT 70
        "A0A140D2T1_ZIKV_Sourisseau_2019",  # the structure models residues 291-794 of 3,423
        "KCNH2_HUMAN_Kozek_2020",  # 1,159 residues in one pass; ESM-2 scores its MSA region
    ],
)
def test_real_model_matches_proteingym(assay_id: str) -> None:
    """Same checkpoint, same inputs: the scores agree to float precision, not just in rank.
    Uses the configured cache, so it is quick after the benchmark run."""
    reference = pd.read_csv(_REFERENCE)
    reference = reference[reference["DMS_id"] == assay_id]
    target = str(reference["target_seq"].item())
    structures = load_struct_tokens(
        _PROCESSED / "three_di",
        _PROCESSED / "structure_features",
        {assay_id: target},
        float(_cfg.model.plddt_threshold),
    )
    region = structure_regions(reference, structures)[assay_id]
    scorer = build_scorer(
        as_str_dict(OmegaConf.to_container(_cfg.model, resolve=True), "model"),
        device=str(_cfg.scoring.device),
        cache_dir=Path(_cfg.scoring.cache_dir),
    )
    rows = load_precomputed_scores(_SCORES, ["SaProt_650M_AF2"], "single")
    rows = rows[rows["assay_id"] == assay_id]
    ours = score_assay(
        scorer, target, region, rows["mutant"].tolist(), struct_tokens=structures[assay_id].tokens
    )
    theirs = rows["SaProt_650M_AF2"].to_numpy(np.float64)
    rho: Any = spearmanr(ours, theirs)
    assert float(rho.statistic) > 0.9999
    assert float(np.abs(ours - theirs).max()) < 1e-3
