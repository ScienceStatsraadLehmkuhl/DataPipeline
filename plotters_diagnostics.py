"""
Diagnostic plots that go beyond one-variable-over-time.

Per leg (plot types of main_plot.run_plotting; each one is gated to the
instrument it applies to and returns None for any other):
  wind_rose             METEOROLOGY/GMX560: corrected wind direction x speed
  cleaning_diagnostics  OCEANOGRAPHY/Ferrybox_CTD: what cleaning removed/blanked
  gps_sources           NAVIGATION/GPS-MERGED-SOURCES: track, timeline and
                        coverage of GGA vs the gap-fill sources

Expedition-wide (run_expedition_diagnostics, called from
main_plot.run_expedition_plotting; figures go to combined_files/FIGURES):
  data coverage timeline   from the gap-analysis workbook
  GPS source summary       per leg, share of the leg covered by each source

Like the time plots, everything is clipped to the leg window, since processed
files are not trimmed to it. Colours: dataviz reference palette
(plotters_by_leg.PLOT_COLORS); GGA is the neutral baseline, the gap-fill
sources take categorical slots 1-3 (which validate for scatter/overlap),
wind speed uses the one-hue blue sequential ramp.
"""
from __future__ import annotations

import os

import matplotlib as mpl
import matplotlib.ticker
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from DataPipeline.combine_dataset_new import combined_output_root, resolve_cruise
from DataPipeline.globals import EXPERIMENTS, INSTRUMENTS, RENAME_COLUMNS, VARIABLES, PLOT_LABELS
from DataPipeline.input_tools import input_folders_processer
from DataPipeline.main_globals import GAP_THRESHOLD_MINUTES, GAP_THRESHOLD_MINUTES_BY_INSTRUMENT
from DataPipeline.plotters_all_legs import (
    add_leg_markers, add_month_year_axis, expedition_figures_root, load_leg_bounds,
)
from DataPipeline.plotters_by_leg import (
    PLOT_COLORS, _leg_window_naive, _plot_with_gaps, get_plot_label, process_fig,
)
from DataPipeline.preprocessing import to_utc

INK, MUTED, NEUTRAL, TRACK = (PLOT_COLORS[k] for k in ("ink", "muted", "neutral", "track"))
BLUE, ORANGE, AQUA = PLOT_COLORS["blue"], PLOT_COLORS["orange"], PLOT_COLORS["aqua"]

_RC = {
    "font.size": 9, "axes.titlesize": 10, "axes.labelsize": 9,
    "xtick.labelsize": 8, "ytick.labelsize": 8, "axes.linewidth": 0.8,
    "legend.frameon": False, "pdf.fonttype": 42, "ps.fonttype": 42,
}


def _clip_to_leg(df, time_column, leg, leg_start_end_path, sheet_name=0):
    """
    Copy of df whose time column is tz-naive UTC and restricted to the leg
    window, plus that window ((start, end), or None when no leg is given).
    Rows with an unparseable time are dropped.
    """
    t = to_utc(df[time_column]).dt.tz_localize(None)
    window = None
    keep = t.notna()
    if leg is not None:
        if leg_start_end_path is None:
            raise ValueError("If leg is provided, leg_start_end_path must also be provided.")
        window = _leg_window_naive(leg, leg_start_end_path, sheet_name)
        keep &= (t >= window[0]) & (t <= window[1])
    out = df.loc[keep].copy()
    out[time_column] = t[keep]
    return out, window


def _style_axes(ax, grid_axis="y"):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, axis=grid_axis, color=TRACK, linewidth=0.6)
    ax.set_axisbelow(True)


def _runs(mask: pd.Series, times: pd.Series):
    """Contiguous runs of True in `mask` (in row order) as (first_time, last_time) pairs."""
    m = mask.to_numpy(dtype=bool)
    if not m.any():
        return []
    edges = np.flatnonzero(np.diff(np.r_[False, m, False].astype(int)))
    return [(times.iloc[a], times.iloc[b - 1]) for a, b in zip(edges[::2], edges[1::2])]


# ---------------------------------------------------------------------------
# Wind rose  (METEOROLOGY / GMX560)
# ---------------------------------------------------------------------------

