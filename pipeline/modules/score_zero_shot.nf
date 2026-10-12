/*
 * Zero-shot scores of every assay's single mutants with one model and one scoring method
 * (masked or wt = wild-type marginal), at the positions each assay measures. One task per
 * (model, method), one model load each; maxForks 1 because the Mac has one GPU. The
 * log-probability cache is under params.data_dir, outside the work dir, so a task that is
 * killed and rerun (with or without -resume) scores only what is not cached yet.
 *
 * The command names the score set itself (zero_shot/<label>/, label = model, or model_wt), so
 * the naming rule lives in one place, mutascope.eval.zero_shot.run_label. The run is logged to
 * the MLflow store in params.tracking_dir.
 *
 * The structure tables are passed to every model; only a structure-aware one (SaProt) reads
 * them, and it scores the assays with a structure on the residues the structure models.
 */
process SCORE_ZERO_SHOT {
    tag "${model} ${method}"
    maxForks 1
    publishDir params.processed_dir, mode: 'copy', pattern: 'zero_shot/*'
    publishDir params.reports_dir, mode: 'copy', pattern: 'report/*', saveAs: { file(it).name }

    input:
    path reference
    path variants
    path three_di
    path structure_features
    tuple val(model), val(method)

    output:
    tuple val(model), val(method), path('zero_shot/*'), emit: scores
    path 'report/*',                                    emit: report

    script:
    def fast = method == 'wt' ? '--fast' : ''
    """
    mutascope score zero-shot --reference ${reference} --variants-dir ${variants} \\
        --three-di-dir ${three_di} --structure-features-dir ${structure_features} \\
        --out-dir zero_shot --report-dir report ${fast} \\
        --config-dir "${params.conf_dir}" "data.data_dir=${params.data_dir}" \\
        "tracking.dir=${params.tracking_dir}" "model=${model}" ${params.hydra_overrides}
    """
}
