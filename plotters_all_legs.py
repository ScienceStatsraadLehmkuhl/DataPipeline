"""
Expedition-length time series plots, built from the combined 5-min files
that combine_dataset_new.py produces (one CSV per experiment/instrument,
spanning all legs). Figures are saved under combined_files/figures/{pdf,png}.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import matplotlib.transforms as mtransforms
import pandas as pd

from DataPipeline.globals import EXPERIMENTS, INSTRUMENTS, PLOT_LABELS, get_variables
from DataPipeline.main_globals import ONLY_EXPERIMENTS, ONLY_INSTRUMENTS, ONLY_VARIABLES
from DataPipeline.combine_dataset_new import combined_output_folder, combined_output_root, resolve_cruise
from DataPipeline.manual_data_read import get_logsheet_paths, load_leg_windows
from DataPipeline.plotters_by_leg import plot_property_over_time_pub, process_fig

EXPEDITION_INTERVAL = "5min"
# Data points are 5 min apart; break the line only on gaps well beyond that
# (e.g. in-port time between legs) rather than on every normal sample step.
EXPEDITION_MAX_GAP = "15min"
EXPEDITION_FIGSIZE = (10, 3.5)
# Less bottom room than the default: no rotated date labels, just a month row and a year row
EXPEDITION_MARGINS = dict(left=0.12, right=0.98, bottom=0.2, top=0.86)


def expedition_figures_root(cruise: str | None = None) -> Path:
    return combined_output_root(cruise) / "FIGURES"


def combined_csv_path(experiment: str, instrument: str, interval: str = EXPEDITION_INTERVAL, cruise: str | None = None) -> Path:
    return combined_output_folder(interval, cruise=cruise) / f"{resolve_cruise(cruise)}_{experiment}_{instrument}_{interval}_COMBINED.csv"


def load_leg_bounds(cruise: str) -> pd.DataFrame | None:
    """Leg number + tz-naive UTC start/end, or None if the logsheet can't be read."""
    leg_start_end_path, _ = get_logsheet_paths(cruise)
    try:
        # .copy(): load_leg_windows is lru_cached and returns the SAME object
        # to every caller, so tz-stripping it in place below would hand
        # tz-naive leg windows to later callers (gap analysis crashed on
        # comparing them with tz-aware data).
        legs = load_leg_windows(str(leg_start_end_path)).copy()
    except Exception as e:
        print(f"      [WARN] Leg boundaries unavailable ({e}); plotting without leg markers")
        return None
    legs["start"] = legs["start"].dt.tz_localize(None)
    legs["end"] = legs["end"].dt.tz_localize(None)
    return legs


def add_leg_markers(ax, legs: pd.DataFrame) -> None:
    """Dashed lines at each leg start/end, leg numbers centred above the axes, "Leg" left of the first."""
    x0, x1 = (pd.Timestamp(mdates.num2date(x)).tz_localize(None) for x in ax.get_xlim())
    label_tf = mtransforms.blended_transform_factory(ax.transData, ax.transAxes)

    boundaries = set()
    first_label = True
    for row in legs.itertuples():
        if row.end < x0 or row.start > x1:
            continue
        boundaries.update(t for t in (row.start, row.end) if x0 <= t <= x1)

        # Centre the number on the visible part of the leg
        vis_start, vis_end = max(row.start, x0), min(row.end, x1)
        centre = vis_start + (vis_end - vis_start) / 2
        ax.text(centre, 1.015, str(row.leg), transform=label_tf, ha="center", va="bottom",
                fontsize=6.5, color="0.3", clip_on=False)
        if first_label:
            ax.annotate("Leg", xy=(mdates.date2num(centre), 1.015), xycoords=label_tf, xytext=(-7, 0), textcoords="offset points",
                        ha="right", va="bottom", fontsize=6.5, color="0.3", annotation_clip=False)
            first_label = False

    for t in sorted(boundaries):
        ax.axvline(t, color="0.5", linewidth=0.5, linestyle=(0, (3, 3)), alpha=0.7, zorder=0)


def add_month_year_axis(ax, min_label_frac: float = 0.03) -> None:
    """
    Replace the date tick labels with month names centred between month-start ticks,
    and the year centred below, with a divider at each year boundary.
    Segments too narrow for their label (partial months/years at the edges) are left blank.
    """
    x0, x1 = (pd.Timestamp(mdates.num2date(x)).tz_localize(None) for x in ax.get_xlim())
    span = (x1 - x0).total_seconds()
    tf = mtransforms.blended_transform_factory(ax.transData, ax.transAxes)

    month_starts = pd.date_range(x0, x1, freq="MS")
    year_starts = pd.date_range(x0, x1, freq="YS")

    ax.xaxis.set_major_locator(mticker.FixedLocator(mdates.date2num(month_starts.to_pydatetime())))
    ax.xaxis.set_major_formatter(mticker.NullFormatter())
    ax.xaxis.set_minor_locator(mticker.NullLocator())
    ax.tick_params(axis="x", which="major", length=4)

    def _segments(starts):
        edges = [x0, *starts, x1]
        return [(a, b) for a, b in zip(edges[:-1], edges[1:]) if b > a]

    def _label(text, a, b, offset_pts, **kw):
        if (b - a).total_seconds() / span < min_label_frac:
            return
        ax.annotate(text, xy=(mdates.date2num(a + (b - a) / 2), 0), xycoords=tf, xytext=(0, offset_pts), textcoords="offset points",
                    ha="center", va="top", fontsize=8, annotation_clip=False, **kw)

    for a, b in _segments(month_starts):
        _label(a.strftime("%b"), a, b, -6)
    for a, b in _segments(year_starts):
        _label(str(a.year), a, b, -20)

    # Divider from the axis down through the year row at each new year
    ax_height_pts = ax.get_position().height * ax.figure.get_figheight() * 72
    for t in year_starts:
        ax.plot([t, t], [0, -30 / ax_height_pts], transform=tf, color="black", linewidth=0.8, clip_on=False)


