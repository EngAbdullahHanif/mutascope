/*
 * Fail the run before any output is written if a wild-type residue disagrees with its target
 * sequence or an assay's structure does not match its target range. The log is the only output;
 * later steps take it as input so they cannot start before validation passes.
 */
process VALIDATE {
    publishDir params.reports_dir, mode: 'copy'

    input:
    path reference
    path dms_dir
    path structures_dir

    output:
    path 'validation.txt', emit: log

    script:
    """
    set -o pipefail
    {
        mutascope data check-variants --reference ${reference} --dms-dir ${dms_dir} \\
            --config-dir "${params.conf_dir}" ${params.hydra_overrides}
        mutascope data check-structures --reference ${reference} --structures-dir ${structures_dir} \\
            --config-dir "${params.conf_dir}" ${params.hydra_overrides}
    } | tee validation.txt
    """
}
