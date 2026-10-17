#!/usr/bin/env nextflow
/*
 * MutaScope pipeline (DSL2). Every process calls a `mutascope` CLI command; no inline Python.
 *
 *   DOWNLOAD -> [SUBSET] -> VALIDATE -> CLEAN ----------\
 *                                    -> STRUCT_FEATURES --> STRUCTURE_REPORT
 *                                    -> FOLDSEEK_3DI ----/
 *            CLEAN, STRUCT_FEATURES, FOLDSEEK_3DI -> SCORE_ZERO_SHOT -> BENCHMARK_ZERO_SHOT
 *                                                    SCORE_ZERO_SHOT -> LANDSCAPES -> CHECK_LANDSCAPES
 *   Phase 3 (params.supervised; ADR 0027):
 *            CLEAN -> FOLDS
 *            CHECK_LANDSCAPES -> EMBED (per feature model) -> FEATURES_BUILD
 *            FOLDS, FEATURES_BUILD -> TRAIN_CV (deployed run) -> TRAIN_CALIBRATE -> TRAIN_FINAL
 *                                  -> CHECK_RELEASE -> [TRAIN_CV_EXTRA (params.extra_models)]
 *
 * SCORE_ZERO_SHOT and BENCHMARK_ZERO_SHOT run once per (model, method) in params.models x
 * params.methods, except that the models in params.masked_only_models run the masked marginal
 * only (SaProt: the official scoring defines no wild-type marginal, and ProteinGym has no
 * scores to check one against). LANDSCAPES runs once per model, after every scoring task (they
 * fill the same cache entries and share one GPU), unless params.landscapes is false.
 * SUBSET runs only when params.assays names assays (the test profile). Every command gets
 * params.hydra_overrides appended, so one override reaches every step.
 *
 * Phase 3 rebuilds the deployed model from the processed data: the feature store from the
 * complete landscapes, then the deployed run (params.final_run, ridge on seq_struct) through
 * `train cv`, `train calibrate` and `train final`, whose own checks block a bundle that does not
 * reproduce this run's calibration and CV results. CHECK_RELEASE then compares the outputs with
 * the released ones and publishes them only if they are identical (params.release_check). The
 * training steps run one after another: each uses every core. LightGBM and MLP runs
 * (params.extra_runs) are optional, behind params.extra_models, because of their compute cost.
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
include { FOLDS               } from './modules/folds'
include { EMBED               } from './modules/embed'
include { FEATURES_BUILD      } from './modules/features_build'
include { TRAIN_CV            } from './modules/train_cv'
include { TRAIN_CV as TRAIN_CV_EXTRA } from './modules/train_cv'
include { TRAIN_CALIBRATE     } from './modules/train_calibrate'
include { TRAIN_FINAL         } from './modules/train_final'
include { CHECK_RELEASE       } from './modules/check_release'

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
    def feature_models = as_list(params.feature_models)
    def final_run = as_list(params.final_run)
    if (params.supervised) {
        if (!params.landscapes) {
            error "params.supervised needs params.landscapes: the feature store reads full landscapes"
        }
        def unscored = feature_models - models
        if (unscored) {
            error "params.feature_models ${unscored} are not in params.models, so not scored"
        }
        def extra = params.extra_models ? params.extra_runs.collect { it.join(' ') } : []
        log.info "supervised: ${final_run.join(' ')} (release check: ${params.release_check})" +
            (extra ? " | extra runs: ${extra.join(', ')}" : ' | extra runs off')
    }

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
    if (params.supervised) {
        // The release's folds directory, also under SUBSET: FOLDS reads only processed assays.
        FOLDS(CLEAN.out.variants, DOWNLOAD.out.folds_dir)
        EMBED(
            inputs.reference,
            FOLDSEEK_3DI.out.three_di,
            STRUCT_FEATURES.out.features,
            CHECK_LANDSCAPES.out.report,
            channel.fromList(feature_models),
        )
        FEATURES_BUILD(
            inputs.reference,
            CLEAN.out.variants,
            FOLDSEEK_3DI.out.three_di,
            STRUCT_FEATURES.out.features,
            EMBED.out.report.collect(),
        )
        def store = FEATURES_BUILD.out.store
        TRAIN_CV(
            inputs.reference,
            CLEAN.out.variants,
            FOLDS.out.folds,
            store,
            store,
            channel.of(final_run),
        )
        def cv = TRAIN_CV.out.results.map { _model, _set, files -> files }
        TRAIN_CALIBRATE(CLEAN.out.variants, FOLDS.out.folds, store, cv, final_run[0])
        TRAIN_FINAL(
            inputs.reference,
            CLEAN.out.variants,
            FOLDS.out.folds,
            store,
            cv,
            TRAIN_CALIBRATE.out.calibration,
        )
        CHECK_RELEASE(
            TRAIN_FINAL.out.bundle,
            TRAIN_FINAL.out.results,
            TRAIN_CALIBRATE.out.calibration,
        )
        if (params.extra_models) {
            // After the release is checked; maxForks 1 runs them one at a time.
            TRAIN_CV_EXTRA(
                inputs.reference,
                CLEAN.out.variants,
                FOLDS.out.folds,
                store,
                CHECK_RELEASE.out[0].collect(),
                channel.fromList(params.extra_runs),
            )
        }
    }
}
