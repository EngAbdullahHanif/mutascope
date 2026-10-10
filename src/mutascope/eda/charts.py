"""Altair charts for the EDA notebook, built from the frames in :mod:`mutascope.eda.summary`.

Each function takes an already-summarised frame and returns a chart; no data is computed
here beyond what a chart encoding needs. Single-series charts use one blue; the fit/unfit
split uses the first two slots of a colourblind-validated categorical palette (blue, orange).
Function types are shown as small multiples rather than five overlaid colours.
"""

import math

import altair as alt
import pandas as pd

from mutascope.eda.summary import MAX_MODEL_LENGTH

BLUE = "#2a78d6"
ORANGE = "#eb6834"
MUTED = "#898781"
# Axis labels for a log10-transformed field, shown in original units.
LOG10_LABELS = alt.Axis(labelExpr="format(pow(10, datum.value), ',.0f')")
WIDTH = 520


def assay_sizes(overview: pd.DataFrame) -> alt.Chart:
    """Histogram of single mutants per assay, on log-spaced bins (sizes span 33 to ~17k)."""
    return (
        alt.Chart(overview[["assay_id", "singles"]], width=WIDTH, height=220)
        .transform_calculate(log_singles="log(datum.singles) / LN10")
        .mark_bar(color=BLUE, cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
        .encode(
            alt.X(
                "log_singles:Q",
                bin=alt.Bin(step=0.2),
                title="Single mutants per assay (log scale)",
                axis=LOG10_LABELS,
            ),
            alt.Y("count():Q", title="Assays"),
            tooltip=[alt.Tooltip("count():Q", title="Assays")],
        )
        .properties(title="Assay sizes after cleaning")
    )


def function_types(summary: pd.DataFrame) -> alt.HConcatChart:
    """Assays and single mutants per function type, side by side (two scales, two charts)."""
    order = summary["function_type"].tolist()
    base = alt.Chart(summary, width=WIDTH // 2 - 20, height=180)
    y = alt.Y("function_type:N", sort=order, title=None)
    tooltip = ["function_type:N", "assays:Q", "proteins:Q", alt.Tooltip("singles:Q", format=",")]
    assays = (
        base.mark_bar(color=BLUE, cornerRadiusTopRight=3, cornerRadiusBottomRight=3)
        .encode(alt.X("assays:Q", title="Assays"), y, tooltip=tooltip)
        .properties(title="Assays per function type")
    )
    singles = (
        base.mark_bar(color=BLUE, cornerRadiusTopRight=3, cornerRadiusBottomRight=3)
        .encode(
            alt.X("singles:Q", title="Single mutants", axis=alt.Axis(format="~s")),
            y,
            tooltip=tooltip,
        )
        .properties(title="Single mutants per function type")
    )
    return alt.hconcat(assays, singles)


def standardized_scores(histogram: pd.DataFrame) -> alt.FacetChart:
    """Per-assay z-scored ``DMS_score`` densities, one small multiple per function type."""
    return (
        alt.Chart(histogram, width=WIDTH // 2 - 20, height=110)
        .mark_area(color=BLUE, opacity=0.85, interpolate="step-after")
        .encode(
            alt.X("bin_start:Q", title="DMS_score, z-scored within assay (clipped at ±5)"),
            alt.Y("density:Q", title="Density"),
            tooltip=[
                alt.Tooltip("bin_start:Q", format=".2f", title="from"),
                alt.Tooltip("bin_end:Q", format=".2f", title="to"),
                alt.Tooltip("density:Q", format=".3f"),
            ],
        )
        .facet(facet=alt.Facet("function_type:N", title=None), columns=2)
        .properties(title="Score distributions by function type")
    )


def assay_scores(histogram: pd.DataFrame, assay_id: str, cutoff: float | None) -> alt.LayerChart:
    """One assay's raw score histogram, stacked by binary label, with its cutoff marked."""
    bars = (
        alt.Chart(histogram, width=WIDTH, height=220)
        .mark_bar()
        .encode(
            alt.X("bin_start:Q", bin="binned", title="DMS_score (higher = fitter)"),
            alt.X2("bin_end:Q"),
            alt.Y("count:Q", stack=True, title="Single mutants"),
            alt.Color(
                "label:N",
                scale=alt.Scale(domain=["unfit (0)", "fit (1)"], range=[ORANGE, BLUE]),
                title="DMS_score_bin",
            ),
            tooltip=[
                alt.Tooltip("bin_start:Q", format=".3g", title="from"),
                alt.Tooltip("bin_end:Q", format=".3g", title="to"),
                "label:N",
                "count:Q",
            ],
        )
    )
    layers: list[alt.Chart] = [bars]
    if cutoff is not None:
        rule = pd.DataFrame({"cutoff": [cutoff]})
        layers.append(
            alt.Chart(rule)
            .mark_rule(color=MUTED, strokeDash=[4, 3], strokeWidth=1.5)
            .encode(x="cutoff:Q", tooltip=[alt.Tooltip("cutoff:Q", format=".4g")])
        )
    return alt.LayerChart(layer=layers).properties(
        title=f"{assay_id}: raw scores and binarization cutoff"
    )


def fraction_fit(overview: pd.DataFrame) -> alt.Chart:
    """Histogram over assays of the share of single mutants labelled fit (1)."""
    return (
        alt.Chart(overview[["assay_id", "frac_fit"]], width=WIDTH, height=180)
        .mark_bar(color=BLUE, cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
        .encode(
            alt.X("frac_fit:Q", bin=alt.Bin(step=0.05), title="Share of single mutants labelled 1"),
            alt.Y("count():Q", title="Assays"),
            tooltip=[alt.Tooltip("count():Q", title="Assays")],
        )
        .properties(title="Label balance per assay")
    )


def sequence_lengths(overview: pd.DataFrame) -> alt.LayerChart:
    """Histogram of target lengths (log-spaced bins) with ESM-2's 1,022-residue limit marked.

    Binned with an explicit ``transform_bin``: an encoding-level ``bin`` layered with the rule
    renders no bars in Vega-Lite.
    """
    bars = (
        alt.Chart(overview[["assay_id", "seq_len"]])
        .transform_calculate(log_len="log(datum.seq_len) / LN10")
        .transform_bin(["bin_start", "bin_end"], "log_len", bin=alt.Bin(step=0.1))
        .mark_bar(color=BLUE, cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
        .encode(
            alt.X(
                "bin_start:Q",
                bin="binned",
                title="Target sequence length (log scale)",
                axis=LOG10_LABELS,
            ),
            alt.X2("bin_end:Q"),
            alt.Y("count():Q", title="Assays"),
            tooltip=[alt.Tooltip("count():Q", title="Assays")],
        )
    )
    limit = math.log10(MAX_MODEL_LENGTH)
    rule = alt.Chart().mark_rule(color=MUTED, strokeDash=[4, 3]).encode(x=alt.datum(limit))
    text = (
        alt.Chart()
        .mark_text(align="left", dx=4, y=12, color=MUTED, fontSize=11)
        .encode(x=alt.datum(limit), text=alt.datum("1,022: longer needs windows"))
    )
    return alt.LayerChart(layer=[bars, rule, text]).properties(
        title="Target sequence lengths", width=WIDTH, height=220
    )


def coverage(overview: pd.DataFrame) -> alt.LayerChart:
    """Single mutants against target length per assay; the line is all 19 * L substitutions."""
    points = (
        alt.Chart(overview, width=WIDTH, height=280)
        .mark_circle(color=BLUE, size=60, opacity=0.75, stroke="white", strokeWidth=1)
        .encode(
            alt.X(
                "seq_len:Q",
                scale=alt.Scale(type="log", domain=[30, 4000], nice=False),
                title="Target sequence length",
            ),
            alt.Y(
                "singles:Q",
                scale=alt.Scale(type="log", domain=[30, 70000], nice=False),
                title="Single mutants",
            ),
            tooltip=[
                "assay_id:N",
                "function_type:N",
                "seq_len:Q",
                alt.Tooltip("singles:Q", format=","),
                alt.Tooltip("coverage:Q", format=".1%"),
            ],
        )
    )
    lo, hi = int(overview["seq_len"].min()), int(overview["seq_len"].max())
    full = pd.DataFrame({"seq_len": [lo, hi], "singles": [19 * lo, 19 * hi]})
    line = (
        alt.Chart(full)
        .mark_line(color=MUTED, strokeDash=[4, 3])
        .encode(x="seq_len:Q", y="singles:Q")
    )
    return alt.LayerChart(layer=[line, points]).properties(
        title="Mutational coverage (dashed line: every possible substitution measured)"
    )
