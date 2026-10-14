#!/usr/bin/env nextflow
/*
 * MutaScope pipeline (DSL2). Every process calls a `mutascope` CLI command; no inline Python.
 *
 *   DOWNLOAD -> [SUBSET] -> VALIDATE -> CLEAN ----------\
 *                                    -> STRUCT_FEATURES --> STRUCTURE_REPORT
 *                                    -> FOLDSEEK_3DI ----/
 *            CLEAN, STRUCT_FEATURES, FOLDSEEK_3DI -> SCORE_ZERO_SHOT -> BENCHMARK_ZERO_SHOT
 *                                                    SCORE_ZERO_SHOT -> LANDSCAPES -> CHECK_LANDSCAPES
 *
 * SCORE_ZERO_SHOT and BENCHMARK_ZERO_SHOT run once per (model, method) in params.models x
 * params.methods, except that the models in params.masked_only_models run the masked marginal
 * only (SaProt: the official scoring defines no wild-type marginal, and ProteinGym has no
 * scores to check one against). LANDSCAPES runs once per model, after every scoring task (they
 * fill the same cache entries and share one GPU), unless params.landscapes is false.
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
include { SCORE_ZERO_SHOT     } from './modules/score_zero_shot'
include { BENCHMARK_ZERO_SHOT } from './modules/benchmark_zero_shot'
include { LANDSCAPES          } from './modules/landscapes'
include { CHECK_LANDSCAPES    } from './modules/check_landscapes'

// A comma-separated command-line value arrives as a string; profiles set lists.
def as_list(value) {
    return (value instanceof List) ? value : value?.tokenize(',')
}

workflow {
    log.info "MutaScope pipeline | profile: ${workflow.profile}"
    log.info "data cache: ${params.data_dir} | processed: ${params.processed_dir} | reports: ${params.reports_dir}"
    def models = as_list(params.models)
    def methods = as_list(params.methods)
    def unknown = methods - ['masked', 'wt']
    if (unknown) {
        error "params.methods: unknown ${unknown}; use masked and/or wt"
    }
    log.info "zero-shot models: ${models.join(', ')} | methods: ${methods.join(', ')}"
    log.info "results: ${params.results_dir} | MLflow store: ${params.tracking_dir}"
    log.info "Hydra overrides: ${params.hydra_overrides ?: '(none)'}"

    DOWNLOAD()
    def inputs = DOWNLOAD.out
    def assays = as_list(params.assays)
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
    def masked_only = as_list(params.masked_only_models) ?: []
    def runs = channel.fromList(models).combine(channel.fromList(methods))
        .filter { model, method -> method == 'masked' || !(model in masked_only) }
    SCORE_ZERO_SHOT(
        inputs.reference,
        CLEAN.out.variants,
        FOLDSEEK_3DI.out.three_di,
        STRUCT_FEATURES.out.features,
        runs,
    )
    BENCHMARK_ZERO_SHOT(inputs.reference, CLEAN.out.variants, SCORE_ZERO_SHOT.out.scores)
    if (params.landscapes) {
        // collect() emits once, when the last scoring task has finished; flatten() then emits
        // each model again, one LANDSCAPES task each.
        def scored = SCORE_ZERO_SHOT.out.scores.map { model, _method, _scores -> model }
            .unique().collect().flatten()
        LANDSCAPES(
            inputs.reference,
            FOLDSEEK_3DI.out.three_di,
            STRUCT_FEATURES.out.features,
            scored,
        )
        CHECK_LANDSCAPES(
            inputs.reference,
            FOLDSEEK_3DI.out.three_di,
            STRUCT_FEATURES.out.features,
            LANDSCAPES.out.report.collect(),
            models,
        )
    }
}
