"""
Seabird_CTD profile plots (vertical profiles: variable on x, depth on y).

Two figure types, both fed by the `cast` column main_process_ctd.py writes:

  plot_station_profile -- one station, all its casts. Layout follows
      jupyter-old_files/CTD_Plots_AllData: three columns (Physical /
      Chemical / Optical), each variable on its own coloured top axis
      stacked over a shared inverted depth axis. Casts of the same station
      differ by line style (colour already identifies the variable).
  plot_leg_profiles -- every station/cast of a leg in one figure, one panel
      per variable (a stacked-axis layout can't also colour by cast); each
      station/cast has its own colour.
  plot_expedition_profiles -- same overlay for the whole expedition (all legs),
      one colour per station (all casts of a station share it).

Station/cast come from the cast (file) name: `St<station>[-_]<cast>`, e.g.
L04_St24_3_26052025 -> station 24, cast 3. Casts without that pattern
(testDeck, testDark, ...) are not station casts and are left out.
"""
import re

import numpy as np
import pandas as pd
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from DataPipeline.ingest.preprocessing import to_utc

DEPTH_COL = "pressure_dbar"
DEPTH_LABEL = "Depth (dbar ≈ m)"

# (panel name, [(column, axis label, colour), ...]) -- colours from the notebook
GROUPS = [
    ("Physical", [
        ("temperature_C", "Temperature (°C)", "red"),
        ("conductivity_S_m", "Conductivity (S m⁻¹)", "royalblue"),
        ("salinity_PSU", "Salinity (PSU)", "darkorchid"),
    ]),
    ("Chemical", [
        ("ph", "pH", "orchid"),
        ("oxygen_ml_L", "O₂ (ml L⁻¹)", "deepskyblue"),
        ("density_sigma_t", "Density (σₜ, kg m⁻³)", "black"),
    ]),
    ("Optical", [
        ("fluorescence_mg_m3", "Fluorescence (mg m⁻³)", "seagreen"),
        ("turbidity_NTU", "Turbidity (NTU)", "peru"),
        ("par_umol_m2_s", "PAR (µmol m⁻² s⁻¹)", "black"),
        ("beam_transmission_pct", "Beam transmission (%)", "gray"),
    ]),
]

CAST_LINESTYLES = ["-", "--", ":", "-.", (0, (5, 1)), (0, (1, 1))]

# dataviz skill's categorical slots, in fixed order (light surface)
SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]

AXIS_OFFSET_PT = 42   # spacing between stacked top axes

PH_NOTE = "pH measurements are incorrect"


def _add_ph_note(ax):
    """Grey warning across a pH panel."""
    ax.text(0.5, 0.5, PH_NOTE, transform=ax.transAxes, ha="center", va="center",
            color="0.5", fontsize=13, fontweight="bold", zorder=10)


def parse_station_cast(cast):
    """(station, cast_no) from a cast/file name, or None if it isn't a station cast."""
    s = str(cast)
    m = re.search(r"St(\d+)[-_](\d+)(?=[-_.()]|$)", s, re.I)   # first match: LEG3 names carry a 2nd, older St..
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"St(\d+)$", s, re.I)                        # e.g. L002_ST1 (no cast number)
    if m:
        return int(m.group(1)), 1
    return None


def cast_table(df):
    """
    One row per station cast (chronological): cast, station, cast_no, start,
    lat, lon, max_depth, label. Returns (table, skipped_cast_names).
    """
    rows, skipped = [], []
    for cast, g in df.groupby("cast", sort=False):
        sc = parse_station_cast(cast)
        if sc is None:
            skipped.append(cast)
            continue
        lat = g["latitude_deg"].dropna() if "latitude_deg" in g else pd.Series(dtype=float)
        lon = g["longitude_deg"].dropna() if "longitude_deg" in g else pd.Series(dtype=float)
        rows.append({
            "cast": cast, "station": sc[0], "cast_no": sc[1],
            "start": g["time"].min(),
            "lat": lat.iloc[0] if len(lat) else np.nan,
            "lon": lon.iloc[0] if len(lon) else np.nan,
            "max_depth": g[DEPTH_COL].max(),
        })
    info = pd.DataFrame(rows)
    if info.empty:
        return info, skipped

    info = info.sort_values(["start", "station", "cast_no"], kind="mergesort").reset_index(drop=True)
    info["label"] = info.apply(lambda r: f"St{r.station:02d}-{r.cast_no}", axis=1)
    # the same station/cast can occur twice (e.g. a re-uploaded file): tell them apart by date
    dup = info["label"].duplicated(keep=False)
    info.loc[dup, "label"] = info[dup].apply(lambda r: f"{r.label} ({r.start:%d %b})", axis=1)
    dup = info["label"].duplicated(keep=False)
    info.loc[dup, "label"] = info[dup].apply(lambda r: f"{r.label} [{r.cast}]", axis=1)
    return info, skipped