WIND_INSTRUMENT = ("METEOROLOGY", "GMX560")
WIND_DIR_COL = "corrected_wind_direction_deg"
WIND_SPEED_COL = "corrected_wind_speed"
WIND_N_SECTORS = 16
WIND_CALM_BELOW = 0.5   # m/s: direction is meaningless in a calm
# Speed classes in m/s (the GMX560 reports m/s by default; the export carries no unit column)
WIND_SPEED_EDGES = [0.5, 3, 6, 9, 12, 15, np.inf]
# One-hue sequential ramp, light -> dark (blue steps 200 300 400 500 600 700)
WIND_RAMP = ["#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


def _speed_class_labels():
    labels = []
    for lo, hi in zip(WIND_SPEED_EDGES[:-1], WIND_SPEED_EDGES[1:]):
        labels.append(f"{lo:g}+" if np.isinf(hi) else f"{lo:g}-{hi:g}")
    return labels


def draw_wind_rose(direction, speed, title):
    """
    Wind rose: share of records per direction sector, stacked by speed class.
    `direction` is where the wind blows FROM, in degrees; north is at the top.
    """
    direction = pd.to_numeric(direction, errors="coerce")
    speed = pd.to_numeric(speed, errors="coerce")
    ok = direction.notna() & speed.notna()
    direction, speed = direction[ok] % 360.0, speed[ok]
    if direction.empty:
        return None

    n_total = len(direction)
    calm = speed < WIND_CALM_BELOW
    direction, speed = direction[~calm], speed[~calm]

    width = 2 * np.pi / WIND_N_SECTORS
    sector = (((direction + 180.0 / WIND_N_SECTORS) % 360.0) // (360.0 / WIND_N_SECTORS)).astype(int).to_numpy()
    speed_class = np.digitize(speed.to_numpy(), WIND_SPEED_EDGES) - 1   # 0..len(ramp)-1

    with mpl.rc_context(_RC):
        fig = plt.figure(figsize=(7.2, 6.4))
        ax = fig.add_axes([0.06, 0.06, 0.66, 0.80], projection="polar")
        ax.set_theta_zero_location("N")
        ax.set_theta_direction(-1)

        centres = np.arange(WIND_N_SECTORS) * width
        bottom = np.zeros(WIND_N_SECTORS)
        for k, colour in enumerate(WIND_RAMP):
            share = np.array([
                np.sum((sector == s) & (speed_class == k)) for s in range(WIND_N_SECTORS)
            ]) / n_total * 100.0
            ax.bar(centres, share, width=width, bottom=bottom, color=colour,
                   edgecolor="white", linewidth=0.8)
            bottom += share

        ax.set_xticks(np.deg2rad(np.arange(0, 360, 45)))
        ax.set_xticklabels(["N", "NE", "E", "SE", "S", "SW", "W", "NW"], color=INK)
        ax.yaxis.set_major_formatter(mpl.ticker.FuncFormatter(lambda v, _: f"{v:g}%"))
        ax.set_rlabel_position(22.5)
        ax.tick_params(axis="y", labelsize=7, labelcolor=MUTED)
        ax.grid(True, color=TRACK, linewidth=0.6)
        ax.spines["polar"].set_visible(False)

        handles = [Patch(facecolor=c, edgecolor="white", label=l) for c, l in zip(WIND_RAMP, _speed_class_labels())]
        fig.legend(handles=handles, title="Wind speed (m/s, assumed)", loc="center left",
                   bbox_to_anchor=(0.76, 0.52), fontsize=8, title_fontsize=8)

        fig.text(0.06, 0.965, title, ha="left", va="top", fontsize=10, color=INK)
        fig.text(
            0.06, 0.925,
            f"Direction the wind blows from; n = {n_total:,} records; calm (< {WIND_CALM_BELOW:g} m/s) {calm.mean() * 100:.1f}%",
            ha="left", va="top", fontsize=8, color=MUTED,
        )
    return fig


def plot_wind_rose(
    df, time_column="time", experiment=None, instrument=None, plot_labels=None,
    leg=None, leg_start_end_path=None, leg_sheet_name=0, raw_csv_path=None,
):
    """
    Wind rose of the GMX560's corrected (true) wind for one leg; None for any
    other instrument. (`plot_labels`/`raw_csv_path` are unused; they keep the
    signature the same as the other per-leg diagnostic plots.)
    """
    if (experiment, instrument) != WIND_INSTRUMENT:
        return None
    missing = [c for c in (WIND_DIR_COL, WIND_SPEED_COL) if c not in df.columns]
    if missing:
        raise ValueError(f"Wind rose needs columns {missing}")

    d, _window = _clip_to_leg(df, time_column, leg, leg_start_end_path, leg_sheet_name)
    if d.empty:
        raise ValueError("After clipping to the leg window, no data remains.")
    title = f"Wind rose - {instrument}" + (f" - LEG {leg}" if leg is not None else "")
    return draw_wind_rose(d[WIND_DIR_COL], d[WIND_SPEED_COL], title)


# ---------------------------------------------------------------------------
# Cleaning diagnostics  (OCEANOGRAPHY / Ferrybox_CTD)
# ---------------------------------------------------------------------------

CLEANING_INSTRUMENT = ("OCEANOGRAPHY", "Ferrybox_CTD")
TRILUX_COLS = ("trilux_chlorophyll", "trilux_phycoerythrin", "trilux_turbidity")


def plot_cleaning_diagnostics(
    df, time_column="time", experiment=None, instrument=None, plot_labels=None,
    leg=None, leg_start_end_path=None, leg_sheet_name=0, raw_csv_path=None, ncols=2,
):
    """
    What cleaning did to the Ferrybox data of one leg: for every variable the
    cleaned series, the points blanked by the Hampel spike filter and by the
    Trilux <= 0 rule (drawn at their raw value), and the stretches of rows
    removed altogether (pressure-removal windows) shaded across all panels.

    `df` is the cleaned frame; the raw one is re-read from `raw_csv_path`
    (the leg's combined CSV) and compared row by row on `time`.
    """
    if (experiment, instrument) != CLEANING_INSTRUMENT:
        return None
    if not raw_csv_path or not os.path.exists(raw_csv_path):
        raise FileNotFoundError(f"Raw combined CSV needed for cleaning diagnostics not found: {raw_csv_path!r}")
    if plot_labels is None:
        plot_labels = PLOT_LABELS

    # raw frame with the canonical column names
    rename_map = RENAME_COLUMNS[experiment][instrument]
    raw = pd.read_csv(raw_csv_path, low_memory=False)
    keep = {k: v for k, v in rename_map.items() if k in raw.columns}
    raw = raw[list(keep)].rename(columns=keep)
    raw, window = _clip_to_leg(raw, time_column, leg, leg_start_end_path, leg_sheet_name)
    cleaned, _ = _clip_to_leg(df, time_column, leg, leg_start_end_path, leg_sheet_name)
    raw = raw.sort_values(time_column, kind="mergesort").reset_index(drop=True)
    cleaned = cleaned.sort_values(time_column, kind="mergesort")
    if raw.empty:
        raise ValueError("No raw data inside the leg window.")

    # rows of the raw frame that cleaning dropped altogether
    kept = raw[time_column].isin(cleaned[time_column])
    removed_windows = _runs(~kept, raw[time_column])
    n_removed = int((~kept).sum())

    variables = [
        v for v in VARIABLES[experiment][instrument]
        if v in raw.columns and v in cleaned.columns
    ]
    panels = []
    for var in variables:
        pair = (
            raw.loc[kept, [time_column, var]].drop_duplicates(time_column)
            .merge(cleaned[[time_column, var]].drop_duplicates(time_column), on=time_column, suffixes=("_raw", "_clean"))
        )
        r = pd.to_numeric(pair[f"{var}_raw"], errors="coerce")
        c = pd.to_numeric(pair[f"{var}_clean"], errors="coerce")
        blanked = r.notna() & c.isna()
        zero = blanked & (r <= 0) if var in TRILUX_COLS else pd.Series(False, index=pair.index)
        hampel = blanked & ~zero
        if c.notna().any() or blanked.any():
            panels.append((var, pair[time_column], r, c, hampel, zero))
    if not panels:
        raise ValueError("No Ferrybox variables to compare.")

    nrows = -(-len(panels) // ncols)
    with mpl.rc_context(_RC):
        fig, axes = plt.subplots(nrows, ncols, figsize=(7.5 * ncols, 2.1 * nrows + 1.2), sharex=True, squeeze=False)
        min_span = pd.Timedelta("30s")   # keep a one-row removal visible

        for ax, (var, t, r, c, hampel, zero) in zip(axes.flat, panels):
            for a, b in removed_windows:
                ax.axvspan(a, max(b, a + min_span), color=NEUTRAL, alpha=0.35, linewidth=0)
            _plot_with_gaps(ax, pd.DataFrame({time_column: t.to_numpy(), var: c.to_numpy()}),
                            time_column, var, BLUE, linewidth=0.8)

            # y range from what survived; blanked points far outside it stay off-scale
            finite = c.dropna()
            off_scale = 0
            if len(finite):
                lo, hi = finite.min(), finite.max()
                pad = 0.25 * (hi - lo if hi > lo else max(abs(hi), 1.0))
                ax.set_ylim(lo - pad, hi + pad)
                for mask in (hampel, zero):
                    off_scale += int(((r[mask] < lo - pad) | (r[mask] > hi + pad)).sum())
            if hampel.any():
                ax.scatter(t[hampel], r[hampel], s=22, marker="x", color=ORANGE, linewidths=1.0, zorder=3, clip_on=True)
            if zero.any():
                ax.scatter(t[zero], r[zero], s=16, marker="o", facecolors="none", edgecolors=AQUA,
                           linewidths=1.0, zorder=3, clip_on=True)

            bits = []
            if hampel.any():
                bits.append(f"{int(hampel.sum())} spikes")
            if zero.any():
                bits.append(f"{int(zero.sum())} <= 0")
            if off_scale:
                bits.append(f"{off_scale} off-scale")
            # column name first: several variables share a plot label (three "Water temperature")
            label = get_plot_label(plot_labels, experiment, instrument, var)
            ax.set_title(f"{var}  -  {label}", loc="left", fontsize=8.5, color=INK)
            if bits:
                ax.set_title(", ".join(bits), loc="right", fontsize=8, color=MUTED)
            _style_axes(ax)

        for ax in list(axes.flat)[len(panels):]:
            ax.set_visible(False)

        xlim = window if window is not None else (raw[time_column].min(), raw[time_column].max())
        axes.flat[0].set_xlim(*xlim)
        locator = mdates.AutoDateLocator(minticks=3, maxticks=6)
        for col in range(ncols):
            visible = [ax for ax in axes[:, col] if ax.get_visible()]
            if visible:   # sharex hides inner tick labels; the lowest panel of each column keeps them
                visible[-1].tick_params(axis="x", labelbottom=True)
        for ax in axes.flat:
            ax.xaxis.set_major_locator(locator)
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M"))

        handles = [
            Line2D([], [], color=BLUE, linewidth=1.2, label="Cleaned"),
            Line2D([], [], color=ORANGE, marker="x", linestyle="", markersize=6, label="Blanked by Hampel spike filter (at raw value)"),
            Line2D([], [], markerfacecolor="none", markeredgecolor=AQUA, marker="o", linestyle="", markersize=6, label="Trilux <= 0 blanked"),
            Patch(facecolor=NEUTRAL, alpha=0.35, label="Rows removed (pressure-removal windows)"),
        ]
        head = 0.95   # figure fraction left free at the top for title, note and legend
        fig.text(
            0.01, 0.995,
            f"{instrument} - cleaning diagnostics" + (f" - LEG {leg}" if leg is not None else ""),
            ha="left", va="top", fontsize=11, color=INK,
        )
        fig.text(
            0.01, 0.975,
            f"{n_removed:,} of {len(raw):,} raw rows removed ({n_removed / len(raw) * 100:.1f}%) in {len(removed_windows)} stretch(es)",
            ha="left", va="top", fontsize=8, color=MUTED,
        )
        fig.legend(handles=handles, loc="upper right", ncol=2, fontsize=8, bbox_to_anchor=(0.995, 0.998))
        fig.tight_layout(rect=[0, 0, 1, head])
    return fig


# ---------------------------------------------------------------------------
# GPS sources  (NAVIGATION / GPS-MERGED-SOURCES)
# ---------------------------------------------------------------------------

GPS_INSTRUMENT = ("NAVIGATION", "GPS-MERGED-SOURCES")
GPS_SOURCES = ["GGA", "EK80_gps", "Ferrybox", "Bridge"]      # also the priority order
GPS_LABELS = {"GGA": "GGA", "EK80_gps": "EK80 (MRU)", "Ferrybox": "Ferrybox", "Bridge": "Bridge log"}
GPS_COLORS = {"GGA": NEUTRAL, "EK80_gps": BLUE, "Ferrybox": ORANGE, "Bridge": AQUA}
GPS_MARKERS = {"GGA": ".", "EK80_gps": "o", "Ferrybox": "s", "Bridge": "^"}


def _source_seconds(times: pd.Series, sources: pd.Series) -> pd.Series:
    """
    1-second bins of the leg that hold a position fix, per source. GGA logs at
    ~10 Hz and the fill sources far more sparsely (Bridge: one per minute), so
    fixes are not comparable; each distinct second counts once, for the
    highest-priority source that has a fix in it. This is also what geotagging
    can use, since it matches with a 1-second tolerance -- a sparse source
    "covers" only the seconds it has a fix in, not the time between fixes.
    """
    rank = sources.map({s: i for i, s in enumerate(GPS_SOURCES)})
    tmp = pd.DataFrame({"sec": times.dt.floor("s"), "rank": rank}).dropna()
    tmp = tmp.sort_values("rank", kind="mergesort").drop_duplicates("sec")
    counts = tmp["rank"].astype(int).map(dict(enumerate(GPS_SOURCES))).value_counts()
    return counts.reindex(GPS_SOURCES, fill_value=0)


def _hours(seconds) -> str:
    return f"{seconds / 3600.0:.1f} h"


def plot_gps_sources(
    df, time_column="time", experiment=None, instrument=None, plot_labels=None,
    leg=None, leg_start_end_path=None, leg_sheet_name=0, raw_csv_path=None,
):
    """
    Where the leg's positions came from: track coloured by source, a timeline
    of when each source had fixes, and the leg time covered by each source
    (the gap-fill QC view). None for any other instrument.
    """
    if (experiment, instrument) != GPS_INSTRUMENT:
        return None
    frame = df
    if "source" not in frame.columns:
        if not raw_csv_path or not os.path.exists(raw_csv_path):
            raise FileNotFoundError(f"'source' column not in the cleaned file and {raw_csv_path!r} not found")
        frame = pd.read_csv(raw_csv_path)
    d, window = _clip_to_leg(frame, time_column, leg, leg_start_end_path, leg_sheet_name)
    d = d.dropna(subset=["latitude_deg", "longitude_deg"])
    if d.empty:
        raise ValueError("After clipping to the leg window, no positions remain.")

    seconds = _source_seconds(d[time_column], d["source"])
    window_seconds = (window[1] - window[0]).total_seconds() if window else float(seconds.sum())
    no_position = max(window_seconds - float(seconds.sum()), 0.0)

    with mpl.rc_context(_RC):
        fig = plt.figure(figsize=(12.5, 6.4))
        gs = fig.add_gridspec(2, 2, width_ratios=[1.05, 1.0], height_ratios=[1.0, 1.0], hspace=0.42, wspace=0.18)
        ax_map = fig.add_subplot(gs[:, 0])
        ax_tl = fig.add_subplot(gs[0, 1])
        ax_bar = fig.add_subplot(gs[1, 1])

        # -- track: GGA as a thin baseline, fill sources drawn over it.
        # Fixes at exactly 0/0 (a "no fix" placeholder, not a position) are
        # left off the map so they can't stretch it; the coverage stats keep them.
        on_map = d[~((d["latitude_deg"] == 0) & (d["longitude_deg"] == 0))]
        n_null_island = len(d) - len(on_map)
        gga = on_map[on_map["source"] == "GGA"].iloc[::10]
        ax_map.plot(gga["longitude_deg"], gga["latitude_deg"], color=NEUTRAL, linewidth=0.8, zorder=1)
        for src in reversed(GPS_SOURCES[1:]):   # lowest priority first, so the source credited in the coverage is on top
            s = on_map[on_map["source"] == src]
            if s.empty:
                continue
            dense = len(s) > 500   # a white ring around thousands of points just washes the colour out
            ax_map.scatter(s["longitude_deg"], s["latitude_deg"], s=8 if dense else 26, marker=GPS_MARKERS[src],
                           color=GPS_COLORS[src], edgecolors="none" if dense else "white", linewidths=0.6,
                           zorder=2, label=GPS_LABELS[src])
        lat_mid = float(on_map["latitude_deg"].mean()) if len(on_map) else 0.0
        ax_map.set_aspect(1.0 / max(np.cos(np.deg2rad(lat_mid)), 0.1))
        ax_map.set_xlabel("Longitude (deg)")
        ax_map.set_ylabel("Latitude (deg)")
        ax_map.set_title("Track", loc="left", color=INK)
        if n_null_island:
            ax_map.text(0.0, -0.11, f"{n_null_island:,} fixes at 0/0 not drawn", transform=ax_map.transAxes,
                        ha="left", va="top", fontsize=7.5, color=MUTED)
        _style_axes(ax_map, grid_axis="both")

        # -- timeline: one row per source; GGA thinned to keep the figure light
        for i, src in enumerate(GPS_SOURCES):
            s = d.loc[d["source"] == src, time_column]
            if len(s) > 20000:
                s = s.iloc[:: len(s) // 20000 + 1]
            ax_tl.scatter(s, np.full(len(s), i), s=90, marker="|", color=GPS_COLORS[src], linewidths=0.8)
        ax_tl.set_yticks(range(len(GPS_SOURCES)))
        ax_tl.set_yticklabels([GPS_LABELS[s] for s in GPS_SOURCES])
        ax_tl.set_ylim(len(GPS_SOURCES) - 0.5, -0.5)
        if window:
            ax_tl.set_xlim(*window)
        ax_tl.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=6))
        ax_tl.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M"))
        ax_tl.set_title("When each source had fixes", loc="left", color=INK)
        _style_axes(ax_tl, grid_axis="x")

        # -- coverage: leg time with a position, by source
        rows = [(GPS_LABELS[s], float(seconds[s]), GPS_COLORS[s]) for s in GPS_SOURCES]
        rows.append(("No fix", no_position, None))
        ypos = np.arange(len(rows))
        for y, (name, secs, colour) in zip(ypos, rows):
            if colour is None:
                ax_bar.barh(y, secs / 3600.0, color="white", edgecolor=NEUTRAL, hatch="///", linewidth=0.8, height=0.65)
            else:
                ax_bar.barh(y, secs / 3600.0, color=colour, edgecolor="white", linewidth=0.8, height=0.65)
            pct = f"{secs / window_seconds * 100:.2f}%" if window_seconds else ""
            ax_bar.text(secs / 3600.0, y, f"  {_hours(secs)}  ({pct})", va="center", ha="left", fontsize=8, color=MUTED)
        ax_bar.set_yticks(ypos)
        ax_bar.set_yticklabels([r[0] for r in rows])
        ax_bar.invert_yaxis()
        ax_bar.set_xlim(0, max(r[1] for r in rows) / 3600.0 * 1.45 or 1)
        ax_bar.set_xlabel("Hours of 1-second bins in the leg window")
        ax_bar.set_title("1-second bins with a fix, by source (a bin counts for the first source listed)", loc="left", color=INK, fontsize=9)
        _style_axes(ax_bar, grid_axis="x")

        handles, labels = ax_map.get_legend_handles_labels()
        if handles:
            order = sorted(range(len(labels)), key=lambda i: [GPS_LABELS[x] for x in GPS_SOURCES].index(labels[i]))
            ax_map.legend([handles[i] for i in order], [labels[i] for i in order],
                          loc="best", fontsize=8, title="Gap-fill sources", title_fontsize=8, markerscale=1.6)
        fig.suptitle(
            "GPS position sources" + (f" - LEG {leg}" if leg is not None else "") + f" - {len(d):,} fixes",
            x=0.01, ha="left", fontsize=11, color=INK,
        )
    return fig


# ---------------------------------------------------------------------------
# Expedition-wide figures
# ---------------------------------------------------------------------------

def _read_gap_workbook(cruise: str):
    path = combined_output_root(cruise) / f"{cruise}_gap_analysis.xlsx"
    if not path.exists():
        print(f"      [SKIP] No gap-analysis workbook ({path.name}); run gap_analysis first")
        return None, None
    gaps = pd.read_excel(path, sheet_name="gaps")
    stats = pd.read_excel(path, sheet_name="statistics")
    for col in ("previous_time", "current_time"):
        gaps[col] = to_utc(gaps[col]).dt.tz_localize(None)
    gaps["leg"] = pd.to_numeric(gaps["leg"], errors="coerce")
    stats["leg"] = pd.to_numeric(stats["leg"], errors="coerce")
    return gaps, stats


def _covered_intervals(start, end, gap_intervals):
    """The parts of [start, end] not inside any of the (sorted) gap intervals."""
    out, cur = [], start
    for a, b in gap_intervals:
        a, b = max(a, start), min(b, end)
        if b <= a:
            continue
        if a > cur:
            out.append((cur, a))
        cur = max(cur, b)
    if cur < end:
        out.append((cur, end))
    return out


# Not a continuous time series (one time per cast), so "coverage" is meaningless
COVERAGE_EXCLUDE = {"Seabird_CTD"}


def plot_coverage_timeline(cruise=None, only_experiments=None, only_instruments=None):
    """
    Expedition-wide data coverage: one row per instrument, each leg's window as
    a grey track with the time that has data drawn over it in blue. Built from
    the gap-analysis workbook, so a gap is a stretch longer than
    GAP_THRESHOLD_MINUTES in the raw combined file. Instruments without data in
    any leg are left out. Returns the number of figures written (0 or 1).
    """
    cruise = resolve_cruise(cruise)
    gaps, stats = _read_gap_workbook(cruise)
    legs = load_leg_bounds(cruise)
    if gaps is None or legs is None or legs.empty:
        return 0

    has_data = stats.loc[stats["status"] == "OK"]
    have = set(zip(has_data["experiment"], has_data["instrument"]))
    real_gaps = gaps.loc[gaps["gap_type"] != "No data"].dropna(subset=["previous_time", "current_time"])
    gap_index = {
        key: sorted(zip(g["previous_time"], g["current_time"]))
        for key, g in real_gaps.groupby(["leg", "experiment", "instrument"])
    }
    ok_legs = set(zip(has_data["leg"], has_data["experiment"], has_data["instrument"]))

    rows, skipped = [], []
    for experiment in EXPERIMENTS:
        if only_experiments is not None and experiment not in only_experiments:
            continue
        group = []
        for instrument in INSTRUMENTS.get(experiment, []):
            if instrument in COVERAGE_EXCLUDE or (only_instruments is not None and instrument not in only_instruments):
                continue
            (group if (experiment, instrument) in have else skipped).append(instrument)
        rows.append((experiment, [i for i in INSTRUMENTS.get(experiment, []) if i in group]))
    rows = [(e, insts) for e, insts in rows if insts]
    if not rows:
        print("      [SKIP] Coverage timeline: no instruments with data")
        return 0
    if skipped:
        print(f"      [INFO] Coverage timeline: no data in any leg for {', '.join(skipped)}")

    # y positions: a header row per experiment, then its instruments
    layout, y = [], 0
    for experiment, insts in rows:
        layout.append(("header", experiment, None, y))
        y += 1
        for inst in insts:
            layout.append(("row", experiment, inst, y))
            y += 1
    n_rows = y

    x0, x1 = legs["start"].min(), legs["end"].max()
    with mpl.rc_context(_RC):
        height = 0.26 * n_rows + 1.7
        fig, ax = plt.subplots(figsize=(10, height))
        fig.subplots_adjust(left=0.17, right=0.98, top=1 - 0.7 / height, bottom=0.95 / height)

        for kind, experiment, inst, ypos in layout:
            if kind == "header":
                ax.text(-0.005, ypos, experiment, transform=ax.get_yaxis_transform(), ha="right", va="center",
                        fontsize=8, fontweight="bold", color=INK)
                continue
            for leg in legs.itertuples():
                span = (mdates.date2num(leg.start), mdates.date2num(leg.end) - mdates.date2num(leg.start))
                ax.broken_barh([span], (ypos - 0.34, 0.68), facecolors=TRACK, linewidth=0)
                if (leg.leg, experiment, inst) not in ok_legs:
                    continue
                covered = _covered_intervals(leg.start, leg.end, gap_index.get((leg.leg, experiment, inst), []))
                bars = [(mdates.date2num(a), mdates.date2num(b) - mdates.date2num(a)) for a, b in covered]
                if bars:
                    ax.broken_barh(bars, (ypos - 0.34, 0.68), facecolors=BLUE, linewidth=0)

        tick_rows = [(ypos, inst) for kind, _e, inst, ypos in layout if kind == "row"]
        ax.set_yticks([p for p, _ in tick_rows])
        ax.set_yticklabels([n for _, n in tick_rows], fontsize=8)
        ax.set_ylim(n_rows - 0.4, -0.6)
        ax.set_xlim(mdates.date2num(x0), mdates.date2num(x1))
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_visible(False)
        ax.tick_params(axis="y", length=0)
        ax.xaxis_date()
        add_month_year_axis(ax)
        add_leg_markers(ax, legs)

        fig.text(0.01, 1 - 0.12 / height, "Data coverage by instrument", ha="left", va="top", fontsize=11, color=INK)
        overrides = "".join(f", {t:g} min for {inst}" for inst, t in GAP_THRESHOLD_MINUTES_BY_INSTRUMENT.items())
        fig.text(
            0.01, 1 - 0.38 / height,
            f"Gaps are stretches longer than {GAP_THRESHOLD_MINUTES:g} min{overrides} in the raw combined files; "
            "grey is a leg window without data",
            ha="left", va="top", fontsize=7.5, color=MUTED,
        )
        handles = [Patch(facecolor=BLUE, label="Data"), Patch(facecolor=TRACK, label="No data / gap")]
        fig.legend(handles=handles, loc="upper right", ncol=2, fontsize=8, bbox_to_anchor=(0.98, 1 - 0.08 / height))

    fig_root = expedition_figures_root(cruise)
    process_fig(fig, name="data_coverage", base_name=f"{cruise}_LEGSALL",
                outdir_pdf=fig_root / "PDF", outdir_png=fig_root / "PNG")
    print(f"      [OK] Plotted expedition data coverage ({sum(len(i) for _, i in rows)} instruments)")
    return 1


def _leg_source_seconds(path, window):
    """Seconds per source of one leg's merged-GPS CSV (only distinct seconds are parsed)."""
    d = pd.read_csv(path, usecols=["time", "source"], dtype=str)
    d["sec"] = d["time"].str.slice(0, 19)               # 'YYYY-MM-DD HH:MM:SS'
    d = d.drop_duplicates(["sec", "source"])
    t = to_utc(d["sec"]).dt.tz_localize(None)
    keep = t.notna() & (t >= window[0]) & (t <= window[1])
    return _source_seconds(t[keep], d.loc[keep, "source"])


def plot_gps_sources_expedition(cruise=None, only_experiments=None, only_instruments=None):
    """
    Per leg: how much of the leg window each position source covers, and the
    hours contributed by the gap-fill sources. Reads every leg's merged-GPS
    CSV (time + source only). Returns the number of figures written (0 or 1).
    """
    if only_experiments is not None and GPS_INSTRUMENT[0] not in only_experiments:
        return 0
    if only_instruments is not None and GPS_INSTRUMENT[1] not in only_instruments:
        return 0
    cruise = resolve_cruise(cruise)
    legs = load_leg_bounds(cruise)
    if legs is None or legs.empty:
        return 0

    records = []
    for leg in legs.itertuples():
        path = input_folders_processer(str(leg.leg), *GPS_INSTRUMENT, cruise=cruise)[6]
        if not os.path.exists(path):
            continue
        print(f"      [GPS] LEG {leg.leg}: reading {os.path.basename(path)}")
        try:
            secs = _leg_source_seconds(path, (leg.start, leg.end))
        except Exception as exc:
            print(f"      [WARN] LEG {leg.leg}: {exc}")
            continue
        records.append((leg.leg, (leg.end - leg.start).total_seconds(), secs))
    if not records:
        print("      [SKIP] GPS source summary: no merged GPS files found")
        return 0

    leg_nums = [str(r[0]) for r in records]
    x = np.arange(len(records))
    pct = {s: np.array([r[2][s] / r[1] * 100.0 for r in records]) for s in GPS_SOURCES}
    none_pct = np.clip(100.0 - sum(pct.values()), 0, None)

    with mpl.rc_context(_RC):
        fig, (ax_a, ax_b) = plt.subplots(2, 1, figsize=(10, 6.6), sharex=True, gridspec_kw=dict(height_ratios=[1.3, 1]))

        bottom = np.zeros(len(records))
        for s in GPS_SOURCES:
            ax_a.bar(x, pct[s], bottom=bottom, color=GPS_COLORS[s], edgecolor="white", linewidth=0.8, width=0.75)
            bottom += pct[s]
        ax_a.bar(x, none_pct, bottom=bottom, color="white", edgecolor=NEUTRAL, hatch="///", linewidth=0.6, width=0.75)
        ax_a.set_ylabel("% of the leg window\n(1-second bins)")
        ax_a.set_ylim(0, 100)
        ax_a.set_title("1-second bins with a position fix, by source", loc="left", color=INK)
        handles = [Patch(facecolor=GPS_COLORS[s], edgecolor="white", label=GPS_LABELS[s]) for s in GPS_SOURCES]
        handles.append(Patch(facecolor="white", edgecolor=NEUTRAL, hatch="///", label="No fix"))
        ax_a.legend(handles=handles, ncol=5, loc="lower center", bbox_to_anchor=(0.5, 1.08), fontsize=8)
        _style_axes(ax_a)

        bottom = np.zeros(len(records))
        for s in GPS_SOURCES[1:]:
            hours = np.array([r[2][s] / 3600.0 for r in records])
            ax_b.bar(x, hours, bottom=bottom, color=GPS_COLORS[s], edgecolor="white", linewidth=0.8, width=0.75)
            bottom += hours
        ax_b.set_ylabel("Hours of 1-second bins\nfilled from other sources")
        ax_b.set_title("Gap-fill contribution", loc="left", color=INK)
        ax_b.set_xticks(x)
        ax_b.set_xticklabels(leg_nums, fontsize=8)
        ax_b.set_xlabel("Leg")
        _style_axes(ax_b)
        fig.suptitle(f"GPS position sources - {cruise}", x=0.01, ha="left", fontsize=11, color=INK)
        fig.tight_layout(rect=[0, 0, 1, 0.97])

    fig_root = expedition_figures_root(cruise)
    process_fig(fig, name="gps_sources", base_name=f"{cruise}_LEGSALL_{GPS_INSTRUMENT[0]}_{GPS_INSTRUMENT[1]}",
                outdir_pdf=fig_root / "PDF", outdir_png=fig_root / "PNG")
    print(f"      [OK] Plotted expedition GPS source summary ({len(records)} legs)")
    return 1


def run_expedition_diagnostics(cruise=None, only_experiments=None, only_instruments=None) -> int:
    """All expedition-wide diagnostic figures; a failure in one doesn't stop the other."""
    n = 0
    for name, func in (("data coverage", plot_coverage_timeline), ("GPS sources", plot_gps_sources_expedition)):
        try:
            n += func(cruise, only_experiments=only_experiments, only_instruments=only_instruments)
        except Exception as exc:
            print(f"      [ERROR] Expedition {name} plot failed: {exc}")
    return n
