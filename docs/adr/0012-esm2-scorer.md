# 0012 — Scorer protocol and the ESM-2 scorer

- Status: accepted (benchmark scoring region decided by the author, 2026-10-12)
- Date: 2026-10-12

## Context

Phase 2 needs zero-shot landscapes from ESM-2 (35M, 150M, 650M) that reproduce ProteinGym's
precomputed `ESM2_*` scores, so the model gate (ADR 0010) can pass and the website can serve the
same numbers. ProteinGym produced those scores with
`proteingym/baselines/esm/compute_fitness.py` (pinned commit `1f8de97`) using the `fair-esm`
package and `--scoring-strategy masked-marginals`. We read it before writing ours. The details
that decide the numbers:

- **Score.** `log P(mt) − log P(wt)` at the mutated position, from a log-softmax over the whole
  33-token vocabulary (normalisation cancels in the difference).
- **Masked marginal.** One forward pass per token position with that token replaced by
  `<mask>`, reading the log-probabilities at that position.
- **Offset.** `<cls>` is token 0, so residue `idx` is token `1 + idx` ("add 1 for BOS").
- **Long sequences** (more than 1,024 tokens, i.e. more than 1,022 residues): masked marginals use
  `get_optimal_window`. That is a 1,024-token slice of the tokenised sequence centred on the
  masked position, clamped at the ends. It is a raw slice, so a middle window has neither `<cls>`
  nor `<eos>`. Wild-type marginals, under the default `--scoring-window optimal`, are *not*
  windowed: the whole sequence goes through the model in one pass. Under `overlapping` they are
  a sigmoid-weighted average of tiles.

## Decision

1. **`Scorer` protocol** (`scoring/base.py`): `landscape(seq, struct_tokens=None, *, fast=False)`
   returns `(L, 20)` float32 in `AA_ORDER`, `log P(a) − log P(wt)`. `fast=False` is the masked
   marginal, `fast=True` the wild-type marginal. **Registry**: a scorer name in
   `conf/model/*.yaml` maps to a module with `from_config(model_cfg, *, device, cache_dir)`. The
   module is imported only when a scorer is built, so data commands never import torch.
2. **Hugging Face `transformers`, not `fair-esm`.** Same weights (the Hub checkpoints are
   conversions of the fair-esm ones) and the same 33-token vocabulary in the same order. Each
   checkpoint is pinned by its full commit sha, read from the Hub API (`conf/model/esm2_*.yaml`).
   The model loads in float32 and runs under `torch.inference_mode()`.
3. **Masked marginal matches upstream exactly, batched.** Up to `batch_size` (16) masked copies go
   through one forward pass; this gives the same numbers as upstream's one-at-a-time loop. We mask
   residue tokens only; upstream also masks `<cls>` and `<eos>` but never reads those rows.
   Windows use a verbatim port of `get_optimal_window` (`scoring/windows.py`), tested against
   upstream's function as an oracle. This is CLAUDE.md's "each position from the window where it
   is most central": the window is centred on the position except near the ends.
4. **Wild-type marginal on long sequences: half-overlapping tiles, each position read from the
   tile whose centre is nearest.** This is a deliberate difference. Running 3,000 tokens through a
   model trained on 1,024 is outside its training distribution, and the sigmoid average mixes
   positions read near a window edge into the result. Tiles keep one forward pass per 512 tokens.
   It does not affect the gate, which uses masked marginals.
5. **The token offset is checked on every sequence.** `tokenize` requires exactly `<cls>`,
   one token per residue and `<eos>`, or raises. Tests pin it two ways: a stand-in model whose
   logits copy a known neighbour (exact, includes windows), and the real 8M model, whose
   unmasked argmax matches 72 of 76 ubiquitin residues aligned and 3 of 75 shifted by one.
6. **Cache key includes the method and the region.** CLAUDE.md keyed the cache by model,
   revision, sequence and structure tokens. Masked and wild-type marginals of the same sequence
   differ, so the scoring method is part of the key. So is the scoring region (`"full"` or
   `"msa:<a>-<b>"`, decision 8), so benchmark and website landscapes are separate, labelled
   entries (CLAUDE.md updated). The fields are JSON-encoded before hashing, so two different
   tuples cannot concatenate to the same string. An entry is the `(L, 20)` log-probabilities,
   not the landscape, so Phase 3 can reuse them as features.
7. **Device**: MPS when available, else CPU; never CUDA (`utils/device.py`). torch comes from
   PyTorch's CPU-only index on Linux (CI, Docker) and from PyPI on macOS (MPS).
8. **Benchmark scoring region** (`eval/region.py`, `conf/eval/scoring_region.yaml`). Every
   benchmark number scores an assay on the region its MSA covers (`MSA_start`..`MSA_end`) when
   that region is not the full target. This covers the model gate, the zero-shot benchmark
   results and the Phase 3 features, so our results stay comparable with ProteinGym's
   leaderboard. Website landscapes use the full target for every assay, and the five affected
   proteins are labelled there. The set is derived from the reference file. The expected
   assay ids are kept in config, and any difference between the two fails loudly, in either
   direction (the `structure_exceptions` pattern). A variant outside the scored region raises
   instead of being dropped; on the pinned release every variant of the five lies inside it.
