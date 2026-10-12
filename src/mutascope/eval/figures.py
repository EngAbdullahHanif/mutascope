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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib
import pandas as pd
from matplotlib.axes import Axes
from matplotlib.figure import Figure
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


def save_figure(
    figure: Figure, out_dir: Path, name: str, formats: Sequence[str], dpi: int
) -> list[Path]:
    """Write ``<out_dir>/<name>.<format>`` for every format and return the paths.

    The files carry no creation date or software version, so redrawing unchanged results gives
    identical files and a diff of ``results/figures`` shows real changes only.
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
        with matplotlib.rc_context(_RC):
            figure.savefig(path, format=fmt, dpi=dpi, metadata=blank[fmt])
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