def prepare_frame(df):
    df = df.copy()
    df["time"] = to_utc(df["time"])
    df[DEPTH_COL] = pd.to_numeric(df[DEPTH_COL], errors="coerce")
    return df.dropna(subset=[DEPTH_COL])


def _groups_with_data(df):
    """GROUPS restricted to variables that have at least one value; empty groups dropped."""
    out = []
    for name, variables in GROUPS:
        present = [v for v in variables if v[0] in df.columns and df[v[0]].notna().any()]
        if present:
            out.append((name, present))
    return out


def _cast_description(row):
    pos = "" if pd.isna(row.lat) or pd.isna(row.lon) else f" · {abs(row.lat):.2f}°{'N' if row.lat >= 0 else 'S'} {abs(row.lon):.2f}°{'E' if row.lon >= 0 else 'W'}"
    return f"{row.label} · {row.start:%Y-%m-%d %H:%M} UTC{pos} · max {row.max_depth:.0f} dbar"


def plot_station_profile(df, casts, leg, station):
    """All casts of one station. `casts`: rows of cast_table() for that station."""
    data = df[df["cast"].isin(casts["cast"])]
    groups = _groups_with_data(data)
    if not groups:
        return None
    max_depth = data[DEPTH_COL].max()

    with mpl.rc_context({"font.size": 11, "axes.linewidth": 0.8}):
        fig, axs = plt.subplots(1, len(groups), figsize=(5.2 * len(groups), 14), sharey=True, squeeze=False)
        axs = axs[0]
        for ax, (gname, variables) in zip(axs, groups):
            ax.set_ylim(max_depth, 0)
            ax.xaxis.set_visible(False)
            ax.grid(axis="y", color="0.85", lw=0.6)
            ax.set_title(gname, fontweight="bold", pad=AXIS_OFFSET_PT * len(variables) + 8)
            for k, (col, label, color) in enumerate(variables):
                tw = ax.twiny()
                tw.spines["top"].set_position(("outward", AXIS_OFFSET_PT * k))
                for side in ("bottom", "left", "right"):
                    tw.spines[side].set_visible(False)
                tw.spines["top"].set_color(color)
                for (_, row), ls in zip(casts.iterrows(), CAST_LINESTYLES * 4):
                    g = data[data["cast"] == row["cast"]]
                    tw.plot(g[col], g[DEPTH_COL], color=color, ls=ls, lw=1.3)
                tw.set_xlabel(label, color=color)
                tw.tick_params(axis="x", colors=color)
            if any(col == "ph" for col, _, _ in variables):
                _add_ph_note(tw)   # last twin is drawn on top of the others
        axs[0].set_ylabel(DEPTH_LABEL)

        handles = [Line2D([], [], color="0.15", ls=ls, lw=1.5) for ls in CAST_LINESTYLES * 4][: len(casts)]
        fig.legend(handles, [_cast_description(r) for r in casts.itertuples()],
                   loc="lower center", ncol=1, frameon=False, fontsize=10)
        fig.suptitle(f"CTD profiles: LEG {leg}, station {station:02d}", fontsize=15, y=0.985)
        fig.subplots_adjust(left=0.08, right=0.98, top=0.74, bottom=0.05 + 0.02 * len(casts), wspace=0.12)
    return fig


def cast_colors(n):
    """One colour per cast: the ordered categorical slots up to 8, then a larger palette."""
    if n <= len(SERIES_COLORS):
        return SERIES_COLORS[:n]
    if n <= 20:
        return [plt.get_cmap("tab20")(i) for i in range(n)]
    return [plt.get_cmap("turbo")(x) for x in np.linspace(0.02, 0.98, n)]


