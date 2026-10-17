# 0029 — ESM-2 35M / 150M on ONNX Runtime for CPU serving

- Status: accepted (export, scorer, checks, publication). Open: which precision the API serves
  (section 6).
- Date: 2026-10-17
- Builds on: ADR 0010 (model gate), ADR 0012 (ESM-2 scorer, windows), ADR 0013 (cache), ADR
  0014 (paired comparisons, tracking), ADR 0026 addendum (pinned Hub revisions)

## Context

The API scores user sequences live on the free Hugging Face Space: 2 vCPUs, no GPU, linux/amd64
(CLAUDE.md). PyTorch would add several hundred MB to the image and seconds to a cold start, and
the plan was always ONNX Runtime with int8-quantized ESM-2 35M and 150M. The PyTorch scorers
are pinned and gated (ADR 0012, 0017). The question is how to make an ONNX scorer that is
provably the same model, and to measure what quantization and the CPU cost.

## Decision

### 1. optimum exports a local snapshot of the pinned checkpoint, in its own environment

`mutascope onnx export` downloads the pinned revision (`conf/model/esm2_{35m,150m}.yaml`) as a
local snapshot and runs `optimum-cli export onnx --task fill-mask --opset 18` on that folder.
optimum never resolves a revision, so it cannot export anything other than the pinned commit.
The graph takes `input_ids` and `attention_mask` (`batch × tokens`, int64) and returns `logits`.
Rotary embeddings are computed from `Range`, and GELU is exact (`Erf`), so nothing is fixed to
the dummy input's length.

optimum's ONNX exporter (`optimum-onnx` 0.1.0, and its `main` on 2026-10-17) requires
`transformers < 4.58`. MutaScope runs transformers 5.19, and forcing optimum-onnx onto it fails
at import (`main_export` cannot be imported). The exporter therefore runs in a separate locked
uv project, `tools/onnx-export` (optimum-onnx 0.1.0, optimum 2.1.0, transformers 4.57.6, torch
2.14.1), called by the CLI as `uv run --project tools/onnx-export --frozen`. The two transformers
versions meet in check 1, which compares the export with MutaScope's own PyTorch model. The
export is deterministic: two runs gave bit-identical files (sha256 of all four 35M files).

Per model, `build/onnx/<model>/` holds `fp32/model.onnx`, `int8/model.onnx`,
`tokenizer.json`, `config.json` and `export.json` (source revision, export environment
versions, quantization recipe, sha256 of every file). The Hub repo has the same layout.

### 2. The tokenizer is a `tokenizers` file, because transformers' ESM tokenizer imports torch

In transformers 5.19, loading `EsmTokenizer` imports torch (`'torch' in sys.modules` after
`AutoTokenizer.from_pretrained`). The export writes ESM's vocabulary as a `tokenizers`
`tokenizer.json`: WordLevel over `vocab.txt`, one token per character, `<cls> … <eos>`
template. Before anything is written, it must give transformers' ids on 200 random sequences
(1–1,022 residues) and on the five parity targets. The scorer checks the token layout on every
sequence, as the PyTorch scorer does.

### 3. One torch-free core for both runtimes

`scoring/marginals.py` (`MarginalScorer`) now holds what the PyTorch scorer did around the
model: the cache (which rows an entry has, which go through the model, NaN for the rest; ADR
0013), the windows and the batching of masked passes (ADR 0012). `MaskedLMScorer` (PyTorch:
ESM-2, SaProt) and `OnnxEsm2Scorer` (`scorer: esm2_onnx`) only encode tokens and run forward
passes. No number changed: the existing scoring, feature and landscape tests pass unchanged
(109), including the real 8M checkpoint against ProteinGym. With the same stand-in function in
both runtimes, the two scorers agree within 1e-5 on masked and wild-type marginals, windowed and
not.

- `OnnxEsm2Scorer` imports neither torch nor transformers. A test builds and runs it in a fresh
  interpreter and fails if either module is loaded.
- Cache keys use `hf_id/file` as the model id (`cache_model_id`). The fp32 and int8 files share
  a repo and a revision, and must not share entries.
- `model.local_dir=build/onnx` reads an export instead of the Hub, with the cache off: an
  unreleased file has no revision to key entries by.
- CPU only (`device` must be `auto` or `cpu`). `threads` sets ONNX Runtime's intra-op threads;
  inter-op is 1.

The ONNX configs are `conf/model/esm2_{35m,150m}_int8.yaml` and
`esm2_{35m,150m}_onnx_fp32.yaml` (`onnx.scorers`). Their `revision` is
`${onnx.publish.revision}`, so the benchmark, the API and the card read one pin.

### 4. Quantization: fixed before any result

