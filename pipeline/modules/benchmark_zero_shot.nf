/*
 * Benchmark one zero-shot score set against the assay labels: per-assay metrics and
 * ProteinGym-style aggregates with bootstrap CIs, written to params.results_dir and logged to
 * the MLflow store. maxForks 1: the store is one SQLite file, written by one task at a time.
 */
process BENCHMARK_ZERO_SHOT {
    tag "${model} ${method}"
    maxForks 1
    publishDir params.results_dir, mode: 'copy'

    input:
    path reference
    path variants
    tuple val(model), val(method), path(scores)

    output:
    path 'zero_shot_*.parquet', emit: results

    script:
    def fast = method == 'wt' ? '--fast' : ''
    """
    mutascope eval zero-shot --scores-dir ${scores} --variants-dir ${variants} \\
        --reference ${reference} --out-dir . ${fast} \\
        --config-dir "${params.conf_dir}" "data.data_dir=${params.data_dir}" \\
        "tracking.dir=${params.tracking_dir}" "model=${model}" ${params.hydra_overrides}
    """
}