def plot_overlay_profiles(df, info, color_key, legend_title, title):
    """
    All casts of `info` overlaid, one panel per variable (one row per group).
    Casts sharing the same `info[color_key]` value share a colour; the legend
    has one entry per distinct value, in order of first appearance, and sits
    in the empty cells above the last panel of the last column (beam
    transmission) -- beside the panels if that column has no free cell.
    """
    data = df[df["cast"].isin(info["cast"])]
    groups = _groups_with_data(data)
    if not groups:
        return None
    max_depth = data[DEPTH_COL].max()
    ncols = max(len(v) for _, v in groups)   # one row per group, one column per variable

    keys = list(dict.fromkeys(info[color_key]))
    palette = dict(zip(keys, cast_colors(len(keys))))
    by_cast = {c: g for c, g in data.groupby("cast", sort=False)}
    casts = [(by_cast[r.cast], palette[getattr(r, color_key)]) for r in info.itertuples() if r.cast in by_cast]
    free_in_last_col = [i for i, (_, variables) in enumerate(groups) if len(variables) < ncols]
    legend_inside = bool(free_in_last_col) and len(groups[-1][1]) == ncols   # free cells sit above the last row's panel
    fs = 8 if len(keys) > 24 else 9

    with mpl.rc_context({"font.size": 10, "axes.linewidth": 0.8}):
        fig_w, fig_h = 4.2 * ncols + 0.8, 5 * len(groups) + 1
        fig, axs = plt.subplots(len(groups), ncols, figsize=(fig_w, fig_h), sharey=True, squeeze=False)
        for ax in axs.flat:
            ax.set_visible(False)
        for i, (gname, variables) in enumerate(groups):
            for j, (col, label, _color) in enumerate(variables):
                ax = axs[i, j]
                ax.set_visible(True)
                ax.grid(color="0.88", lw=0.6)
                for g, color in casts:
                    ax.plot(g[col], g[DEPTH_COL], color=color, lw=1.0)
                ax.set_xlabel(label)
                if col == "ph":
                    _add_ph_note(ax)
                if j == 0:
                    ax.set_ylabel(f"{gname}\n{DEPTH_LABEL}", fontweight="bold")
        axs[0, 0].set_ylim(max_depth, 0)
        fig.suptitle(title, fontsize=14)

        handles = [Line2D([], [], color=palette[k], lw=2) for k in keys]
        if legend_inside:
            fig.subplots_adjust(left=0.07, right=0.98, top=0.93, bottom=0.06, wspace=0.18, hspace=0.25)
            boxes = [axs[i, ncols - 1].get_position() for i in free_in_last_col]
            x0, x1 = min(b.x0 for b in boxes), max(b.x1 for b in boxes)
            y0, y1 = min(b.y0 for b in boxes), max(b.y1 for b in boxes)
            rows_fit = max(1, int((y1 - y0) * fig_h / (fs * 1.7 / 72)))
            fig.legend(handles, keys, title=legend_title, loc="center", bbox_to_anchor=((x0 + x1) / 2, (y0 + y1) / 2),
                       ncol=-(-len(keys) // rows_fit), frameon=False, fontsize=fs)
        else:
            n_leg_cols = 1 if len(keys) <= 24 else 2 if len(keys) <= 48 else 3 if len(keys) <= 72 else 4
            fig.set_size_inches(fig_w + 1.4 * n_leg_cols, fig_h)
            fig.subplots_adjust(left=0.07, right=0.97 - 0.085 * n_leg_cols, top=0.93, bottom=0.06, wspace=0.18, hspace=0.25)
            fig.legend(handles, keys, title=legend_title, loc="center right", ncol=n_leg_cols, frameon=False, fontsize=fs)
    return fig


def plot_leg_profiles(df, info, leg):
    """Every station/cast of the leg: one colour per cast."""
    return plot_overlay_profiles(
        df, info, "label", "Station-cast",
        f"CTD profiles: LEG {leg}, all stations and casts ({len(info)} casts)",
    )


def plot_expedition_profiles(df, info, cruise):
    """Every cast of every leg overlaid: one colour per station."""
    info = info.assign(station_label=info["station"].map(lambda n: f"St{n:02d}"))
    n_st = info["station_label"].nunique()
    return plot_overlay_profiles(
        df, info, "station_label", "Station",
        f"CTD profiles: {cruise}, all legs ({n_st} stations, {len(info)} casts)",
    )