ONNX Runtime 1.31 `quantize_dynamic`, recorded in `onnx.quantization`. Every MatMul with a
constant weight is quantized, with int8 weights per output channel. Activations are quantized
per tensor at run time. `reduce_range=True` (7-bit weights): on x86 CPUs without VNNI, ONNX
Runtime's u8 × s8 kernel adds pairs of products into int16, and 255 × 127 × 2 overflows it.
With 7-bit weights, 255 × 63 × 2 = 32,130 fits. The same file therefore gives the same numbers
on any CPU, including the Space's, whose CPU we do not know. The recipe was chosen before the
pilot on random sequences, which only confirmed it was in range (landscape Spearman 0.987–0.998
against fp32), and before any check below. A second pilot, on the five parity targets, showed
that dropping `reduce_range` would raise fast-landscape agreement with fp32 by about 0.001. The
recipe kept it, for the portability argument above.

### 5. Three checks, and what they found

1. **fp32 logits against PyTorch** (`onnx check-parity`, `results/onnx/logit_parity.parquet`).
   The five parity targets sit at the length quantiles of the 201 targets that fit one window
   (37, 66, 222, 448, 934 residues). Each is checked unmasked and with 8 residues masked, one
   per row, over every token and vocabulary entry, against PyTorch on the CPU. Tolerance 1e-4.
   **Passed:** max |Δ| 6.7e-5 (35M) and 9.0e-5 (150M), both at the 934-residue MSH2 masked
   input. 150M is within 10% of the tolerance. The tolerance was not changed after seeing this,
   and longer inputs are worth watching. fp32 landscapes against PyTorch on MPS (the device that
   computed the published landscapes): Spearman 1.00000 everywhere, max |Δ| 7.9e-5 nats. For
   scale, int8 logits differ by up to 1.76.
2. **int8 fast-mode scores against PyTorch** (`onnx check-scores --fast`,
   `results/onnx/score_parity_<label>.parquet`). These are the model gate's two checks (ADR
   0010), with our PyTorch wild-type-marginal score set as the reference, on all 217 assays
   and 696,311 single mutants. **Failed.** The aggregate holds, but the per-assay threshold
   does not:

   | | assays < 0.99 | min per-assay Spearman | 1st pct | median | aggregate vs PyTorch |
   |---|---|---|---|---|---|
   | 35M int8 | 3 | 0.9780 (NPC1_HUMAN_Erwood_2022_RPE1, 63 variants) | 0.9889 | 0.9975 | 0.3023 vs 0.3028 |
   | 150M int8 | 1 | 0.9819 (DN7A_SACS2_Tsuboyama_2023_1JIC, 1,008 variants) | 0.9932 | 0.9980 | 0.3766 vs 0.3772 |

   The other two 35M failures are VILI_CHICK_Tsuboyama_2023_1YU5 (0.9869) and
   NRAM_I33A0_Jiang_2016 (0.9882). The fp32 export, checked the same way
   (`esm2_*_onnx_fp32`), **passes**: per-assay Spearman 1.0000 at four decimals in every assay,
   and the same aggregate. So all of the disagreement comes from quantization.
3. **Benchmark change, paired bootstrap** (`eval compare`, 10,000 resamples, pairs
   `[esm2_*_int8_wt, esm2_*_wt]` in `eval.comparisons`, 217 assays). Spearman: 35M −0.0005
   [−0.0013, +0.0002], 150M −0.0006 [−0.0012, +0.0002]. Both include 0, and int8 is better in
   6.8% and 6.4% of resamples. Some secondary metrics move with CIs that exclude 0: 35M AUC
   −0.0004 [−0.0009, −0.0001] and top-10% recall −0.0059 [−0.0103, −0.0003]; 150M MCC −0.0019
   [−0.0031, −0.0009] and top-10% recall +0.0014 [+0.0002, +0.0027]. The eight existing pairs
   are bit-identical after the rerun.

The masked marginal (precise mode) is not benchmarked in int8. On the five parity targets its
landscape Spearman against PyTorch is ≥ 0.9966 (35M) and ≥ 0.9967 (150M).

**Post-hoc diagnostic (not adopted; the gate was not re-run).** After check 2 failed, other
recipes were scored for fidelity to PyTorch only, never against DMS labels, all 217 assays
(assays below 0.99, min):

| recipe | 35M | 150M |
|---|---|---|
| fp32 (none) | 0, 0.99999996 | 0, 0.99999997 |
| R0: per-channel, reduce_range (this ADR) | 3, 0.9780 | 1, 0.9819 |
| R1: per-channel | 1, 0.9822 | 1, 0.9865 |
| R2: R0 with the LM head in fp32 | 2, 0.9765 | 1, 0.9819 |
| R3: R1 with the LM head in fp32 | 1, 0.9835 | 1, 0.9877 |

No dynamic-int8 recipe passes, and the failing assay moves between recipes. On the five parity
targets only, keeping any one group of MatMuls in fp32 (query, key, value, attention output,
FFN in, FFN out, LM head dense, LM head decoder) reduces the mean (1 − ρ) of the 35M fast
landscape from 0.00250 by at most 21% (FFN out: 0.00197). The error is spread over the whole
network, so it is not a few outlier layers that mixed precision could isolate. A recipe that
passed after more search would have been tuned to the gate it is judged by. R0 stays.

