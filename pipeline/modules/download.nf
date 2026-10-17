/*
 * Fetch and verify the pinned release into params.data_dir (a cache shared across runs, holding
 * the committed manifest), then link it into the task dir in the staged layout.
 */
process DOWNLOAD {
    tag "${params.data_dir}"

    output:
    path 'inputs/DMS_substitutions.csv', emit: reference
    path 'inputs/dms',                   emit: dms_dir
    path 'inputs/structures',            emit: structures_dir
    path 'inputs/folds',                 emit: folds_dir

    script:
    """
    mutascope data download --link-dir inputs \\
        --config-dir "${params.conf_dir}" "data.data_dir=${params.data_dir}" ${params.hydra_overrides}
    """
}
