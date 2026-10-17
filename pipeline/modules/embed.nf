/*
 * Per-residue embeddings of one feature model (features.models): one unmasked pass per
 * landscape, the same benchmark and full-target regions as LANDSCAPES. The embedding cache is
 * under params.data_dir, outside the work dir, like the log-probability cache: a rerun computes
 * only what is missing. maxForks 1 because the Mac has one GPU.
 *
 * `ready` is not read: it holds the task until CHECK_LANDSCAPES has passed, so no two tasks
 * share the GPU.
 */
process EMBED {
    tag "${model}"
    maxForks 1
    publishDir params.reports_dir, mode: 'copy', saveAs: { file(it).name }

    input:
    path reference
    path three_di
    path structure_features
    val ready
    val model

    output:
    path 'report/*', emit: report

    script:
    def cache = params.embedding_cache_dir
        ? "\"features.embedding_cache_dir=${params.embedding_cache_dir}\"" : ''
    """
    mutascope features embed --reference ${reference} \\
        --three-di-dir ${three_di} --structure-features-dir ${structure_features} \\
        --report-dir report \\
        --config-dir "${params.conf_dir}" "data.data_dir=${params.data_dir}" ${cache} \\
        "tracking.dir=${params.tracking_dir}" "model=${model}" ${params.hydra_overrides}
    """
}
