"""Benchmark figures, drawn only from results files (ADR 0016).

Every function takes frames that were read from ``results/*.parquet`` and returns a matplotlib
figure. Nothing is computed here: a value, an interval or a difference that is not in the
frames is an error (:class:`FigureDataError`), never something worked out while drawing.

Two kinds of interval appear, and the figures keep them apart:

- A score set's own bootstrap CI (:class:`~mutascope.eval.schemas.SummarySchema`) says how well
  it does on assays like these. It is drawn as a grey whisker.
- A paired-bootstrap CI of a difference (:class:`~mutascope.eval.schemas.ComparisonSchema`) is
  the only thing that says whether one score set is better than another (ADR 0014). It is
  drawn as a coloured interval around the difference, or written next to the step it describes.

Colours are the first three slots of the colourblind-validated categorical palette already used
by :mod:`mutascope.eda.charts`. Text is never coloured; a filled or hollow marker, not a hue,
says whether an interval excludes zero.

matplotlib is the optional ``figures`` extra and is imported only by this module.
"""
# matplotlib types the styling arguments of every drawing call as untyped **kwargs.
# pyright: reportUnknownMemberType=false

import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib
import pandas as pd
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.ticker import MaxNLocator

from mutascope.data.schemas import FUNCTION_TYPES

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e4e3de"
SERIES = ("#2a78d6", "#eb6834", "#1baf7a")
"""Categorical slots 1-3 (blue, orange, aqua); validated together for colour-vision deficiency."""

FUNCTION_NAMES = {"OrganismalFitness": "Organismal fitness"}
METRIC_NAMES = {
    "spearman": "Spearman",
    "auc": "AUC",
    "mcc": "MCC",
    "ndcg": "NDCG@10%",
    "top_recall": "top-10% recall",
}

_RC: dict[Any, Any] = {
    "font.family": "DejaVu Sans",  # ships with matplotlib: the same glyphs on every machine
    "font.size": 9,
    "text.color": TEXT,
    "axes.labelcolor": TEXT_SECONDARY,
    "axes.edgecolor": GRID,
    "axes.facecolor": SURFACE,
    "axes.linewidth": 0.8,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "xtick.color": TEXT_SECONDARY,
    "ytick.color": TEXT_SECONDARY,
    "xtick.major.size": 0,
    "ytick.major.size": 0,
    "figure.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "svg.hashsalt": "mutascope",  # stable element ids: an unchanged figure gives the same file
}


class FigureDataError(ValueError):
    """A figure needs a number that is not in the results files."""


@dataclass(frozen=True)
class FigureContext:
    """What every figure states about its numbers."""

    metric: str
    names: Mapping[str, str]
    """Score-set label -> display name."""
    dataset: str
    """e.g. ``ProteinGym v1.3``."""
    confidence: float
    """Level of the intervals in the results files (``eval.bootstrap.confidence``)."""

    def name(self, label: str) -> str:
        if label not in self.names:
            raise FigureDataError(f"no display name for {label!r} in eval.figures.names")
        return self.names[label]

    @property
    def metric_name(self) -> str:
        return METRIC_NAMES[self.metric]

    @property
    def level(self) -> str:
        return f"{self.confidence:.0%}"


def model_size_figure(
    summaries: pd.DataFrame,
    comparisons: pd.DataFrame,
    sizes: Mapping[str, float],
    context: FigureContext,
) -> Figure:
    """The metric against model size, with the paired difference of every size step.

    Args:
        summaries: Summary rows of the score sets in ``sizes`` (their own summary files).
        comparisons: Paired comparisons holding ``[larger, smaller]`` for every step.
        sizes: Score-set label -> parameters in millions, smallest first.
        context: Metric, names and dataset.
    """
    labels = list(sizes)
    rows = [_summary_row(summaries, label, context.metric, None) for label in labels]
    n_assays = _same_count([int(r["n_assays"]) for r in rows], "the model-size score sets")
    with matplotlib.rc_context(_RC):
        fig = Figure(figsize=(7.2, 4.6))
        ax = fig.add_axes((0.09, 0.2, 0.87, 0.6))
        x = [float(sizes[label]) for label in labels]
        y = [float(r["value"]) for r in rows]
        for xi, row in zip(x, rows, strict=True):
            ax.plot(
                [xi, xi],
                [float(row["ci_low"]), float(row["ci_high"])],
                color=TEXT_SECONDARY,
                linewidth=1,
                zorder=2,
            )
            ax.annotate(
                f"{float(row['value']):.3f}",
                (xi, float(row["ci_high"])),
                xytext=(0, 5),
                textcoords="offset points",
                ha="center",
                fontsize=9,
            )
        ax.plot(x, y, color=SERIES[0], linewidth=2, zorder=3)
        ax.plot(
            x,
            y,
            linestyle="none",
            marker="o",
            markersize=9,
            markerfacecolor=SERIES[0],
            markeredgecolor=SURFACE,
            markeredgewidth=2,
            zorder=4,
        )
        low = min(float(r["ci_low"]) for r in rows)
        for (small, x0), (large, x1) in zip(
            zip(labels, x, strict=True), zip(labels[1:], x[1:], strict=False), strict=False
        ):
            step = _comparison_row(comparisons, large, small, context.metric, None)
            _same_count([n_assays, int(step["n_assays"])], f"{large} - {small} and the summaries")
            ax.annotate(
                f"{_signed(step['diff'])}\n[{_signed(step['ci_low'])}, {_signed(step['ci_high'])}]",
                ((x0 * x1) ** 0.5, low),
                xytext=(0, -4),
                textcoords="offset points",
                ha="center",
                va="top",
                fontsize=8,
                color=TEXT_SECONDARY,
            )
        ax.set_xscale("log")
        ax.set_xlim(x[0] / 1.5, x[-1] * 1.5)
        ax.set_xticks(
            x, [f"{context.name(label)}\n{sizes[label]:g}M parameters" for label in labels]
        )
        ax.minorticks_off()
        span = max(float(r["ci_high"]) for r in rows) - low
        ax.set_ylim(low - 0.32 * span, low + 1.22 * span)
        ax.set_ylabel(f"{context.metric_name} (ProteinGym aggregate)")
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        _titles(
            fig,
            f"Zero-shot {context.metric_name} by ESM-2 model size",
            f"{context.dataset}, {n_assays} assays, single mutants, masked marginal",
            f"Whiskers: each model's own {context.level} bootstrap CI over assays. Below each "
            f"step: difference between\nthe two sizes with its {context.level} paired-bootstrap "
            "CI, the only interval here that compares two models.",
        )
    return fig


