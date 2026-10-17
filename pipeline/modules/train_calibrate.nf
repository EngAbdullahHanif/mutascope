/*
 * CV+ calibration rows of the deployed model (train.uncertainty: ridge on seq_struct), fit on
 * all of each assay's variants under each scheme's folds (ADR 0024, 0025). Written to the work
 * dir, never resumed from earlier rows, so TRAIN_FINAL's refit check tests this run. `after` is
 * not read: it orders the task after TRAIN_CV (see there).
 */
process TRAIN_CALIBRATE {
    tag "${model}"

    input:
    path variants
    path folds
    path store
    val after
    val model

    output:
    path 'calibration', emit: calibration

    script:
    def budget = params.max_variants_in_flight?.get(model)
    def budget_arg = budget ? "train.max_variants_in_flight=${budget}" : ''
    def jobs = params.train_jobs ? "--jobs ${params.train_jobs}" : ''
    """
    mutascope train calibrate --variants-dir ${variants} --folds-dir ${folds} \\
        --store-dir ${store} --out-dir calibration ${jobs} \\
        --config-dir "${params.conf_dir}" "data.data_dir=${params.data_dir}" \\
        "tracking.dir=${params.tracking_dir}" ${budget_arg} ${params.hydra_overrides}
    """
}
