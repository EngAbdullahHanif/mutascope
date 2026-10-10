/*
 * Processed single-mutant table (Parquet partitioned by assay_id) plus the cleaning report.
 */
process CLEAN {
    publishDir params.processed_dir, mode: 'copy', pattern: 'variants'
    publishDir params.reports_dir,   mode: 'copy', pattern: 'report/*', saveAs: { file(it).name }

    input:
    path reference
    path dms_dir
    path validation

    output:
    path 'variants', emit: variants
    path 'report/*', emit: report

    script:
    """
    mutascope data clean --reference ${reference} --dms-dir ${dms_dir} \\
        --out-dir variants --report-dir report \\
        --config-dir "${params.conf_dir}" ${params.hydra_overrides}
    """
}