def function_type_figure(
    shared: pd.DataFrame, labels: Sequence[str], context: FigureContext
) -> Figure:
    """Bars of the metric overall and per function type, on the assays all score sets have.

    Args:
        shared: The shared-assay summary (``mutascope eval shared``), holding ``labels``.
        labels: Score sets to draw, in legend order; at most three.
        context: Metric, names and dataset.
    """
    if len(labels) > len(SERIES):
        raise FigureDataError(f"at most {len(SERIES)} score sets per bar figure, got {len(labels)}")
    groups: list[str | None] = [None, *FUNCTION_TYPES]
    counts = [
        _same_count(
            [int(_summary_row(shared, label, context.metric, g)["n_assays"]) for label in labels],
            f"{_group_name(g)} in the shared summary",
        )
        for g in groups
    ]
    width = 0.25
    with matplotlib.rc_context(_RC):
        fig = Figure(figsize=(9.2, 4.8))
        ax = fig.add_axes((0.07, 0.2, 0.91, 0.6))
        top = 0.0
        for i, label in enumerate(labels):
            rows = [_summary_row(shared, label, context.metric, g) for g in groups]
            centres = [j + (i - (len(labels) - 1) / 2) * width for j in range(len(groups))]
            # Narrower than its slot: the surface shows between neighbouring bars.
            ax.bar(
                centres,
                [float(r["value"]) for r in rows],
                width=width - 0.03,
                color=SERIES[i],
                label=context.name(label),
                zorder=2,
            )
            for centre, row in zip(centres, rows, strict=True):
                ax.plot(
                    [centre, centre],
                    [float(row["ci_low"]), float(row["ci_high"])],
                    color=TEXT,
                    linewidth=1,
                    zorder=3,
                )
                ax.annotate(
                    f"{float(row['value']):.2f}",
                    (centre, float(row["ci_high"])),
                    xytext=(0, 3),
                    textcoords="offset points",
                    ha="center",
                    fontsize=7.5,
                    color=TEXT_SECONDARY,
                )
                top = max(top, float(row["ci_high"]))
        ax.set_xticks(
            range(len(groups)),
            [f"{_group_name(g)}\nn = {n}" for g, n in zip(groups, counts, strict=True)],
        )
        ax.set_xlim(-0.55, len(groups) - 0.45)
        ax.set_ylim(0, top * 1.12)
        ax.set_ylabel(f"{context.metric_name} (ProteinGym aggregate)")
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.legend(
            loc="lower left",
            bbox_to_anchor=(0, 1.0),
            ncols=len(labels),
            frameon=False,
            handlelength=1,
            handleheight=1,
            columnspacing=1.6,
            borderaxespad=0.2,
        )
        _titles(
            fig,
            f"Zero-shot {context.metric_name} by function type",
            f"{context.dataset}, the {counts[0]} assays every score set has, single mutants; "
            "n = assays",
            f"Whiskers: each score set's own {context.level} bootstrap CI over assays. They do "
            "not test a difference\nbetween two score sets; the paired intervals in the "
            "difference figures do.",
        )
    return fig


