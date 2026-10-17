/*
 * The feature store: every 19 x L substitution of every target with its features (ADR 0020).
 * No model is loaded and no label is read: scores and log-probabilities come from the
 * log-probability cache (complete, as CHECK_LANDSCAPES showed), embeddings from the embedding
 * cache (EMBED), structure features from the processed tables.
 *
 * `embeddings` is not read: it makes the task wait for every EMBED task.
 */
process FEATURES_BUILD {
    publishDir params.processed_dir, mode: 'copy', pattern: 'features'
    publishDir params.reports_dir, mode: 'copy', pattern: 'report/*', saveAs: { file(it).name }

    input:
    path reference
    path variants
    path three_di
    path structure_features
    path embeddings, stageAs: 'embedding_runs/*'

    output:
    path 'features', emit: store
    path 'report/*', emit: report

    script:
    def cache = params.cache_dir ? "\"scoring.cache_dir=${params.cache_dir}\"" : ''
    def embedding_cache = params.embedding_cache_dir
        ? "\"features.embedding_cache_dir=${params.embedding_cache_dir}\"" : ''
    """
    mutascope features build --reference ${reference} --variants-dir ${variants} \\
        --three-di-dir ${three_di} --structure-features-dir ${structure_features} \\
        --out-dir features --report report/feature_store.parquet \\
        --config-dir "${params.conf_dir}" "data.data_dir=${params.data_dir}" ${cache} \\
        ${embedding_cache} ${params.hydra_overrides}
    """
}
