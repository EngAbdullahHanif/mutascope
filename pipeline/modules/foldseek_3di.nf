/*
 * Foldseek 3Di state per structure residue. Needs the pinned Foldseek build: FOLDSEEK_BIN, or
 * `foldseek` on PATH (docs/OPERATIONS.md, "Installing Foldseek"); the command refuses any other version.
 */
process FOLDSEEK_3DI {
    publishDir params.processed_dir, mode: 'copy'

    input:
    path reference
    path structures_dir
    path validation

    output:
    path 'three_di', emit: three_di

    script:
    """
    mutascope data foldseek-3di --reference ${reference} \\
        --structures-dir ${structures_dir} --out-dir three_di \\
        --config-dir "${params.conf_dir}" ${params.hydra_overrides}
    """
}