def difference_figure(
    comparisons: pd.DataFrame,
    pairs: Sequence[tuple[str, str]],
    context: FigureContext,
    *,
    title: str,
    panel_titles: Sequence[str],
    setting: str,
) -> Figure:
    """Paired differences ``A - B`` with their paired-bootstrap CIs, one panel per pair.

    Rows are the overall aggregate and the function types. A filled marker means the interval
    excludes zero. All panels share the x axis, so the sizes of the differences compare.

    Args:
        comparisons: Paired comparisons (``mutascope eval compare``) holding every pair.
        pairs: ``(A, B)`` score-set labels, one panel each.
        context: Metric, names and dataset.
        title: Figure title.
        panel_titles: One per pair.
        setting: What the pairs have in common, for the subtitle (e.g. ``single mutants``).
    """
    groups: list[str | None] = [None, *FUNCTION_TYPES]
    table = [
        [_comparison_row(comparisons, a, b, context.metric, g) for g in groups] for a, b in pairs
    ]
    counts = [
        _same_count([int(panel[j]["n_assays"]) for panel in table], f"{_group_name(g)}, all pairs")
        for j, g in enumerate(groups)
    ]
    with matplotlib.rc_context(_RC):
        fig = Figure(figsize=(3.1 * len(pairs) + 2.3, 4.4))
        left, right, gap = 2.15 / fig.get_figwidth(), 0.985, 0.03
        panel_width = (right - left - gap * (len(pairs) - 1)) / len(pairs)
        axes: list[Axes] = []
        for k, (panel, panel_title) in enumerate(zip(table, panel_titles, strict=True)):
            ax = fig.add_axes(
                (left + k * (panel_width + gap), 0.24, panel_width, 0.52),
                sharex=axes[0] if axes else None,
            )
            axes.append(ax)
            ax.axvline(0, color=TEXT_SECONDARY, linewidth=1, zorder=1)
            for j, row in enumerate(panel):
                low, high, diff = float(row["ci_low"]), float(row["ci_high"]), float(row["diff"])
                clear = low > 0 or high < 0
                ax.plot([low, high], [j, j], color=SERIES[0], linewidth=2, zorder=2)
                ax.plot(
                    [diff],
                    [j],
                    linestyle="none",
                    marker="o",
                    markersize=8,
                    markerfacecolor=SERIES[0] if clear else SURFACE,
                    markeredgecolor=SERIES[0],
                    markeredgewidth=1.6,
                    zorder=3,
                )
                ax.annotate(
                    _signed(diff),
                    (diff, j),
                    xytext=(0, 7),
                    textcoords="offset points",
                    ha="center",
                    fontsize=7.5,
                    color=TEXT_SECONDARY,
                    # Keeps a label legible where it sits on the zero line or a gridline.
                    bbox={"facecolor": SURFACE, "edgecolor": "none", "pad": 1},
                    zorder=4,
                )
            ax.set_ylim(len(groups) - 0.5, -0.7)
            ax.set_yticks(
                range(len(groups)),
                [f"{_group_name(g)} (n = {n})" for g, n in zip(groups, counts, strict=True)],
            )
            ax.tick_params(axis="y", labelleft=k == 0)
            ax.axhline(0.5, color=GRID, linewidth=0.8)
            ax.grid(axis="x", color=GRID, linewidth=0.8)
            ax.set_axisbelow(True)
            ax.spines["left"].set_visible(False)
            ax.set_title(panel_title, fontsize=9, loc="left", color=TEXT, pad=6)
            ax.set_xlabel(f"Difference in {context.metric_name}")
            ax.xaxis.set_major_locator(MaxNLocator(nbins=5))
        _titles(
            fig,
            title,
            f"{context.dataset}, {counts[0]} assays, {setting}; n = assays",
            f"Lines: {context.level} paired-bootstrap CI of the difference in the ProteinGym "
            "aggregate. Filled marker: the interval\nexcludes zero; hollow: it does not. "
            "Not corrected for the number of comparisons.",
        )
    return fig


MODEL_NAMES = {"esm2_650m": "ESM-2 650M", "saprot_650m_af2": "SaProt 650M"}
BLOCK_NAMES = {
    "onehot": "wild type / mutant one-hot",
    "blosum62": "BLOSUM62",
    "rel_position": "relative position",
    "structure": "structure (pLDDT, SASA, contacts)",
    "score": "zero-shot score",
    "log_probs": "log-probabilities",
    "embedding": "embedding",
}


def _block_name(block: str) -> str:
    model, _, kind = block.partition(":")
    if not kind:
        return BLOCK_NAMES.get(block, block)
    return f"{MODEL_NAMES.get(model, model)} {BLOCK_NAMES.get(kind, kind)}"


def _block_kind(block: str) -> int:
    """Colour slot: 0 zero-shot score, 1 embedding, 2 any other feature."""
    kind = block.partition(":")[2]
    return 0 if kind == "score" else 1 if kind == "embedding" else 2


