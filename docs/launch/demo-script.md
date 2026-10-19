# Demo video script (about 120 s)

Recorded on the deployed site, https://mutascope.abdullahhanif.me, in the light theme, at a
1440 × 900 window. Every shot shows what the site does today. Numbers are listed with their
source rows at the end; say them as written.

Before recording: open `/score` once and wait for the server to wake (about 20 s after idle),
so the live section does not open on "Waking up the server…".

| # | Time | Section | On screen | Narration |
| --- | --- | --- | --- | --- |
| 1 | 0–12 s | Problem | Landing page, top: the headline sentence and the rotating GFP structure. | "A protein of 300 residues has almost six thousand possible single substitutions. Labs measure some of them with deep mutational scans. MutaScope predicts all of them, and checks the predictions against those measurements." |
| 2 | 12–35 s | Explorer | Click the GFP example → `/explore/GFP_AEQVI_Sarkisyan_2016`. Move the pointer across the heatmap; hover a cell so the tooltip shows the prediction, its 80% and 90% intervals and the measured value. Switch Predicted → Measured and back. Open the model menu to show the zero-shot options. | "This is GFP. Each column is a residue, each row the amino acid it could become. Blue is less fit, red is fitter. The default here is a supervised model trained on this assay's 1,084 measurements, and every prediction carries an interval. I can switch to the measurements themselves, or to the zero-shot language models." |
| 3 | 35–50 s | 3D linking | Scroll so the heatmap and the structure are both visible. Hover heatmap columns: the residue lights up in 3D. Click a residue in 3D: its column is selected. Switch "Colour residues by" to pLDDT and back. | "The heatmap and the AlphaFold structure are linked both ways. Hover a column and the residue lights up; click the structure and the heatmap selects that residue. I can also colour by AlphaFold's confidence." |
| 4 | 50–72 s | Live scoring | `/score`. Click the Ubiquitin example, Fast, Score: the landscape appears, labelled "Zero-shot, ESM-2 35M". Switch to Precise and start the job: residues fill in as the progress bar moves. Cancel after a few seconds (the scored residues stay; the rest are grey). Then click the PTEN example (403 residues) with Precise selected: the length message appears. | "Any sequence can be scored live. Fast mode is one pass of ESM-2 35M, a small zero-shot model, on a CPU server: a few seconds. Precise mode masks each residue in turn and streams the results as they finish; it is limited to 400 residues so a job stays within a few minutes. This is zero-shot only: no lab data, no structure." |
| 5 | 72–92 s | Benchmarks | `/benchmarks`. Scroll the zero-shot dot plot (paired differences), then the supervised ladder with its three split panels, stopping on the contiguous panel. | "Every number here is a paired bootstrap difference between two models on the same assays, never two error bars side by side. The split that matters is contiguous: whole blocks of positions are held out, so the model has to predict residues it never saw." |
| 6 | 92–108 s | Engineering | Cut to the GitHub repository: the README's architecture diagram, then `docs/adr/` (the list of ADRs), then the green checks on a merged pull request. | "Underneath: a Nextflow pipeline that calls one Python command per step, pinned and checksummed inputs, and a reproduction test that recomputes ProteinGym's published ESM-2 result before anything else counts. The site is static files; the API is a torch-free container on Cloud Run that sleeps when idle. Every design decision is written down." |
| 7 | 108–122 s | Findings | Back to the Landing page's three headline numbers. | "What I found: adding structure helps, SaProt beats ESM-2 by 0.046 Spearman. A ridge model trained per assay beats zero-shot SaProt by 0.092 on unseen positions. Its 90% intervals cover 90.8% of held-out measurements. These are lab assay effects, not clinical predictions." |

Total: 122 s. If it runs long, shorten section 6 first.

## Numbers and their sources

All from `benchmarks.json` in `bundle-v1.1.0` (revision `1bfc294`), the rows the Landing page
reads (`headlines()` in `web/src/lib/benchmarks/select.ts`), rounded as the page rounds them.

| Said | Source row |
| --- | --- |
| SaProt beats ESM-2 by 0.046 | `zero_shot_comparisons`: `saprot_650m_af2` − `esm2_650m`, overall Spearman, `diff` 0.0456, CI [+0.028, +0.062], 213 assays |
| Ridge beats zero-shot SaProt by 0.092 on unseen positions | `supervised_comparisons`: `ridge_seq_struct` − `zero_shot_saprot_score`, `split` contiguous, overall Spearman, `diff` 0.0919, CI [+0.065, +0.115], 213 assays |
| 90% intervals cover 90.8% | `calibration`: contiguous, `mode` scheme, `level` 0.9, `size_bin` all, `statistic` mean = 0.9075 (`n_below` 0 of 213) |

Other figures in the narration come from the site or the deployed config, not from
`benchmarks.json`: 1,084 measured variants (GFP's manifest entry), 400 residues
(`MUTASCOPE_API_PRECISE_MAX_LENGTH` in `conf/deploy/cloudrun.yaml`, read by the page from
`/ready`), about 20 s to wake (the previous README and CLAUDE.md; not re-measured for this script).

## Check before recording

- "Almost six thousand substitutions" for 300 residues is 19 × 300 = 5,700. Arithmetic, not a
  result.
- How long a precise Ubiquitin (128 residues) job takes on the deployed service is not
  measured; only 400 residues is (293 s with one ONNX thread, `conf/deploy/cloudrun.yaml`). The script cancels after a few
  seconds, so it does not depend on it.
- Check that the PTEN example shows the length message in Precise mode before you rely on the
  shot (it should, since 403 > 400).
- Section 6 says the reproduction test "recomputes ProteinGym's published ESM-2 result". It is
  the harness gate: our metrics on ProteinGym's own ESM-2 650M scores reproduce their published
  average Spearman. If that is too compressed, say "checks our benchmark code against ProteinGym's
  published leaderboard".
