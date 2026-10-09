#!/usr/bin/env nextflow
/*
 * MutaScope pipeline (DSL2). Every process calls a `mutascope` CLI command; no inline Python.
 * Phase 1 adds modules/: DOWNLOAD -> VALIDATE -> CLEAN -> STRUCT_FEATURES -> FOLDSEEK_3DI.
 */

workflow {
    log.info "MutaScope pipeline | profile: ${workflow.profile} | outdir: ${params.outdir}"
    log.info "No modules yet - nothing to run."
}