def plot_expedition_report(
    cruise: str | None = None,
    only_experiments: list[str] | None = ONLY_EXPERIMENTS,
    only_instruments: list[str] | None = ONLY_INSTRUMENTS,
    only_variables: list[str] | None = ONLY_VARIABLES,
    interval: str = EXPEDITION_INTERVAL,
    max_gap: str = EXPEDITION_MAX_GAP,
) -> int:
    fig_root = expedition_figures_root(cruise)
    outdir_pdf = fig_root / "PDF"
    outdir_png = fig_root / "PNG"
    selected_cruise = resolve_cruise(cruise)
    legs = load_leg_bounds(selected_cruise)
    # Same x-range on every figure: first leg start -> last leg end, regardless of data coverage
    xlim = (legs["start"].min(), legs["end"].max()) if legs is not None and not legs.empty else None

    experiments = EXPERIMENTS
    if only_experiments is not None:
        experiments = [e for e in experiments if e in only_experiments]

    n_written = 0
    for experiment in experiments:
        instruments = INSTRUMENTS.get(experiment, [])
        if only_instruments is not None:
            instruments = [i for i in instruments if i in only_instruments]

        # Seabird_CTD casts are vertical profiles (see main_plot_ctd.py), not a time series.
        instruments = [i for i in instruments if i != "Seabird_CTD"]

        for instrument in instruments:
            csv_path = combined_csv_path(experiment, instrument, interval=interval, cruise=cruise)
            if not csv_path.exists():
                print(f"      [SKIP] No combined {interval} file for {experiment}/{instrument}: {csv_path.name}")
                continue

            try:
                df = pd.read_csv(csv_path)
            except Exception as e:
                print(f"      [WARN] Failed reading {csv_path}: {e}")
                continue

            variables = get_variables(experiment, instrument)
            if only_variables is not None:
                variables = [v for v in variables if v in only_variables]

            for variable in variables:
                if variable not in df.columns:
                    continue

                for plot_type, kind, extra_kwargs in (
                    ("time", "line", {}),
                    ("time_pts", "scatter", {"marker_size": 0.5}),
                ):
                    try:
                        fig, ax = plot_property_over_time_pub(
                            df,
                            property_column=variable,
                            experiment=experiment,
                            instrument=instrument,
                            plot_labels=PLOT_LABELS,
                            kind=kind,
                            figsize=EXPEDITION_FIGSIZE,
                            max_gap=max_gap,
                            clip_to_leg_window=False,
                            xlim=xlim,
                            x_label="",  # month/year rows below the axis replace it
                            margins=EXPEDITION_MARGINS,
                            **extra_kwargs,
                        )
                    except ValueError as e:
                        print(f"      [SKIP] {experiment}/{instrument}/{variable} ({plot_type}): {e}")
                        continue

                    add_month_year_axis(ax)
                    if legs is not None:
                        add_leg_markers(ax, legs)

                    fig.text(0.98, 0.95, "(5 min average)", ha="right", va="top", fontsize=7, color="0.4")

                    process_fig(
                        fig,
                        name=plot_type,
                        base_name=f"{selected_cruise}_LEGSALL_{experiment}_{instrument}_{variable}",
                        outdir_pdf=outdir_pdf,
                        outdir_png=outdir_png,
                    )
                    n_written += 1
                    print(f"      [OK] Plotted expedition-length {experiment}/{instrument}/{variable} ({plot_type})")

    # Seabird_CTD profiles (depth vs variable, one colour per station) aren't a
    # time series and don't come from the combined interval files, so they're
    # plotted by main_plot_ctd from the per-leg processed files. Imported here
    # because main_plot_ctd itself imports from this module.
    from DataPipeline.main_plot_ctd import run_expedition_plotting_ctd
    n_written += run_expedition_plotting_ctd(
        selected_cruise, only_experiments=only_experiments, only_instruments=only_instruments,
    )

    print(f"\nWrote {n_written} expedition figure(s) to: {fig_root}")
    return n_written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot expedition-length time series from combined interval files.")
    parser.add_argument("--cruise", default=None, help="Cruise folder name under processed_data. Defaults to main_globals.CRUISE.")
    parser.add_argument("--interval", default=EXPEDITION_INTERVAL, help="Combined interval to plot from (must already exist under combined_files/).")
    parser.add_argument("--max-gap", default=EXPEDITION_MAX_GAP, help="Break the line when the time gap exceeds this (pandas offset string).")
    parser.add_argument("--only-experiments", nargs="+", default=ONLY_EXPERIMENTS, help="Filter to specific experiment names.")
    parser.add_argument("--only-instruments", nargs="+", default=ONLY_INSTRUMENTS, help="Filter to specific instrument names.")
    parser.add_argument("--only-variables", nargs="+", default=ONLY_VARIABLES, help="Filter to specific variable names.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    start = time.time()
    plot_expedition_report(
        cruise=args.cruise,
        only_experiments=args.only_experiments,
        only_instruments=args.only_instruments,
        only_variables=args.only_variables,
        interval=args.interval,
        max_gap=args.max_gap,
    )
    print(f"Elapsed: {time.time() - start:.1f}s")
