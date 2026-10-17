/*
 * Release check, then publication of the deployed model's outputs. With params.release_check,
 * `train check-release` compares this run's cv results, calibration rows, final checks and
 * manifest and landscape bundle with the ones already in params.results_dir/supervised and
 * params.processed_dir, which are what this task would replace: the same files and every value
 * equal to the bit, except the provenance stamps in train.release.ignore. If anything differs
 * the task fails and nothing is published. Without it (a deliberate model change, or the test
 * profile, which has no release to compare with) the outputs are published unchecked.
 *
 * Published per model label (calibration/<label>, supervised_landscapes/<label>) and per file
 * into results/supervised, so a copy never replaces a directory holding other runs' files.
 */
process CHECK_RELEASE {
    publishDir "${params.processed_dir}/supervised_landscapes", mode: 'copy',
        pattern: 'release/supervised_landscapes/*', saveAs: { file(it).name }
    publishDir "${params.processed_dir}/calibration", mode: 'copy',
        pattern: 'release/calibration/*', saveAs: { file(it).name }
    publishDir "${params.results_dir}/supervised", mode: 'copy',
        pattern: 'release/supervised/*', saveAs: { file(it).name }

    input:
    path bundle, stageAs: 'supervised_landscapes'
    path results, stageAs: 'supervised/*'
    path calibration, stageAs: 'calibration'

    output:
    path 'release/supervised_landscapes/*'
    path 'release/calibration/*'
    path 'release/supervised/*'

    script:
    def check = params.release_check ? """
    mutascope train check-release --bundle-dir supervised_landscapes \\
        --results-dir supervised --calibration-dir calibration \\
        --released-bundle-dir "${params.processed_dir}/supervised_landscapes" \\
        --released-results-dir "${params.results_dir}/supervised" \\
        --released-calibration-dir "${params.processed_dir}/calibration" \\
        --config-dir "${params.conf_dir}" "data.data_dir=${params.data_dir}" \\
        ${params.hydra_overrides}
    """ : 'echo "release check off (params.release_check): publishing unchecked"'
    """
    ${check}
    mkdir release
    cp -RL supervised_landscapes calibration supervised release/
    """
}
