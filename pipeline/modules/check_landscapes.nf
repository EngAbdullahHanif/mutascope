/*
 * Final check on the landscapes: every assay has a complete (L, 20) landscape in the cache for
 * every model in params.models (a structure-aware model: every assay with a structure), and a
 * complete full-target landscape where the benchmark region is partial. No model is loaded.
 * The task fails if anything is missing; the coverage table is published either way it ends.
 *
 * `reports` is not read: it makes the task wait for every LANDSCAPES task.
 */
process CHECK_LANDSCAPES {
    publishDir params.reports_dir, mode: 'copy', saveAs: { file(it).name }

    input:
    path reference
    path three_di
    path structure_features
    path reports, stageAs: 'runs/*'
    val models

    output:
    path 'report/*', emit: report

    script:
    def cache = params.cache_dir ? "\"scoring.cache_dir=${params.cache_dir}\"" : ''
    """
    mutascope score check-landscapes --reference ${reference} \\
        --three-di-dir ${three_di} --structure-features-dir ${structure_features} \\
        --out report/landscape_coverage.parquet \\
        --config-dir "${params.conf_dir}" "data.data_dir=${params.data_dir}" ${cache} \\
        ${params.hydra_overrides} "scoring.landscapes.models=[${models.join(',')}]"
    """
}
