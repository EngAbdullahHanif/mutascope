/*
 * Cross-check variants, structure features and 3Di against each other; per-assay report.
 */
process STRUCTURE_REPORT {
    publishDir params.reports_dir, mode: 'copy', saveAs: { file(it).name }

    input:
    path reference
    path structures_dir
    path variants
    path features
    path three_di

    output:
    path 'report/*', emit: report

    script:
    """
    mutascope data structure-report --reference ${reference} \\
        --structures-dir ${structures_dir} --variants-dir ${variants} \\
        --features-dir ${features} --three-di-dir ${three_di} --report-dir report \\
        --config-dir "${params.conf_dir}" ${params.hydra_overrides}
    """
}
