/*
 * Copy the assays named in params.assays into the staged layout (test profile only).
 */
process SUBSET {
    tag "${assays.size()} assays"

    input:
    path reference
    path dms_dir
    path structures_dir
    val assays

    output:
    path 'subset/DMS_substitutions.csv', emit: reference
    path 'subset/dms',                   emit: dms_dir
    path 'subset/structures',            emit: structures_dir

    script:
    def assay_args = assays.collect { "--assay '${it}'" }.join(' ')
    """
    mutascope data subset --reference ${reference} --dms-dir ${dms_dir} \\
        --structures-dir ${structures_dir} ${assay_args} --out-dir subset \\
        --config-dir "${params.conf_dir}" ${params.hydra_overrides}
    """
}
