/*
 * The deployed model (train.final) and the full landscapes of every eligible assay (ADR 0025).
 * `train final` runs its own checks and writes no bundle if one fails: the CV+ refit must
 * reproduce TRAIN_CALIBRATE's rows, the out-of-fold Spearman TRAIN_CV's results. Both come from
 * this run, so the checks show the run agrees with itself; CHECK_RELEASE compares it with the
 * release.
 *
 * `train final` writes final_checks / final_manifest next to the cv results it reads, so the
 * staged cv files are copied into a fresh supervised/ rather than written through their links.
 */
process TRAIN_FINAL {
    input:
    path reference
    path variants
    path folds
    path store
    path cv, stageAs: 'cv/*'
    path calibration

    output:
    path 'supervised_landscapes', emit: bundle
    path 'supervised/*',          emit: results

    script:
    def jobs = params.train_jobs ? "--jobs ${params.train_jobs}" : ''
    """
    mkdir supervised
    cp -L cv/* supervised/
    mutascope train final --variants-dir ${variants} --folds-dir ${folds} \\
        --reference ${reference} --store-dir ${store} --results-dir supervised \\
        --out-dir supervised_landscapes ${jobs} \\
        --config-dir "${params.conf_dir}" "data.data_dir=${params.data_dir}" \\
        "tracking.dir=${params.tracking_dir}" "train.uncertainty.calibration_dir=${calibration}" \\
        ${params.hydra_overrides}
    """
}
