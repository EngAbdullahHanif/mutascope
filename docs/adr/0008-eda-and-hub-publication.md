# 0008 — EDA notebook and Hugging Face Hub publication

- Status: accepted
- Date: 2026-10-10

## Context

Phase 1 ends with an exploratory notebook and the processed dataset on the Hugging Face Hub
with a data card. Notebooks must import from the package and hold no logic (CLAUDE.md). The
processed data is partitioned by assay with `assay_id` only in the directory name (ADR 0005).
Publishing is outward-facing and hard to take back once a dataset is public, and the card had
to be reviewed before anything was uploaded.

## Decision

- **marimo notebook, logic in `mutascope.eda`.** `eda.summary` computes every table in DuckDB
  over the existing views (`assay_overview`, `function_type_summary`, two score histograms);
  `eda.charts` turns those frames into Altair charts. `notebooks/eda.py` only arranges them.
  Data paths come from the Hydra config (`data.processed_dir`, `data.reports_dir`, added here)
  and are resolved against the repo root (`mo.notebook_dir().parent`), so the notebook works
  from any launch directory.
- **Bin in SQL, not in the browser.** 696,311 rows are too many for a Vega-Lite chart (Altair
  refuses more than 5,000 by default). Histograms are binned in DuckDB and only the bins reach
  the chart. Raw scores are z-scored within each assay before pooling, because units differ per
  assay.
- **Charts:** a single blue for one-series charts, blue/orange (a colourblind-validated pair)
  for the fit/unfit split, small multiples for the five function types instead of five
  overlaid colours. Log-scaled axes are labelled in original units.
- **Hub layout: one Parquet file per table, `assay_id` as a column.** The `datasets` loader and
  the Hub viewer do not read hive partition paths, so the publish step writes `data/variants`,
  `data/assays`, `data/structure_features` and `data/three_di` as single sorted files (~10 MB
  in total). Each is validated against its pandera schema first. The `assays` table is a new
  schema (`PublishedAssaySchema`): a subset of ProteinGym's reference file, plus
  `has_structure`.
- **Stage and push are separate.** `mutascope data publish` only stages `publish.stage_dir`:
  tables, reports, `README.md` (generated front matter + `docs/DATA_CARD.md`),
  `provenance.json` (release pin, input checksums, Foldseek version) and `SHA256SUMS`. `--push`
  uploads that folder as one commit, after re-verifying the checksums, so what is uploaded is
  exactly what was reviewed. New repos are created private; visibility is changed by hand.
- **Cross-check before staging.** Each assay's `DMS_number_single_mutants` must equal its
  processed row count, or nothing is staged.
- **Licence:** MIT, the licence of ProteinGym v1.3 (Zenodo record and GitHub repository).

## Consequences

- Staging the full dataset takes ~3 s and is byte-identical across runs.
- The Hub copy duplicates the local data in a different layout; `SHA256SUMS` and
  `provenance.json` tie it to the pinned inputs.
- New dependencies: `huggingface_hub` (runtime, used by `publish`), `marimo` and `altair` (the
  `notebooks` extra).
- The token needs write access to one dataset repo; it is read from `HF_TOKEN` or the cached
  `hf auth login`, never from config.