### 6. Open: the precision the API serves

The int8 files fail the per-assay gate on 3 + 1 assays and pass everything else; the fp32
files pass every check. The API is the next task, and the choice is the author's, not this
ADR's. Two options, both already published:

- **fp32 ONNX** (`esm2_*_onnx_fp32`): passes check 2. Per-assay Spearman against PyTorch is
  1.0000 at four decimals in all 217 assays, and the aggregate is the same (0.3028, 0.3772).
  135 MB / 594 MB.
- **int8 ONNX** (`esm2_*_int8`), **with the gate miss documented:** 35 MB / 152 MB; the
  aggregate is unchanged within the CIs above.

**Latency** (`onnx latency`, `results/onnx/latency.parquet`). Wall time of one `landscape()`
call, in seconds. Machine: Apple M4 Pro, ONNX Runtime 1.31 CPU provider, 2 intra-op threads to
match the Space. Random sequences, the same for every model. Fast mode: median of 10 calls
after one warm-up (at L = 1,000 the 10 calls span at most 0.075 s). Precise mode:
the masked marginal at every residue, one call (L passes, 16 per call):

| mode | model | L = 100 | 300 | 600 | 1,000 |
|---|---|---|---|---|---|
| fast | 35M fp32 / int8 | 0.014 / 0.014 | 0.043 / 0.045 | 0.113 / 0.117 | 0.247 / 0.265 |
| fast | 150M fp32 / int8 | 0.040 / 0.043 | 0.107 / 0.131 | 0.256 / 0.322 | 0.530 / 0.676 |
| precise | 35M fp32 / int8 | 0.99 / 1.19 | 11.0 / 13.4 | 63.9 / 71.1 | 241 / 253 |
| precise | 150M fp32 / int8 | 3.2 / 4.0 | 32.7 / 40.0 | 162 / 175 | 554 / 591 |

int8 is not faster on this CPU. It is 0–28% slower: ONNX Runtime's ARM path quantizes the
activations of every MatMul at run time and multiplies in int8, and at these sizes that costs
more than the fp32 GEMM it replaces. x86 CPUs with VNNI may do better, and the Space is shared
x86 vCPUs, slower than an M4 core. So these numbers are a lower bound, to be measured again on
the Space. Fast mode stays under 0.7 s up to 1,000 residues, within the Phase 4 target of a
fast-mode response under 2 s, with headroom for a slower CPU. Precise mode takes minutes beyond
a few hundred residues (150M: 9–10 min at 1,000), so it has to be the asynchronous job with
streamed progress that the API plan already has (`/v1/jobs`, SSE).

**Recommendation:** serve the fp32 files. They pass every check, are as fast or faster here, and
the 594 MB of the larger model fits the Space's memory. Re-measure on the Space first. If int8
is clearly faster there, it is a trade of a documented gate miss for speed, and that decision is
the author's.

### 7. Publication

`mutascope onnx publish --push` stages `build/hf_onnx/`: both models' export directories, the
check, latency and benchmark tables of the served models, their paired comparisons, the
configs, the model card (`docs/ONNX_MODEL_CARD.md`) and `SHA256SUMS`. It uploads them as one
commit to `engAbdullahHanif/mutascope-esm2-onnx`, pinned as `onnx.publish.revision`. The repo
was created private, checked, then made public, as for the other repos (ADR 0026 addendum).
The benchmark above was run from the local export before the push. It was rerun from the pinned
Hub revision after it, with the same scores.

## Consequences

- The API can score without torch or transformers: onnxruntime, tokenizers, numpy and
  huggingface-hub (the `onnx` extra adds onnxruntime, onnx and tokenizers).
- `onnx export` needs `uv` on `PATH` and the `tools/onnx-export` lock. A new optimum or
  transformers version means re-locking that project and re-running check 1.
- `make test` covers the scorer with stand-in graphs, the tokenizer, the quantizer, the helpers
  and `check-scores`. `make test-slow` checks the published 35M int8 file against PyTorch.
- Not a Nextflow process: the export and the checks run once per release, like `export push`.

## Alternatives considered

- **`torch.onnx.export` from MutaScope's environment:** no second environment, but it gives
  up optimum's tested ESM export config and output validation, which the task asked for.
- **Forcing optimum-onnx onto transformers 5 (`--no-deps`):** fails at import.
- **optimum's `ORTQuantizer` presets** (`avx2`, `avx512_vnni`, `arm64`): a thin wrapper over
  the same `quantize_dynamic`, with platform-specific choices. One explicit recipe that behaves
  the same on the development Mac and the Space is easier to defend.
- **Static (calibrated) quantization:** needs calibration data and per-platform kernels, and
  dynamic int8 was the plan. Not tried.
