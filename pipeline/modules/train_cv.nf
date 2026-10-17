/*
 * Supervised cross-validation of one (model, feature set) on every assay the set covers, under
 * ProteinGym's random, modulo and contiguous folds (ADR 0019). Assays run in worker processes
 * (`--jobs`, default all cores but one) under train.max_variants_in_flight, which
 * params.max_variants_in_flight can set per model; results never depend on either (ADR 0022).
 * maxForks 1: each task already uses every core, and the MLflow store is one SQLite file.
 *
 * No publishDir here: the deployed run's results are published by CHECK_RELEASE, after the
 * release check, and the optional runs (TRAIN_CV_EXTRA) by the publishDir that
 * nextflow.config gives them. `after` is not read: it orders the task after another one, so
 * that no two training steps compete for the cores.
 */
process TRAIN_CV {
    tag "${model} ${feature_set}"
    maxForks 1

    input:
    path reference
    path variants
    path folds
    path store
    val after
    tuple val(model), val(feature_set)

    output:
    tuple val(model), val(feature_set), path('supervised/*'), emit: results

    script:
    def budget = params.max_variants_in_flight?.get(model)
    def budget_arg = budget ? "train.max_variants_in_flight=${budget}" : ''
    def jobs = params.train_jobs ? "--jobs ${params.train_jobs}" : ''
    """
    mutascope train cv --variants-dir ${variants} --folds-dir ${folds} \\
        --reference ${reference} --store-dir ${store} --out-dir supervised ${jobs} \\
        --config-dir "${params.conf_dir}" "data.data_dir=${params.data_dir}" \\
        "tracking.dir=${params.tracking_dir}" "train.model=${model}" \\
        "train.feature_set=${feature_set}" ${budget_arg} ${params.hydra_overrides}
    """
}
