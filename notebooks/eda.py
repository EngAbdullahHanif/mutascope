import marimo

__generated_with = "0.25.1"
app = marimo.App(width="medium", app_title="MutaScope: ProteinGym EDA")


@app.cell
def _():
    import marimo as mo

    from mutascope.eda import charts
    from mutascope.eda.summary import (
        assay_overview,
        assay_score_histogram,
        connect_processed,
        function_type_summary,
        standardized_score_histogram,
    )

    return (
        assay_overview,
        assay_score_histogram,
        charts,
        connect_processed,
        function_type_summary,
        mo,
        standardized_score_histogram,
    )


@app.cell
def _(mo):
    mo.md("""
    # ProteinGym v1.3 single mutants: exploratory overview

    What `make pipeline` produced: the cleaned single-mutant table plus per-residue
    structure features. All numbers and charts come from `mutascope.eda`; this notebook
    only displays them. Paths come from the Hydra config (`conf/`), resolved against the
    repo root. Scores are functional effects measured in DMS assays, not clinical labels.
    """)
    return


@app.cell
def _(assay_overview, connect_processed, function_type_summary, mo):
    root = mo.notebook_dir().parent
    con = connect_processed(root / "conf", root)
    overview = assay_overview(con)
    by_type = function_type_summary(overview)
    return by_type, con, overview


@app.cell
def _(mo, overview):
    mo.hstack(
        [
            mo.stat(f"{len(overview)}", label="Assays"),
            mo.stat(f"{overview['uniprot_id'].nunique()}", label="Proteins (UniProt IDs)"),
            mo.stat(f"{int(overview['singles'].sum()):,}", label="Single mutants"),
            mo.stat(
                f"{int(overview['multi_mutants'].sum()):,}",
                label="Multi-mutant rows removed",
            ),
            mo.stat(
                f"{int((overview['positions_with_structure'] > 0).sum())}",
                label="Assays with a structure",
            ),
        ],
        justify="start",
        gap=2,
    )
    return


@app.cell
def _(charts, mo, overview):
    mo.vstack(
        [
            mo.md("## Assay sizes"),
            mo.md(
                "Assays range from a few dozen to ~17k single mutants, so per-assay metrics "
                "on the smallest are noisy. ProteinGym's aggregation (per protein, then per "
                "function type) keeps the large assays from dominating."
            ),
            mo.ui.altair_chart(charts.assay_sizes(overview), chart_selection=False),
        ]
    )
    return


@app.cell
def _(by_type, charts, mo):
    mo.vstack(
        [
            mo.md("## Function types"),
            mo.md(
                "`coarse_selection_type` is the second aggregation level of the headline "
                "metric. Organismal fitness has the most assays and mutants. 64 of the 66 "
                "stability assays come from one study (Tsuboyama et al., 2023)."
            ),
            mo.ui.altair_chart(charts.function_types(by_type), chart_selection=False),
            mo.ui.table(by_type, selection=None),
        ]
    )
    return


@app.cell
def _(charts, con, mo, standardized_score_histogram):
    mo.vstack(
        [
            mo.md("## Score distributions"),
            mo.md(
                "Raw `DMS_score` units differ per assay, so scores are z-scored within each "
                "assay before pooling. Activity, binding, expression and stability are "
                "left-skewed: a peak of near-neutral variants and a long tail of damaging "
                "ones. Organismal fitness is broader and closer to symmetric."
            ),
            mo.ui.altair_chart(
                charts.standardized_scores(standardized_score_histogram(con)),
                chart_selection=False,
            ),
        ]
    )
    return


@app.cell
def _(mo, overview):
    assay_picker = mo.ui.dropdown(
        options=overview["assay_id"].tolist(),
        value=overview.loc[overview["singles"].idxmax(), "assay_id"],
        label="Assay",
        searchable=True,
    )
    return (assay_picker,)


@app.cell
def _(assay_picker, assay_score_histogram, charts, con, mo, overview):
    _row = overview.set_index("assay_id").loc[assay_picker.value]
    mo.vstack(
        [
            mo.md("### One assay, raw scores and binary label"),
            assay_picker,
            mo.md(
                f"{_row['function_type']} · {int(_row['singles']):,} single mutants · "
                f"cutoff by **{_row['bin_method']}** = {_row['bin_cutoff']:.4g} · "
                f"{_row['frac_fit']:.0%} labelled fit"
            ),
            mo.ui.altair_chart(
                charts.assay_scores(
                    assay_score_histogram(con, assay_picker.value),
                    assay_picker.value,
                    float(_row["bin_cutoff"]),
                ),
                chart_selection=False,
            ),
        ]
    )
    return


@app.cell
def _(charts, mo, overview):
    mo.vstack(
        [
            mo.md(
                "Label balance. ProteinGym sets the cutoff per assay, by median or manually. "
                "Where the median was taken over multi-mutants as well, single mutants "
                "(usually fitter) end up mostly labelled 1; in one assay, all of them."
            ),
            mo.ui.altair_chart(charts.fraction_fit(overview), chart_selection=False),
        ]
    )
    return


@app.cell
def _(charts, mo, overview):
    mo.vstack(
        [
            mo.md("## Sequence lengths and coverage"),
            mo.md(
                f"{int(overview['needs_windows'].sum())} targets are longer than 1,022 "
                "residues and will need sliding windows for ESM-2. The peak at 40-80 residues "
                "is the Tsuboyama stability domains. Short targets are usually saturated; "
                "long ones are sparsely sampled."
            ),
            mo.ui.altair_chart(charts.sequence_lengths(overview), chart_selection=False),
            mo.ui.altair_chart(charts.coverage(overview), chart_selection=False),
        ]
    )
    return


@app.cell
def _(mo, overview):
    mo.vstack([mo.md("## Per-assay table"), mo.ui.table(overview, selection=None)])
    return


if __name__ == "__main__":
    app.run()
