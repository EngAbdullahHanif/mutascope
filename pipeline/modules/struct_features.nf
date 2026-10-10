/*
 * Per-residue pLDDT, SASA and contact counts for every assay with a matching structure.
 */
process STRUCT_FEATURES {
    publishDir params.processed_dir, mode: 'copy'

    input:
    path reference
    path structures_dir
    path validation

    output:
    path 'structure_features', emit: features

    script:
    """
    mutascope data structure-features --reference ${reference} \\
        --structures-dir ${structures_dir} --out-dir structure_features \\
        --config-dir "${params.conf_dir}" ${params.hydra_overrides}
    """
}
