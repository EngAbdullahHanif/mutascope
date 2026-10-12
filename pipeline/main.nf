#!/usr/bin/env nextflow
/*
 * MutaScope pipeline (DSL2). Every process calls a `mutascope` CLI command; no inline Python.
 *
 *   DOWNLOAD -> [SUBSET] -> VALIDATE -> CLEAN ----------\
 *                                    -> STRUCT_FEATURES --> STRUCTURE_REPORT
 *                                    -> FOLDSEEK_3DI ----/
 *                                       CLEAN -> SCORE_ZERO_SHOT
 *
 * SUBSET runs only when params.assays names assays (the test profile). Every command gets
 * params.hydra_overrides appended, so one override reaches every step.
 */

include { DOWNLOAD         } from './modules/download'
include { SUBSET           } from './modules/subset'
include { VALIDATE         } from './modules/validate'
include { CLEAN            } from './modules/clean'
include { STRUCT_FEATURES  } from './modules/struct_features'
include { FOLDSEEK_3DI     } from './modules/foldseek_3di'
include { STRUCTURE_REPORT } from './modules/structure_report'
include { SCORE_ZERO_SHOT  } from './modules/score_zero_shot'

workflow {
    log.info "MutaScope pipeline | profile: ${workflow.profile}"
    log.info "data cache: ${params.data_dir} | processed: ${params.processed_dir} | reports: ${params.reports_dir}"
    log.info "zero-shot model: ${params.model}"
    log.info "Hydra overrides: ${params.hydra_overrides ?: '(none)'}"

    DOWNLOAD()
    def inputs = DOWNLOAD.out
    // A --assays A,B command-line value arrives as a string; the test profile sets a list.
    def assays = params.assays instanceof List ? params.assays : params.assays?.tokenize(',')
    if (assays) {
        log.info "Assays: ${assays.join(', ')}"
        SUBSET(inputs.reference, inputs.dms_dir, inputs.structures_dir, assays)
        inputs = SUBSET.out
    }

    VALIDATE(inputs.reference, inputs.dms_dir, inputs.structures_dir)
    CLEAN(inputs.reference, inputs.dms_dir, VALIDATE.out.log)
    STRUCT_FEATURES(inputs.reference, inputs.structures_dir, VALIDATE.out.log)
    FOLDSEEK_3DI(inputs.reference, inputs.structures_dir, VALIDATE.out.log)
    STRUCTURE_REPORT(
        inputs.reference,
        inputs.structures_dir,
        CLEAN.out.variants,
        STRUCT_FEATURES.out.features,
        FOLDSEEK_3DI.out.three_di,
    )
    SCORE_ZERO_SHOT(inputs.reference, CLEAN.out.variants)
}