def shap_figure(
    importance: pd.DataFrame,
    assays: Sequence[str],
    function_types: Mapping[str, str],
    *,
    top: int,
    dataset: str,
) -> Figure:
    """Block-level SHAP importance of one model, one panel per assay (``train shap``).

    Bars are each block's share of the summed block importance (mean ``|sum of the block's
    SHAP values|``, :class:`~mutascope.eval.schemas.ShapImportanceSchema`), the ``top``
    largest per assay. Nothing is computed: shares are read from the table.
    """
    blocks = importance[importance["level"] == "block"]
    labels = set(zip(importance["model"], importance["feature_set"], strict=True))
    if len(labels) != 1:
        raise FigureDataError(f"expected one model and feature set, got {sorted(labels)}")
    ((model, feature_set),) = labels
    with matplotlib.rc_context(_RC):
        fig = Figure(figsize=(4.0 * len(assays) + 1.0, 0.32 * top + 2.3))
        left, right, gap = 0.015, 0.985, 0.05
        width = (right - left - gap * (len(assays) - 1)) / len(assays)
        for k, assay_id in enumerate(assays):
            rows = blocks[blocks["assay_id"] == assay_id]
            if rows.empty:
                raise FigureDataError(f"no SHAP importance for {assay_id}")
            rows = rows.sort_values("share", ascending=False).head(top)
            ax = fig.add_axes((left + k * (width + gap), 0.17, width, 0.62))
            ax.set_xlim(0, max(0.6, float(rows["share"].max()) + 0.25))
            for j, row in enumerate(rows.itertuples(index=False)):
                share = float(str(row.share))
                ax.barh(j, share, height=0.7, color=SERIES[_block_kind(str(row.name))])
                ax.text(
                    share + 0.015,
                    j,
                    f"{_block_name(str(row.name))}  {share:.0%}",
                    va="center",
                    fontsize=7.5,
                    color=TEXT,
                )
            ax.set_ylim(top - 0.5, -0.6)
            ax.set_yticks([])
            ax.grid(axis="x", color=GRID, linewidth=0.8)
            ax.set_axisbelow(True)
            ax.spines["left"].set_visible(False)
            ax.xaxis.set_major_locator(MaxNLocator(nbins=4))
            ax.xaxis.set_major_formatter(lambda value, _: f"{value:.0%}")  # pyright: ignore[reportUnknownLambdaType]
            n = int(str(rows["n_variants"].iloc[0]))
            ax.set_title(
                f"{assay_id}\n{n:,} variants, {_group_name(function_types[assay_id])}",
                fontsize=8.5,
                loc="left",
                color=TEXT,
                pad=6,
            )
            ax.set_xlabel("Share of block importance")
        _titles(
            fig,
            f"What LightGBM uses: SHAP importance by feature block ({model}, {feature_set})",
            f"{dataset}; one model per assay, fit on all its variants",
            "Block importance: mean over variants of |sum of the block's SHAP values| "
            "(TreeSHAP), as a share of all blocks. Colour: blue zero-shot score,\n"
            "orange embedding, aqua other features. Explains what the model uses; it is not "
            "a performance estimate.",
        )
    return fig


