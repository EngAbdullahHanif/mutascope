# 0030 — The API core: pinned bundle, read-only routes, one error format

- Status: accepted
- Date: 2026-10-17
- Builds on: ADR 0028 (website bundle, `export.publish.revision`), ADR 0029 (fp32 ONNX
  scorers, `onnx.publish.revision`, torch-free scorer test)

## Context

Task 1 published the website bundle and pinned it. Task 2 made a torch-free ESM-2 scorer and
chose the fp32 ONNX files for serving. The API has to serve the bundle's content
(proteins, landscapes, benchmarks) and hold the live scorers that later tasks will use for
`/v1/score` and `/v1/jobs`. It must not import torch or transformers. A response must never mix
up two bundles or two models, and every response has to say whether it is supervised or
zero-shot.

## Decision

### 1. Settings are pins, tested against `conf/`

`api.app.settings.Settings` (pydantic-settings, env prefix `MUTASCOPE_API_`) holds the dataset
repo, `bundle_path`, `bundle_revision` (`3ae7672`, tag `bundle-v1.0.0`), the served model
configs (`esm2_35m_onnx_fp32`, `esm2_150m_onnx_fp32`), `onnx_revision` (`82e1528`),
`onnx_threads` (2, the Space's vCPUs), the protein LRU size and `Cache-Control`. Revisions must
be 40-character commit hashes, so a branch or tag name fails validation. The API is configured
by environment, not Hydra, but a test fails if any default differs from the pin in
`conf/` (`publish.repo_id`, `export.publish.*`, `onnx.publish.revision`). There is still one
source of truth, and the copy is checked against it.

The scorers are built from `conf/model/<name>.yaml` with the settings' revision and thread
count. A config that is not `scorer: esm2_onnx` with `precision: fp32` is refused at startup:
int8 stays rejected (ADR 0029, section 6), and a PyTorch config cannot slip in.

### 2. Startup loads everything once, and refuses rather than degrades

The `lifespan` hook:

1. downloads `bundle/` of the dataset repo at the pinned commit (`snapshot_download`,
   `allow_patterns`), never `data/processed`;
2. checks every file in the folder's `SHA256SUMS` (223 files) and that every protein file in
   the manifest is present;
3. reads `schema_version` from `manifest.json` and `benchmarks.json` before parsing them, and
   raises unless it is in `SUPPORTED_SCHEMA_VERSIONS` (`{"1.0.0"}`). The bundle's Pydantic
   models forbid unknown fields, so even a minor version is only accepted once the code lists
   it;
4. loads both fp32 ONNX scorers (cache off: live scoring keys nothing by revision yet).

Any failure raises, and uvicorn does not start. A server that is up has passed all four.
`/health` is liveness only. `/ready` returns 200 with the schema version, bundle revision, protein count
and model names, or a 503 problem before startup completes.

Protein files are parsed on first request and kept in an LRU (`protein_cache_size`, 32).
Parsing all 217 (79 MB of JSON) raised peak RSS by 462 MB on the M4 Pro, too much to keep
next to two ONNX models on the Space. Each file is checked against the
manifest's sha256 when it is read, so a file changed after startup is refused too.

`bundle_dir` / `onnx_local_dir` read local copies instead (tests, development). A local bundle
may hold a subset of the protein files; it still needs `SHA256SUMS`, and `bundle_revision`
is reported as `null`, so a response never claims a Hub revision it did not come from.

### 3. Routes

| Route | Returns |
|---|---|
| `GET /v1/proteins?function_type=&q=` | every assay with its default model and label; `function_type` is one of the five ProteinGym types (else 422), `q` a case-insensitive substring of assay id, UniProt id or molecule name |
| `GET /v1/proteins/{id}` | the bundle's `AssayInfo`, default model, label and reason, MSA region, and the landscapes on offer |
| `GET /v1/proteins/{id}/landscape?model=default\|esm2_650m\|saprot` | one landscape (below) |
| `GET /v1/benchmarks` | `benchmarks.json` unchanged |
| `GET /health`, `GET /ready` | probes |

Every `/v1` response carries `version`: the API version, the bundle's Hub revision and the
bundle's own `VersionStamp`, which names the code, dataset and model revisions behind every number.

**Landscapes** are a discriminated union on `kind`:

- `supervised` (the default where a supervised model exists): `prediction`, `lower_80`,
  `upper_80`, `lower_90`, `upper_90`, `measured`, `oof_prediction`, `position_status`,
  `calibration_scheme`, `structure_imputed`, `n_measured`, `levels`, and the label
  `"Supervised (n = 621)"` from the manifest. `oof_prediction` is `prediction` at measured
  variants and null elsewhere. In the bundle, a measured variant's prediction already comes
  from the fold model that never saw it (ADR 0025). The extra grid makes that pairing
  explicit, so a client cannot compare an all-data prediction with a measurement by mistake.
- `zero_shot`: `scores`, `measured`, the model's Hub id and revision, label `"Zero-shot"`, and
  for ESM-2 the MSA region the benchmark used (ADR 0012).

The fixed-point integers are decoded (`q / 10**decimals`, correctly rounded) into plain
numbers. A client of the API needs no decoding rule; the static site keeps reading the compact
bundle itself. Grids keep the bundle's `(L, 20)` row-major layout and `aa_order`.

### 4. One error format: RFC 9457 problem details

Every error is `application/problem+json` with `type`, `title`, `status`, `detail` and
`instance` (the path):

| Case | Status | `type` |
|---|---|---|
| unknown protein | 404 | `urn:mutascope:problem:protein-not-found` |
| model without a landscape for that protein (SaProt and no structure) | 404 | `urn:mutascope:problem:model-not-available`, detail names the reason and "213 of 217" |
| bad query parameter | 422 | `urn:mutascope:problem:invalid-request`, plus `errors` (`loc`, `msg`, `type`) |
| not started | 503 | `urn:mutascope:problem:not-ready` |
| unknown route, wrong method | 404, 405 | `about:blank` (RFC 9457: the status says it all) |
| anything unhandled | 500 | `about:blank`, no internals in `detail` |

The URN scheme matches the bundle schemas' `$id` (`urn:mutascope:bundle:…`) and promises no
web page that does not exist. The OpenAPI schema documents these responses with a `Problem`
component, and FastAPI's default `HTTPValidationError` no longer appears.

### 5. ETag, Cache-Control, gzip

A pure ASGI middleware buffers successful `GET`s under `/v1/proteins` and `/v1/benchmarks`,
sets `ETag: W/"<sha256 of the body, 32 hex>"` and `Cache-Control: public, max-age=3600`, and
answers a matching `If-None-Match` with `304` and no body. The tag is computed from the body,
not the bundle revision, so it also changes when a code change alters a response. It is weak
because gzip (Starlette's `GZipMiddleware`, outside it, ≥ 1,000 bytes) changes the bytes but
not the content. Errors and the probes get neither header.

### 6. Types for the frontend

`make types` writes the OpenAPI schema (`python -m api.app.openapi`) and runs
`openapi-typescript` 7.13.0 (with TypeScript 5.9.3, pnpm 12.10.1, all pinned in a minimal
`web/package.json` + lockfile, the seed of the Phase 5 app) to `web/src/lib/api-types.ts`,
which is committed. A CI job (`types`) regenerates it and fails on `git diff`. The output is
deterministic: two runs give identical files.

## Evidence

- **Golden tests** against the published files of `bundle-v1.0.0` (fetched at the pinned
  revision): every grid of the supervised landscape of `TCRG1_MOUSE_Tsuboyama_2023_1E0L`
  (37 residues, 621 measured) and the SaProt and ESM-2 landscapes of
  `F7YBW8_MESOW_Aakre_2015` equal the values decoded from the gzip JSON independently of the
  API's models, plus pinned literal cells (e.g. TCRG1 residue 0 → A: measured −0.1039,
  prediction −0.1262, 80% [−0.4863, 0.2339], 90% [−0.6714, 0.419]).
- **Startup from the Hub** (`make test-slow`, fresh interpreter): the full bundle snapshot and
  both fp32 models at the pinned revisions load in 48.6 s (the model files were already in the
  local Hugging Face cache from Task 2; the bundle was downloaded in that run),
  `/ready` is 200, both scorers score ubiquitin, and neither torch nor transformers is in
  `sys.modules`. The same no-torch check runs in `make test` with the fixture bundle and
  stand-in graphs.
- **Cost of the largest landscape** (A0A140D2T1_ZIKV, 3,423 residues, M4 Pro, protein file
  already cached): 0.69 s per request, 4,374,165 bytes of JSON, 935,669 gzipped. The smallest
  (TCRG1): 5 ms, 47,628 bytes. A repeat with the ETag costs the same server time but sends no body.

## Consequences

- New: `api/app/{settings,bundle,scorers,routes,schemas,problems,caching}.py`; `create_app`
  replaces the module-level app construction (`api.app.main:app` still exists for uvicorn).
- `pytest` gets `pythonpath = ["."]`, so `api/tests` can import the scorer's stand-in graphs
  from `tests/` when run on their own.
- No new Python dependency (pydantic-settings and fastapi were already in the `api` extra; the
  API also needs the `onnx` extra). New dev tooling under `web/` (Node).
- Not yet done (later Phase 4 tasks): `/v1/score`, `/v1/jobs` (SSE), `/metrics`, CORS, rate
  limiting, diskcache, the Docker image. The 0.69 s worst case is response building and
  serialisation of about 480,000 numbers. If it matters on the Space, cache the serialised
  body per (assay, model) next to the parsed file.

## Alternatives considered

- **Serve the bundle's fixed-point grids as they are:** smaller, but every API client would need
  the decoding rule. The static site already gets the compact form from the bundle.
- **ETag from the bundle revision:** cheaper (no hashing), but wrong after a code change to a
  response with the same bundle.
- **Hydra for the API's settings:** one config system, but environment variables are how a
  container is configured, and Hydra has no typed validation of a commit hash. The pins are
  checked against `conf/` by a test instead.
- **Parse all 217 protein files at startup:** no first-request latency, but +462 MB of
  Python objects (measured) on a small Space. The LRU holds the few that are being viewed.
