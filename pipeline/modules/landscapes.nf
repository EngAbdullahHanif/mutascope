/*
 * Full landscapes of one model: masked marginals at every residue of every assay, and for an
 * assay whose benchmark region is part of its target, a second landscape of the full target.
 * The landscapes are the entries of the log-probability cache under params.data_dir that
 * SCORE_ZERO_SHOT started; this task scores only the residues they lack, so it never changes
 * a benchmark score. The cache is written every scoring.landscapes.checkpoint_tokens of model
 * work: a task that is killed and rerun (with or without -resume) continues where it stopped.
 *
 * One task per model, masked marginal only; maxForks 1 because the Mac has one GPU. The
 * output is the run report; the run is logged to the MLflow store in params.tracking_dir.
 */
process LANDSCAPES {
    tag "${model}"
    maxForks 1
    publishDir params.reports_dir, mode: 'copy', saveAs: { file(it).name }

    input:
    path reference
    path three_di
    path structure_features
    val model

    output:
    path 'report/*', emit: report

    script:
    def cache = params.cache_dir ? "\"scoring.cache_dir=${params.cache_dir}\"" : ''
    """
    mutascope score landscapes --reference ${reference} \\
        --three-di-dir ${three_di} --structure-features-dir ${structure_features} \\
        --report-dir report \\
        --config-dir "${params.conf_dir}" "data.data_dir=${params.data_dir}" ${cache} \\
        "tracking.dir=${params.tracking_dir}" "model=${model}" ${params.hydra_overrides}
    """
}
