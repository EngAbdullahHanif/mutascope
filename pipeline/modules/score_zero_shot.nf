/*
 * Zero-shot scores of every assay's single mutants with params.model (masked marginal), at the
 * positions each assay measures. One task, one model load; maxForks 1 because the Mac has one
 * GPU. The log-probability cache is under params.data_dir, outside the work dir, so a task
 * that is killed and rerun (with or without -resume) scores only what is not cached yet.
 */
process SCORE_ZERO_SHOT {
    tag "${params.model}"
    maxForks 1
    publishDir "${params.processed_dir}/zero_shot", mode: 'copy', pattern: "${params.model}"
    publishDir params.reports_dir, mode: 'copy', pattern: 'report/*', saveAs: { file(it).name }

    input:
    path reference
    path variants

    output:
    path "${params.model}", emit: scores
    path 'report/*',        emit: report

    script:
    """
    mutascope score zero-shot --reference ${reference} --variants-dir ${variants} \\
        --out-dir "${params.model}" --report-dir report \\
        --config-dir "${params.conf_dir}" "data.data_dir=${params.data_dir}" \\
        "model=${params.model}" ${params.hydra_overrides}
    """
}