def save_figure(
    figure: Figure, out_dir: Path, name: str, formats: Sequence[str], dpi: int
) -> list[Path]:
    """Write ``<out_dir>/<name>.<format>`` for every format and return the paths.

    The files carry no creation date or software version, so redrawing unchanged results gives
    identical files and a diff of ``results/figures`` shows real changes only. matplotlib ends
    some SVG lines with a space; those are removed here, because the repository's
    trailing-whitespace hook would otherwise rewrite the file on commit and the committed
    figure would no longer be the one this function wrote.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    blank: dict[str, dict[str, str | None]] = {
        "svg": {"Date": None, "Creator": None},
        "png": {"Software": None},
    }
    paths: list[Path] = []
    for fmt in formats:
        if fmt not in blank:
            raise FigureDataError(f"unsupported figure format {fmt!r}; use {sorted(blank)}")
        path = out_dir / f"{name}.{fmt}"
        buffer = io.BytesIO()
        with matplotlib.rc_context(_RC):
            figure.savefig(buffer, format=fmt, dpi=dpi, metadata=blank[fmt])
        data = buffer.getvalue()
        if fmt == "svg":
            lines = [line.rstrip() for line in data.decode("utf-8").splitlines()]
            data = ("\n".join(lines).rstrip("\n") + "\n").encode("utf-8")
        path.write_bytes(data)
        paths.append(path)
    return paths


def _titles(fig: Figure, title: str, subtitle: str, note: str) -> None:
    fig.text(0.02, 0.965, title, fontsize=12, fontweight="bold", va="top")
    fig.text(0.02, 0.905, subtitle, fontsize=9, color=TEXT_SECONDARY, va="top")
    fig.text(0.02, 0.02, note, fontsize=7.5, color=TEXT_SECONDARY, va="bottom", linespacing=1.4)


def _group_name(group: str | None) -> str:
    return "Overall" if group is None else FUNCTION_NAMES.get(group, group)


def _signed(value: object) -> str:
    """``+0.006`` / ``-0.002`` with a real minus sign; ``0.000`` for what rounds to zero."""
    text = f"{float(str(value)):+.3f}"
    return "0.000" if text[1:] == "0.000" else text.replace("-", "\N{MINUS SIGN}")


def _same_count(counts: Sequence[int], what: str) -> int:
    if len(set(counts)) != 1:
        raise FigureDataError(f"{what}: different assay counts {sorted(set(counts))}")
    return counts[0]


def _level(frame: pd.DataFrame, metric: str, group: str | None) -> pd.DataFrame:
    """Zero-shot rows of one metric at one level: overall (``group`` None) or a function type."""
    rows = frame[frame["split"].isna() & (frame["metric"] == metric)]
    if group is None:
        return rows[rows["level"] == "overall"]
    return rows[(rows["level"] == "function") & (rows["group"] == group)]


def _summary_row(summary: pd.DataFrame, label: str, metric: str, group: str | None) -> pd.Series:
    rows = _level(summary[summary["model"] == label], metric, group)
    if len(rows) != 1 or rows["value"].isna().any():
        raise FigureDataError(
            f"need one defined summary row for {label}, {metric}, {_group_name(group)}; "
            f"found {len(rows)}"
        )
    return rows.iloc[0]


def _comparison_row(
    comparisons: pd.DataFrame, a: str, b: str, metric: str, group: str | None
) -> pd.Series:
    pair = comparisons[(comparisons["model_a"] == a) & (comparisons["model_b"] == b)]
    rows = _level(pair, metric, group)
    if len(rows) != 1 or rows["diff"].isna().any():
        raise FigureDataError(
            f"no paired comparison {a} - {b} for {metric}, {_group_name(group)}: add "
            f"[{a}, {b}] to eval.comparisons and run `mutascope eval compare`"
        )
    return rows.iloc[0]


MODE_NAMES = {"random": "random calibration folds", "scheme": "scheme-like calibration folds"}
INTERVAL_NAMES = {"absolute": "constant width", "normalized": "adaptive"}
SCHEME_NAMES = {
    "random": "Random split",
    "modulo": "Modulo split",
    "contiguous": "Contiguous split",
}


def calibration_figure(summary: pd.DataFrame, *, title: str, dataset: str) -> Figure:
    """Target vs actual coverage per scheme (panel), calibration mode (colour) and interval type
    (filled: constant width, hollow: adaptive), from ``uncertainty_<label>_summary.parquet``.

    Markers are the mean over assays of per-assay coverage; whiskers the 10th-90th percentile of
    the per-assay values; the number under a marker is how many assays fell more than the
    configured margin below target. All three are read from the summary.
    """
    rows = summary[(summary["metric"] == "coverage") & (summary["size_bin"] == "all")]
    if rows.empty:
        raise FigureDataError("no coverage rows in the uncertainty summary")
    schemes = [s for s in SCHEME_NAMES if s in set(rows["scheme"])]
    levels = sorted({float(v) for v in rows["level"]})
    series = [(m, i) for m in MODE_NAMES for i in INTERVAL_NAMES]
    with matplotlib.rc_context(_RC):
        fig = Figure(figsize=(11.0, 4.6))
        left, right, gap = 0.06, 0.985, 0.03
        width = (right - left - gap * (len(schemes) - 1)) / len(schemes)
        span = max(levels) - min(levels) or 0.1
        for k, scheme in enumerate(schemes):
            ax = fig.add_axes((left + k * (width + gap), 0.2, width, 0.56))
            low, high = min(levels) - 0.6 * span, max(levels) + 0.6 * span
            ax.plot([low, high], [low, high], color=TEXT_SECONDARY, linewidth=0.8, linestyle="--")
            for j, (mode, interval) in enumerate(series):
                offset = (j - (len(series) - 1) / 2) * 0.12 * span
                for level in levels:
                    stats = rows[
                        (rows["scheme"] == scheme)
                        & (rows["mode"] == mode)
                        & (rows["interval"] == interval)
                        & (rows["level"] == level)
                    ].set_index("statistic")["value"]
                    for needed in ("mean", "q10", "q90", "n_below"):
                        if needed not in stats.index:
                            raise FigureDataError(
                                f"no {needed} for {scheme}, {mode}, {interval}, {level}"
                            )
                    colour = SERIES[list(MODE_NAMES).index(mode)]
                    x = level + offset
                    ax.plot([x, x], [stats["q10"], stats["q90"]], color=colour, linewidth=1.2)
                    ax.plot(
                        x,
                        stats["mean"],
                        marker="o",
                        markersize=6,
                        color=colour,
                        markerfacecolor=colour if interval == "absolute" else SURFACE,
                        markeredgewidth=1.4,
                    )
                    ax.text(
                        x,
                        float(stats["q10"]) - 0.015,
                        f"{int(stats['n_below'])}",
                        ha="center",
                        va="top",
                        fontsize=6.5,
                        color=TEXT_SECONDARY,
                    )
            ax.set_xlim(low, high)
            ax.set_ylim(min(0.4, low), 1.0)
            ax.set_xticks(levels)
            ax.xaxis.set_major_formatter(lambda value, _: f"{value:.0%}")  # pyright: ignore[reportUnknownLambdaType]
            ax.yaxis.set_major_formatter(lambda value, _: f"{value:.0%}")  # pyright: ignore[reportUnknownLambdaType]
            ax.grid(axis="y", color=GRID, linewidth=0.8)
            ax.set_axisbelow(True)
            ax.set_title(SCHEME_NAMES[scheme], fontsize=9.5, loc="left", color=TEXT)
            ax.set_xlabel("Target coverage")
            if k == 0:
                ax.set_ylabel("Actual coverage on test folds")
            else:
                ax.set_yticklabels([])
        handles = [
            Line2D(
                [],
                [],
                marker="o",
                linestyle="",
                color=SERIES[list(MODE_NAMES).index(m)],
                markerfacecolor=SERIES[list(MODE_NAMES).index(m)] if i == "absolute" else SURFACE,
                markeredgewidth=1.4,
                label=f"{MODE_NAMES[m]}, {INTERVAL_NAMES[i]}",
            )
            for m, i in series
        ]
        fig.legend(
            handles=handles,
            loc="upper left",
            bbox_to_anchor=(0.012, 0.875),
            ncol=4,
            frameon=False,
            fontsize=7.5,
        )
        n_assays = int(
            rows[rows["statistic"] == "n_assays"]["value"].max()  # pyright: ignore[reportArgumentType]
        )
        _titles(
            fig,
            title,
            f"{dataset}; {n_assays} assays; CV+ intervals calibrated inside the training folds",
            "Marker: mean over assays of per-assay coverage of the test folds; whisker: 10th-90th "
            "percentile over assays; number: assays more than 10 points below target.\n"
            "Dashed: actual = target. Filled: constant width (absolute residuals); hollow: "
            "adaptive (residuals / fold-ensemble spread).",
        )
    return fig


def width_error_figure(bins: pd.DataFrame, *, level: float, title: str, dataset: str) -> Figure:
    """Mean absolute error vs mean interval width per within-assay width decile, one panel per
    scheme, from ``uncertainty_<label>_bins.parquet`` (both in assay-SD units).

    If width tracks error, the adaptive lines rise; a constant-width interval's deciles bunch up.
    """
    rows = bins[bins["level"] == level]
    if rows.empty:
        raise FigureDataError(f"no width bins at level {level}")
    schemes = [s for s in SCHEME_NAMES if s in set(rows["scheme"])]
    with matplotlib.rc_context(_RC):
        fig = Figure(figsize=(11.0, 4.4))
        left, right, gap = 0.06, 0.985, 0.04
        width = (right - left - gap * (len(schemes) - 1)) / len(schemes)
        for k, scheme in enumerate(schemes):
            ax = fig.add_axes((left + k * (width + gap), 0.2, width, 0.56))
            for mode in MODE_NAMES:
                for interval in INTERVAL_NAMES:
                    line = rows[
                        (rows["scheme"] == scheme)
                        & (rows["mode"] == mode)
                        & (rows["interval"] == interval)
                    ].sort_values("bin")
                    if line.empty:
                        raise FigureDataError(f"no bins for {scheme}, {mode}, {interval}")
                    colour = SERIES[list(MODE_NAMES).index(mode)]
                    ax.plot(
                        line["width_sd"],
                        line["abs_error_sd"],
                        color=colour,
                        linewidth=1.4,
                        linestyle="-" if interval == "normalized" else ":",
                        marker="o" if interval == "normalized" else None,
                        markersize=3.5,
                        markerfacecolor=SURFACE,
                        label=f"{MODE_NAMES[mode]}, {INTERVAL_NAMES[interval]}",
                    )
            ax.grid(color=GRID, linewidth=0.8)
            ax.set_axisbelow(True)
            ax.set_title(SCHEME_NAMES[scheme], fontsize=9.5, loc="left", color=TEXT)
            ax.set_xlabel(f"Mean {level:.0%} interval width (assay SD)")
            if k == 0:
                ax.set_ylabel("Mean |error| (assay SD)")
        handles, labels = fig.axes[0].get_legend_handles_labels()
        fig.legend(
            handles,
            labels,
            loc="upper left",
            bbox_to_anchor=(0.012, 0.875),
            ncol=4,
            frameon=False,
            fontsize=7.5,
        )
        _titles(
            fig,
            title,
            f"{dataset}; deciles of interval width within each assay, averaged over assays",
            "Each point: one width decile (10% of an assay's test variants by interval width), "
            "mean width vs mean |prediction - target|, averaged over assays.\nSolid: adaptive "
            "intervals; dotted: constant width. Error of the outer model's point prediction.",
        )
    return fig


def _final_comparison(comparisons: pd.DataFrame, run: str, scheme: str) -> pd.Series:
    """The one overall Spearman comparison of ``run`` against its comparator under ``scheme``."""
    rows = comparisons[
        (comparisons["model_a"] == run)
        & (comparisons["split"] == scheme)
        & (comparisons["metric"] == "spearman")
        & (comparisons["level"] == "overall")
    ]
    if len(rows) != 1 or rows["diff"].isna().any():
        raise FigureDataError(
            f"need one paired comparison of {run} under {scheme}, found {len(rows)}"
        )
    return rows.iloc[0]


def ladder_figure(
    summary: pd.DataFrame,
    comparisons: pd.DataFrame,
    runs: Sequence[str],
    names: Mapping[str, str],
    *,
    dataset: str,
    level: str,
) -> Figure:
    """The model ladder per scheme: each run's paired difference from its zero-shot comparator.

    One panel per scheme, one row per run (ladder order, top to bottom). The marker and line are
    the paired difference and its paired-bootstrap CI (filled: excludes zero); the text gives the
    run's own aggregate and assay count. Own CIs are not drawn: they do not compare runs.
    """

    def name(label: str) -> str:
        if label not in names:
            raise FigureDataError(f"no display name for {label!r} in train.final_table.names")
        return names[label]

    schemes = [s for s in SCHEME_NAMES if s in set(comparisons["split"].dropna())]
    with matplotlib.rc_context(_RC):
        fig = Figure(figsize=(11.5, 1.2 + 0.52 * len(runs) + 1.6))
        left, right, gap = 0.235, 0.985, 0.025
        width = (right - left - gap * (len(schemes) - 1)) / len(schemes)
        axes: list[Axes] = []
        labels: list[str] = []
        for k, scheme in enumerate(schemes):
            ax = fig.add_axes(
                (left + k * (width + gap), 0.2, width, 0.6), sharex=axes[0] if axes else None
            )
            axes.append(ax)
            ax.axvline(0, color=TEXT_SECONDARY, linewidth=1, zorder=1)
            for j, run in enumerate(runs):
                pair = _final_comparison(comparisons, run, scheme)
                own = summary[
                    (summary["model"] == run)
                    & (summary["split"] == scheme)
                    & (summary["metric"] == "spearman")
                    & (summary["level"] == "overall")
                ]
                if len(own) != 1:
                    raise FigureDataError(f"need one summary row for {run} under {scheme}")
                low, high, diff = float(pair["ci_low"]), float(pair["ci_high"]), float(pair["diff"])
                clear = low > 0 or high < 0
                ax.plot([low, high], [j, j], color=SERIES[0], linewidth=2, zorder=2)
                ax.plot(
                    [diff],
                    [j],
                    linestyle="none",
                    marker="o",
                    markersize=7,
                    markerfacecolor=SERIES[0] if clear else SURFACE,
                    markeredgecolor=SERIES[0],
                    markeredgewidth=1.5,
                    zorder=3,
                )
                own_value = f"{float(own['value'].iloc[0]):.3f}".replace("-", "\N{MINUS SIGN}")
                ax.annotate(
                    f"{_signed(diff)} (own {own_value})",
                    (diff, j),
                    xytext=(0, 8),
                    textcoords="offset points",
                    ha="center",
                    fontsize=7,
                    color=TEXT_SECONDARY,
                    bbox={"facecolor": SURFACE, "edgecolor": "none", "pad": 1},
                    zorder=4,
                )
                if k == 0:
                    labels.append(
                        f"{name(run)}\nvs {name(str(pair['model_b']))}, n = {int(pair['n_assays'])}"
                    )
            ax.set_ylim(len(runs) - 0.5, -0.6)
            ax.set_yticks(range(len(runs)), labels if k == 0 else [""] * len(runs))
            ax.tick_params(axis="y", labelleft=k == 0, labelsize=7.5)
            ax.grid(axis="x", color=GRID, linewidth=0.8)
            ax.set_axisbelow(True)
            ax.spines["left"].set_visible(False)
            ax.set_title(SCHEME_NAMES[scheme], fontsize=9.5, loc="left", color=TEXT, pad=6)
            ax.set_xlabel("Spearman minus zero-shot (paired)")
            ax.xaxis.set_major_locator(MaxNLocator(nbins=5))
        lo, hi = axes[0].get_xlim()
        axes[0].set_xlim(lo - 0.08 * (hi - lo), hi + 0.08 * (hi - lo))
        _titles(
            fig,
            "Supervised models against the best zero-shot model on the same assays",
            f"{dataset}; pooled Spearman, ProteinGym aggregate; each run paired with SaProt 650M "
            "(structure features) or ESM-2 650M (sequence only) on the assays both have",
            f"Lines: {level} paired-bootstrap CI of the difference. Filled marker: excludes zero. "
            '"own": the run\'s aggregate on its n assays.\nThe MLP runs on assays with at least '
            "300 variants only, so its n and its comparator's value differ from ridge's.",
        )
    return fig


def size_figure(
    differences: pd.DataFrame,
    by_size: pd.DataFrame,
    runs: Sequence[str],
    names: Mapping[str, str],
    bins: Sequence[str],
    *,
    dataset: str,
    level: str,
) -> Figure:
    """Supervised minus zero-shot by assay size: per-bin paired differences, one panel per scheme.

    Each run (colour) gets the paired-bootstrap mean difference within each size bin it has
    (``final_size_comparisons``); the first run also gets its per-assay differences as grey dots
    (``final_assay_differences``). Bins a run has no assay in are left empty.
    """
    if len(runs) > len(SERIES):
        raise FigureDataError(f"at most {len(SERIES)} runs per size figure, got {len(runs)}")
    for run in runs:
        if run not in names:
            raise FigureDataError(f"no display name for {run!r} in train.final_table.names")
        if not (by_size["model_a"] == run).any():
            raise FigureDataError(f"no size comparisons for {run}")
    comparators = sorted(set(by_size.loc[by_size["model_a"].isin(runs), "model_b"].astype(str)))
    schemes = [s for s in SCHEME_NAMES if s in set(by_size["split"].dropna())]
    index = {label: i for i, label in enumerate(bins)}
    with matplotlib.rc_context(_RC):
        fig = Figure(figsize=(12.0, 4.9))
        left, right, gap = 0.06, 0.985, 0.025
        width = (right - left - gap * (len(schemes) - 1)) / len(schemes)
        axes: list[Axes] = []
        for k, scheme in enumerate(schemes):
            ax = fig.add_axes(
                (left + k * (width + gap), 0.24, width, 0.54), sharey=axes[0] if axes else None
            )
            axes.append(ax)
            ax.axhline(0, color=TEXT_SECONDARY, linewidth=1, zorder=1)
            dots = differences[
                (differences["model_a"] == runs[0]) & (differences["split"] == scheme)
            ]
            ax.plot(
                [index[b] - 0.3 for b in dots["size_bin"]],
                dots["diff"],
                linestyle="none",
                marker="o",
                markersize=2.5,
                color=TEXT_SECONDARY,
                alpha=0.35,
                zorder=2,
            )
            for i, run in enumerate(runs):
                rows = by_size[(by_size["model_a"] == run) & (by_size["split"] == scheme)]
                for row in rows.to_dict("records"):
                    x = index[str(row["size_bin"])] + (i - (len(runs) - 1) / 2) * 0.17 + 0.08
                    low, high, diff = (
                        float(row["ci_low"]),
                        float(row["ci_high"]),
                        float(row["diff"]),
                    )
                    clear = low > 0 or high < 0
                    ax.plot([x, x], [low, high], color=SERIES[i], linewidth=1.8, zorder=3)
                    ax.plot(
                        [x],
                        [diff],
                        marker="o",
                        markersize=5.5,
                        color=SERIES[i],
                        markerfacecolor=SERIES[i] if clear else SURFACE,
                        markeredgewidth=1.4,
                        linestyle="none",
                        zorder=4,
                        label=names[run]
                        if k == 0 and row["size_bin"] == rows["size_bin"].iloc[0]
                        else None,
                    )
            counts = by_size[(by_size["model_a"] == runs[0]) & (by_size["split"] == scheme)]
            n_of = dict(
                zip(counts["size_bin"].astype(str), counts["n_assays"].astype(int), strict=True)
            )
            ax.set_xticks(
                range(len(bins)), [f"{b}\nn = {n_of[b]}" if b in n_of else b for b in bins]
            )
            ax.tick_params(axis="x", labelsize=7)
            ax.set_xlim(-0.6, len(bins) - 0.4)
            ax.grid(axis="y", color=GRID, linewidth=0.8)
            ax.set_axisbelow(True)
            ax.set_title(SCHEME_NAMES[scheme], fontsize=9.5, loc="left", color=TEXT)
            ax.set_xlabel("Measured variants per assay")
            if k == 0:
                ax.set_ylabel("Spearman minus zero-shot")
            else:
                ax.tick_params(axis="y", labelleft=False)
        fig.legend(
            loc="upper left",
            bbox_to_anchor=(0.012, 0.875),
            ncol=len(runs),
            frameon=False,
            fontsize=7.5,
        )
        shown = ", ".join(names.get(c, c) for c in comparators)
        _titles(
            fig,
            "Where supervision pays: supervised minus zero-shot by assay size",
            f"{dataset}; pooled Spearman; comparator {shown} "
            f"on the same assays; n = assays of {names[runs[0]]} per bin",
            f"Markers and lines: per-bin mean of per-assay differences, {level} paired-bootstrap "
            "CI over assays (filled: excludes zero). A plain mean: a bin has too few assays per "
            "function type\nfor the ProteinGym aggregate. Grey dots: per-assay differences of "
            f"{names[runs[0]]}. The MLP has no assay below 300 variants (its minimum); final "
            "landscapes start at 300.",
        )
    return fig
