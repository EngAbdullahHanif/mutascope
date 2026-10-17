# MutaScope ESM-2 35M / 150M for ONNX Runtime (fp32 and int8)

ONNX exports of Meta's ESM-2 protein language models
[`facebook/esm2_t12_35M_UR50D`](https://huggingface.co/facebook/esm2_t12_35M_UR50D) (revision
`6fbf070`) and [`facebook/esm2_t30_150M_UR50D`](https://huggingface.co/facebook/esm2_t30_150M_UR50D)
(revision `a695f60`). Each comes as an fp32 graph and as a graph with int8 weights. MutaScope
uses them to score amino-acid substitutions on a CPU without PyTorch, for the live scoring API of
[MutaScope](https://github.com/EngAbdullahHanif/mutascope). Their design and checks are recorded
in ADR 0029 of that repository.

Scores are **zero-shot predictions of functional effect in laboratory deep mutational scanning
(DMS) assays**. They are **not clinical pathogenicity predictions and must not be used for
clinical or diagnostic decisions.**

## Files

| path | what |
|---|---|
| `esm2_35m/fp32/model.onnx`, `esm2_150m/fp32/model.onnx` | optimum export (`fill-mask`, opset 18): `input_ids`, `attention_mask` (int64, batch × tokens) → `logits` (batch × tokens × 33) |
| `esm2_*/int8/model.onnx` | the fp32 graph with every constant-weight MatMul dynamically quantized (ONNX Runtime 1.31: int8 weights per channel, 7-bit range, activations quantized at run time) |
| `esm2_*/tokenizer.json` | ESM's 33-token vocabulary for the `tokenizers` library: one token per residue, `<cls> … <eos>` |
| `esm2_*/config.json` | the checkpoint's Hugging Face config |
| `esm2_*/export.json` | source revision, export environment (optimum-onnx 0.1.0, transformers 4.57.6, torch 2.14.1), quantization recipe, sha256 of every file |
| `results/` | the checks, timings and benchmark below, as Parquet |
| `config/` | the MutaScope configs that produced them |
| `SHA256SUMS` | every file's sha256 |

Sizes: 35M fp32 134.5 MB, int8 34.8 MB. 150M fp32 593.8 MB, int8 151.8 MB.

## Use

```python
import numpy as np
import onnxruntime as ort
from huggingface_hub import hf_hub_download
from tokenizers import Tokenizer

repo, revision = "engAbdullahHanif/mutascope-esm2-onnx", "main"  # pin a commit in practice
model = hf_hub_download(repo, "esm2_35m/int8/model.onnx", revision=revision)
tokenizer = Tokenizer.from_file(hf_hub_download(repo, "esm2_35m/tokenizer.json", revision=revision))

sequence = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"
ids = np.array([tokenizer.encode(sequence).ids], dtype=np.int64)  # residue i is token i + 1
session = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
(logits,) = session.run(["logits"], {"input_ids": ids, "attention_mask": np.ones_like(ids)})
```

MutaScope's scorer (`mutascope.scoring.onnx_esm2`) turns logits into the score of every
substitution: `log P(mutant) − log P(wild type)` from a log-softmax over the whole vocabulary.
*Fast* mode takes them from one unmasked pass (wild-type marginal). *Precise* mode masks each
residue in turn (masked marginal, ProteinGym's method). Sequences of more than 1,022 residues
are windowed as in the PyTorch scorer.

## How they were checked

Against MutaScope's PyTorch scorer (transformers 5.19), which reproduces ProteinGym's published
ESM-2 scores (per-assay Spearman ≥ 0.99 in every assay; MutaScope ADR 0017):

- **fp32 logits** on five ProteinGym targets of 37 to 934 residues, unmasked and with 8 residues
  masked, every token and vocabulary entry: max |Δ| 6.7e-5 (35M) and 9.0e-5 (150M), within
  the 1e-4 tolerance. Fast-mode scores of all 217 ProteinGym v1.3 assays: per-assay Spearman
  against PyTorch 1.0000 in every assay, and the same aggregate.
- **int8 fast-mode scores**, all 217 assays (696,311 single mutants), per-assay Spearman against
  PyTorch: median 0.9975 (35M) and 0.9980 (150M), minimum 0.9780 and 0.9819. **3 assays (35M)
  and 1 (150M) fall below MutaScope's 0.99 threshold**, so the int8 files do not pass that
  check. No other dynamic-int8 recipe tried did (ADR 0029).
- **Benchmark** (fast mode, ProteinGym-style aggregate Spearman against `DMS_score`, 95%
  bootstrap CI over assays):

  | | PyTorch | int8 ONNX | int8 − PyTorch (paired bootstrap) |
  |---|---|---|---|
  | ESM-2 35M | 0.3028 [0.2768, 0.3290] | 0.3023 [0.2762, 0.3285] | −0.0005 [−0.0013, +0.0002] |
  | ESM-2 150M | 0.3772 [0.3537, 0.4028] | 0.3766 [0.3532, 0.4023] | −0.0006 [−0.0012, +0.0002] |

  The fp32 files reproduce the PyTorch column. Precise mode, the stronger method, scores 0.3113
  (35M) and 0.3820 (150M) with PyTorch. Its int8 landscapes on the five check targets agree with
  PyTorch at Spearman ≥ 0.9966.

## Speed

Wall time of one full landscape (all 19 × L substitutions), in seconds, with ONNX Runtime 1.31
on 2 threads of an Apple M4 Pro. Fast mode: median of 10 calls. Precise mode: one call, L
masked passes.

| mode | model | L = 100 | 300 | 600 | 1,000 |
|---|---|---|---|---|---|
| fast | 35M fp32 / int8 | 0.014 / 0.014 | 0.043 / 0.045 | 0.113 / 0.117 | 0.247 / 0.265 |
| fast | 150M fp32 / int8 | 0.040 / 0.043 | 0.107 / 0.131 | 0.256 / 0.322 | 0.530 / 0.676 |
| precise | 35M fp32 / int8 | 0.99 / 1.19 | 11.0 / 13.4 | 63.9 / 71.1 | 241 / 253 |
| precise | 150M fp32 / int8 | 3.2 / 4.0 | 32.7 / 40.0 | 162 / 175 | 554 / 591 |

int8 is not faster on this ARM CPU. x86 CPUs with VNNI may behave differently, and a shared
cloud vCPU is slower than an M4 core.

## Limitations

- Zero-shot scores reflect evolutionary plausibility learned from UniRef50, not a measured
  phenotype. On ProteinGym they correlate with DMS measurements at the levels above, with wide
  variation between assays.
- The int8 files trade a small, measured loss of agreement with PyTorch for size (see above).
- Numbers were checked with ONNX Runtime 1.31 on the CPU. Other versions or execution providers
  may differ in the last digits.

## License and citation

MIT, like ESM-2. If you use these files, cite ESM-2: Lin et al., "Evolutionary-scale prediction
of atomic-level protein structure with a language model", *Science* 379 (2023). For the
benchmark, cite ProteinGym: Notin et al., "ProteinGym: Large-Scale Benchmarks for Protein Fitness
Prediction and Design", *NeurIPS* 2023 Datasets and Benchmarks.
