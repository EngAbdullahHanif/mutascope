/*
 * ProteinGym's CV folds joined to the processed variants (random, modulo, contiguous; one fold
 * per variant and scheme, or the task fails). folds_dir is the full release's folds directory
 * even in the test profile: only the processed assays' files are read.
 */
process FOLDS {
    publishDir params.processed_dir, mode: 'copy'

    input:
    path variants
    path folds_dir, stageAs: 'cv_folds'

    output:
    path 'folds', emit: folds

    script:
    """
    mutascope data folds --variants-dir ${variants} --folds-dir cv_folds --out-dir folds \\
        --config-dir "${params.conf_dir}" ${params.hydra_overrides}
    """
}