9. **Score every position.** *Superseded for benchmark runs by ADR 0013 (2026-10-12): the
   benchmark scores only measured positions; the cache entry keeps its `(L, 20)` shape.* Website landscapes need masked marginals at every residue, not
   only at measured variants, so the scoring step computes the full `(L, 20)` landscape for
   every scored sequence. Benchmark metrics then read only the measured variants from it.

## Evidence

Our masked marginals against ProteinGym's precomputed scores, same checkpoint:

| Assay | Residues | Mutated positions | Model | Spearman | max abs. difference |
|---|---:|---|---|---:|---:|
| GCN4_YEAST_Staller_2018 | 281 | 100–141 | 8M / 650M | 1.000000 / 1.000000 | 2.4e-06 / 2.3e-05 |
| SPG1_STRSG_Wu_2016 | 448 | 264–279 | 8M / 650M | 1.000000 / 1.000000 | 1.2e-05 / 1.5e-05 |
| UBE4B_MOUSE_Starita_2013 | 1,173 | 1071–1172 (right windows) | 8M | 1.000000 | 3.4e-05 |
| ERBB2_HUMAN_Elazar_2016 | 1,255 | 650–673 (centred) | 8M | 1.000000 | 8.1e-06 |
| BRCA1_HUMAN_Findlay_2018 | 1,863 | 0–1854 (all three kinds) | 8M | 1.000000 | 1.5e-05 |
| BRCA2_HUMAN_Erwood_2022_HEK293T | 3,418 | 387–2653 (centred) | 8M | 1.000000 | 6.4e-06 |

CPU and MPS agree with each other to 4.4e-05 on the first two assays.

## The five MSA-region assays

Scoring the full target disagreed with ProteinGym on three long assays (8M: KCNH2 ρ = 0.36,
POLG_HCVJF 0.91, ZIKV 0.96). These are among the five assays whose MSA covers only part of the
target. For all five, ProteinGym's published scores were computed on the MSA region. **This is an
upstream inconsistency:** the scoring script at the pinned commit (`compute_fitness.py`) crops to
the MSA only for MSA Transformer, so these published scores do not follow the script published
with them. Evidence below: esm2_t6_8M masked marginals against ProteinGym's `ESM2_8M`, Spearman on
the full target vs the MSA region (max abs. difference on the region).

- `A0A140D2T1_ZIKV_Sourisseau_2019` (MSA 281–804 of 3,423 residues): ρ 0.9603 on the full
  target; exact on the MSA region (ρ 1.0000, 2.7e-05).
- `KCNH2_HUMAN_Kozek_2020` (MSA 535–565 of 1,159): ρ 0.36 (0.3613) on the full target; exact on
  the MSA region (ρ 1.0000, 4.3e-06).
- `POLG_CXB3N_Mattenberger_2021` (MSA 1–861 of 2,185): ρ 0.9917 on the full target; exact on the
  MSA region (ρ 1.0000, 8.8e-06).
- `POLG_HCVJF_Qi_2014` (MSA 1984–2089 of 3,033): ρ 0.9111 on the full target; exact on the MSA
  region (ρ 1.0000, 8.1e-06).
- `SCN5A_HUMAN_Glazer_2019` (MSA 1611–1642 of 2,016): ρ 0.39 (0.3901) on the full target; exact
  on the MSA region (ρ 1.0000, 8.5e-06).

On the full target, KCNH2 and SCN5A would fail the model gate's "≥ 0.99 in every assay". Through
`eval.region.score_assay` in benchmark mode, all five reproduce ProteinGym with the numbers above,
and two full-target controls (GCN4, SPG1) stay exact.

## Plan for scoring all positions

Estimate for 650M masked marginals on MPS, from the two measured runs (about 2.9e-4 s per token
per pass, batch 16): about 4 hours for full landscapes of the 187 distinct target sequences
(72,933 residues). The 13 targets longer than 1,022 residues take about half of that. The five MSA
regions add about 5 minutes. Landscapes are computed once per distinct sequence and region and
cached. A full-target benchmark landscape and the website landscape are the same cache entry
(region `full`), so the benchmark adds only the five regions. The run is resumable because
every finished sequence is already on disk.

## Consequences

- `make test` loads `esm2_t6_8M` from the Hub (cached in CI). Two tests compare it with
  ProteinGym when the release is present. Region tests check the rule on a toy reference and,
  when the release is present, that the five use their MSA region in benchmark mode, the other
  212 the full target, and landscape mode the full target for all 217. Three `slow` tests
  cover the long-sequence windows (about 3.5 minutes on MPS).
- Throughput, 650M masked marginal on MPS (M4 Pro): 22.5 s for 281 residues, 58.9 s for 448.
  The wild-type marginal takes 0.1–0.2 s for the same sequences.
- SaProt will implement the same protocol and reuse `windows.py` and the cache.
