"""
Plotting functions using Matplotlib (publication-friendly, static).
Saves figures as BOTH PDF (vector) and PNG (raster) when savefig=True.
"""
import os
import pandas as pd
import numpy as np
from pathlib import Path
import matplotlib as mpl
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from OneOceanExpedition_DataPipeline.globals import PLOT_LABELS
from OneOceanExpedition_DataPipeline.manual_data_read import  load_leg_windows


def get_plot_label(plot_labels, experiment, instrument, key, default=None):
    if default is None:
        default = key

    if not plot_labels or not experiment or not instrument:
        return default

    return (
        plot_labels
        .get(experiment, {})
        .get(instrument, {})
        .get(key, default)
    )

def get_leg_window(leg_start_end_path: str, leg: int, *, sheet_name=0):
    legs = load_leg_windows(leg_start_end_path, sheet_name=sheet_name)
    row = legs.loc[legs["leg"] == int(leg)]
    if row.empty:
        raise KeyError(f"Leg {leg} not found in {leg_start_end_path}")
    start = row.iloc[0]["start"]
    end   = row.iloc[0]["end"]
    return start, end

def plot_property_distribution(df, column_name, bins=30):
    if column_name not in df.columns:
        raise ValueError(f"Column '{column_name}' not found in DataFrame.")

    x = pd.to_numeric(df[column_name], errors="coerce").dropna()

    fig = plt.figure(figsize=(6.5, 4.0))
    gs = fig.add_gridspec(nrows=2, ncols=1, height_ratios=[1, 4], hspace=0.05)

    ax_box = fig.add_subplot(gs[0])
    ax_hist = fig.add_subplot(gs[1], sharex=ax_box)

    ax_box.boxplot(x.values, vert=False, widths=0.7, showfliers=False)
    ax_box.set_yticks([])
    ax_box.tick_params(axis="x", labelbottom=False)

    ax_hist.hist(x.values, bins=bins, edgecolor="black", linewidth=0.6)
    ax_hist.set_title(f"Distribution of '{column_name}'")
    ax_hist.set_xlabel(column_name)
    ax_hist.set_ylabel("Count")
    ax_hist.grid(True, axis="y", alpha=0.3)

    fig.tight_layout()
    return fig, (ax_box, ax_hist)


PUB_LAYOUT = {
    # Axes rectangle in figure fraction coords:
    # [left, bottom, width, height]
    "ax_rect": [0.12, 0.20, 0.85, 0.62],

    # Fixed title position (figure coords)
    "title_xy": (0.12, 0.92),   # align with left of axes
}

PUB_MARGINS = dict(
    left=0.12,
    right=0.98,
    bottom=0.32,  # <- increase to fit rotated date labels
    top=0.86,     # <- leaves room for title
)


def plot_property_over_time_pub(
    df,
    property_column,
    time_column="time",
    freq=None,
    agg="mean",
    figsize=(6.5, 3.2),
    dpi=300,
    color="0.15",
    line_width=0.5,
    add_markers=False,
    marker_size=2.5,
    kind="line",
    title=None,
    y_label=None,
    x_label=None,
    experiment=None,
    instrument=None,
    plot_labels=None,
    max_gap="1min",

    # FIXED layout controls
    margins=None,
    title_y=0.95,
    x_tick_rotation=30,

    # x-limits from leg windows (or manual override)
    leg=None,
    leg_start_end_path=None,
    leg_sheet_name=0,
    clip_to_leg_window=True,
    xlim=None,

    # debugging
    debug=False,
):
    import matplotlib.dates as mdates  # ensure available regardless of debug


    if plot_labels is None:
        plot_labels = PLOT_LABELS

    if margins is None:
        margins = dict(left=0.12, right=0.98, bottom=0.32, top=0.86)

    def _label_for(key, default):
        try:
            return plot_labels[experiment][instrument].get(key, default)
        except Exception:
            return default

    if time_column not in df.columns or property_column not in df.columns:
        raise ValueError(f"DataFrame must contain '{time_column}' and '{property_column}' columns")

    d = df.copy()
    d[time_column] = pd.to_datetime(d[time_column], utc=True, errors="coerce").dt.tz_localize(None)
    d = d.dropna(subset=[time_column, property_column]).sort_values(time_column)

    # Resolve x-limits (xlim overrides leg window)
    start_end = None

    if xlim is not None:
        if len(xlim) != 2:
            raise ValueError("xlim must be a 2-tuple: (start, end)")
        start = pd.to_datetime(xlim[0], utc=True, errors="coerce").tz_localize(None)
        end   = pd.to_datetime(xlim[1], utc=True, errors="coerce").tz_localize(None)
        if pd.isna(start) or pd.isna(end) or (end <= start):
            raise ValueError(f"Bad xlim provided: {xlim}")
        start_end = (start, end)

    elif leg is not None:
        if leg_start_end_path is None:
            raise ValueError("If leg is provided, leg_start_end_path must also be provided.")

        legs = load_leg_windows(leg_start_end_path, sheet_name=leg_sheet_name)
        row = legs.loc[legs["leg"] == int(leg)]

        if row.empty:
            raise KeyError(f"Leg {leg} not found in leg windows file: {leg_start_end_path}")

        start = pd.to_datetime(row.iloc[0]["start"], utc=True, errors="coerce").tz_localize(None)
        end   = pd.to_datetime(row.iloc[0]["end"],   utc=True, errors="coerce").tz_localize(None)
        if pd.isna(start) or pd.isna(end) or (end <= start):
            raise ValueError(f"Bad leg window for leg={leg}: start={start}, end={end}")

        start_end = (start, end)

    if debug:
        print("DEBUG start_end:", start_end)
        if not d.empty:
            print("DEBUG d time range (pre-clip):",
                  d[time_column].min(), "->", d[time_column].max(), "n=", len(d))

    # Clip data to window BEFORE resampling/gap detection
    if clip_to_leg_window and start_end is not None and not d.empty:
        start, end = start_end
        d = d.loc[(d[time_column] >= start) & (d[time_column] <= end)]

    if debug:
        if not d.empty:
            print("DEBUG d time range (post-clip):",
                  d[time_column].min(), "->", d[time_column].max(), "n=", len(d))
        else:
            print("DEBUG d is empty after clipping")

    if clip_to_leg_window and start_end is not None and d.empty:
        raise ValueError(
            "After clipping to the leg window, no data remains. "
            "Check leg number, time ranges, and timezone consistency."
        )

    if freq is not None:
        s = getattr(d.resample(freq, on=time_column)[property_column], agg)()
        d = s.reset_index()

    resolved_x_label = x_label if x_label is not None else _label_for(time_column, "time")
    resolved_y_label = y_label if y_label is not None else _label_for(property_column, property_column)

    if kind == "line":
        max_gap = pd.Timedelta(max_gap) if not isinstance(max_gap, pd.Timedelta) else max_gap
        if not d.empty:
            gaps = d[time_column].diff() > max_gap
            d.loc[gaps, property_column] = np.nan

    with mpl.rc_context({
        "figure.dpi": dpi,
        "savefig.dpi": dpi,
        "font.size": 9,
        "axes.titlesize": 10,
        "axes.labelsize": 9,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "axes.linewidth": 0.8,
        "xtick.major.width": 0.8,
        "ytick.major.width": 0.8,
        "xtick.minor.width": 0.6,
        "ytick.minor.width": 0.6,
        "legend.frameon": False,
        "font.family": "DejaVu Sans",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    }):
        fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
        fig.subplots_adjust(**margins)

        if kind == "scatter":
            ax.scatter(d[time_column], d[property_column], s=marker_size ** 2, color=color, linewidths=0, rasterized=True)
        elif kind == "line":
            plot_kwargs = dict(color=color, linewidth=line_width, solid_capstyle="round", rasterized=True)
            if add_markers:
                plot_kwargs.update(dict(
                    marker="o",
                    markersize=marker_size,
                    markerfacecolor=color,
                    markeredgewidth=0
                ))

            ax.plot(d[time_column], d[property_column], **plot_kwargs)
        else:
            raise ValueError(f"Unknown kind '{kind}'. Use 'line' or 'scatter'.")

        # Apply x-limits from leg window/xlim
        if start_end is not None:
            ax.set_xlim(*start_end)

        if debug:
            print("DEBUG ax.get_xlim raw:", ax.get_xlim())
            print("DEBUG ax.get_xlim as dates:", [mdates.num2date(x) for x in ax.get_xlim()])

        ax.set_xlabel(resolved_x_label)
        ax.set_ylabel(resolved_y_label)

        if title is None:
            title = f"{resolved_y_label} over time"
        fig.text(margins["left"], title_y, title, ha="left", va="top", fontsize=10)

        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        ax.grid(True, axis="y", alpha=0.25, linewidth=0.6)
        ax.grid(False, axis="x")

        locator = mdates.AutoDateLocator(minticks=4, maxticks=8)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m/%Y %H:%M"))

        for lbl in ax.get_xticklabels():
            lbl.set_rotation(x_tick_rotation)
            lbl.set_ha("right")

        ax.minorticks_on()



        return fig, ax


_SOO_GUARD_COLORS = {
    "blue":   "#5B8EAC",
    "red":    "#C56E6E",
    "green":  "#6BA36B",
    "purple": "#8D71C3",
    "orange": "#D39B5F",
    "teal":   "#5FA59E",
}


def _plot_with_gaps(ax, group, time_column, ycol, color, label=None, max_gap_seconds=60):
    """
    Plot ycol vs time_column, breaking the line wherever the time gap
    exceeds max_gap_seconds, so dropouts aren't bridged by a straight line.
    """
    valid = ~(group[time_column].isna() | group[ycol].isna())
    g = group.loc[valid]

    if len(g) == 0:
        return

    gaps = g[time_column].diff().dt.total_seconds().fillna(0)
    split_points = np.where(gaps > max_gap_seconds)[0]
    segments = np.split(g.index.values, split_points)

    first_label = label
    for seg in segments:
        seg = list(seg)
        if len(seg) > 1:
            ax.plot(g.loc[seg, time_column], g.loc[seg, ycol], color=color, label=first_label)
        first_label = None


def plot_ferrybox_ctd_panel(
    df,
    time_column="time",
    experiment=None,
    instrument=None,
    plot_labels=None,
    leg=None,
    leg_start_end_path=None,
    leg_sheet_name=0,
    clip_to_leg_window=True,
    max_gap_seconds=60,
    figsize=(15, 20),
):
    """
    6-panel diagnostic figure for the Ferrybox_CTD (SooGuard) sensor package:
    Pressure, Temperature, Oxygen, Conductivity & Salinity, Density & Sound
    Speed, and Chlorophyll/Phycoerythrin & Turbidity, spanning the full leg.

    Runs ONLY when experiment == "OCEANOGRAPHY" and instrument == "Ferrybox_CTD".
    """
    # Gate: only run for this experiment/instrument combination
    if experiment != "OCEANOGRAPHY" or instrument != "Ferrybox_CTD":
        return None

    if plot_labels is None:
        plot_labels = PLOT_LABELS

    def _label_for(key, default):
        try:
            return plot_labels[experiment][instrument].get(key, default)
        except Exception:
            return default

    if time_column not in df.columns:
        raise ValueError(f"DataFrame must contain '{time_column}' column")

    d = df.copy()
    d[time_column] = pd.to_datetime(d[time_column], utc=True, errors="coerce").dt.tz_localize(None)
    d = d.dropna(subset=[time_column]).sort_values(time_column)

    start_end = None
    if leg is not None:
        if leg_start_end_path is None:
            raise ValueError("If leg is provided, leg_start_end_path must also be provided.")

        start, end = get_leg_window(leg_start_end_path, leg, sheet_name=leg_sheet_name)
        start = pd.to_datetime(start, utc=True, errors="coerce").tz_localize(None)
        end = pd.to_datetime(end, utc=True, errors="coerce").tz_localize(None)
        if pd.isna(start) or pd.isna(end) or (end <= start):
            raise ValueError(f"Bad leg window for leg={leg}: start={start}, end={end}")
        start_end = (start, end)

    if clip_to_leg_window and start_end is not None and not d.empty:
        start, end = start_end
        d = d.loc[(d[time_column] >= start) & (d[time_column] <= end)]

    if clip_to_leg_window and start_end is not None and d.empty:
        raise ValueError(
            "After clipping to the leg window, no data remains. "
            "Check leg number, time ranges, and timezone consistency."
        )

    fig, axes = plt.subplots(nrows=6, ncols=1, figsize=figsize, sharex=True)
    fig.suptitle(f"{instrument} - LEG{leg}", fontsize=16, fontweight="bold", y=0.98)

    # Subplot 1: Pressure
    ax = axes[0]
    if "ts_pressure" in d.columns:
        label = _label_for("ts_pressure", "Pressure")
        _plot_with_gaps(ax, d, time_column, "ts_pressure", _SOO_GUARD_COLORS["blue"],
                         label=label, max_gap_seconds=max_gap_seconds)
    ax.set_title("Pressure")
    ax.legend(loc="upper left")

    # Subplot 2: Temperature
    ax = axes[1]
    temp_cols = [
        ("ts_temperature_C", _SOO_GUARD_COLORS["blue"]),
        ("CS_temperature_C", _SOO_GUARD_COLORS["green"]),
        ("o2_sensor_temperature_C", _SOO_GUARD_COLORS["teal"]),
    ]
    for col, color in temp_cols:
        if col in d.columns:
            _plot_with_gaps(ax, d, time_column, col, color,
                             label=_label_for(col, col), max_gap_seconds=max_gap_seconds)
    ax.set_title("Temperature")
    ax.legend(loc="upper left")

    # Subplot 3: O2 concentration & Air saturation
    ax = axes[2]
    ax2 = None
    if "o2_concentration" in d.columns:
        label = _label_for("o2_concentration", "O2 concentration")
        _plot_with_gaps(ax, d, time_column, "o2_concentration", _SOO_GUARD_COLORS["blue"],
                         label=label, max_gap_seconds=max_gap_seconds)
        ax.set_ylabel(label)
    if "o2_air_saturation_pct" in d.columns:
        ax2 = ax.twinx()
        label2 = _label_for("o2_air_saturation_pct", "O2 air saturation")
        _plot_with_gaps(ax2, d, time_column, "o2_air_saturation_pct", _SOO_GUARD_COLORS["red"],
                         label=label2, max_gap_seconds=max_gap_seconds)
        ax2.set_ylabel(label2)
    ax.set_title("Oxygen")
    ax.legend(loc="upper left")
    if ax2:
        ax2.legend(loc="upper right")

    # Subplot 4: Conductivity & Salinity
    ax = axes[3]
    ax2 = None
    if "CS_conductivity" in d.columns:
        label = _label_for("CS_conductivity", "Conductivity")
        _plot_with_gaps(ax, d, time_column, "CS_conductivity", _SOO_GUARD_COLORS["teal"],
                         label=label, max_gap_seconds=max_gap_seconds)
        ax.set_ylabel(label)
    if "CS_salinity_psu" in d.columns:
        ax2 = ax.twinx()
        label2 = _label_for("CS_salinity_psu", "Salinity")
        _plot_with_gaps(ax2, d, time_column, "CS_salinity_psu", _SOO_GUARD_COLORS["orange"],
                         label=label2, max_gap_seconds=max_gap_seconds)
        ax2.set_ylabel(label2)
    ax.set_title("Conductivity & Salinity")
    ax.legend(loc="upper left")
    if ax2:
        ax2.legend(loc="upper right")

    # Subplot 5: Density & Sound Speed
    ax = axes[4]
    ax2 = None
    if "CS_density" in d.columns:
        label = _label_for("CS_density", "Density")
        _plot_with_gaps(ax, d, time_column, "CS_density", _SOO_GUARD_COLORS["green"],
                         label=label, max_gap_seconds=max_gap_seconds)
        ax.set_ylabel(label)
    if "CS_sound_speed_ms" in d.columns:
        ax2 = ax.twinx()
        label2 = _label_for("CS_sound_speed_ms", "Sound Speed")
        _plot_with_gaps(ax2, d, time_column, "CS_sound_speed_ms", _SOO_GUARD_COLORS["purple"],
                         label=label2, max_gap_seconds=max_gap_seconds)
        ax2.set_ylabel(label2)
    ax.set_title("Density & Sound Speed")
    ax.legend(loc="upper left")
    if ax2:
        ax2.legend(loc="upper right")

    # Subplot 6: Chlorophyll, Phycoerythrin & Turbidity
    ax = axes[5]
    ax2 = None
    chl_cols = [
        ("trilux_chlorophyll", _SOO_GUARD_COLORS["blue"]),
        ("trilux_phycoerythrin", _SOO_GUARD_COLORS["green"]),
    ]
    for col, color in chl_cols:
        if col in d.columns:
            _plot_with_gaps(ax, d, time_column, col, color,
                             label=_label_for(col, col), max_gap_seconds=max_gap_seconds)
    if "trilux_turbidity" in d.columns:
        ax2 = ax.twinx()
        label2 = _label_for("trilux_turbidity", "Turbidity")
        _plot_with_gaps(ax2, d, time_column, "trilux_turbidity", _SOO_GUARD_COLORS["red"],
                         label=label2, max_gap_seconds=max_gap_seconds)
        ax2.set_ylabel(label2)
    ax.set_title("Chlorophyll, Phycoerythrin & Turbidity")
    ax.legend(loc="upper left")
    if ax2:
        ax2.legend(loc="upper right")

    # X-axis formatting (shared bottom axis)
    if start_end is not None:
        axes[-1].set_xlim(*start_end)

    locator = mdates.AutoDateLocator(minticks=4, maxticks=8)
    axes[-1].xaxis.set_major_locator(locator)
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%d/%m/%Y %H:%M"))
    for lbl in axes[-1].get_xticklabels():
        lbl.set_rotation(30)
        lbl.set_ha("right")
    axes[-1].set_xlabel(_label_for(time_column, "Date and time (UTC)"))

    fig.tight_layout(rect=[0, 0, 1, 0.96])

    return fig


def process_fig(fig, name, base_name, outdir_pdf="figures/pdf", outdir_png="figures/png", dpi=300):
    """
    Save a Matplotlib figure.

    Saves BOTH:
      - outdir/{name}.pdf
      - outdir/{name}.png
    """
    os.makedirs(outdir_pdf, exist_ok=True)
    os.makedirs(outdir_png, exist_ok=True)
    pdf_path = os.path.join(outdir_pdf, f"{base_name}_{name}.pdf")
    png_path = os.path.join(outdir_png, f"{base_name}_{name}.png")

    fig.savefig(pdf_path)
    fig.savefig(png_path, dpi=dpi)

    plt.close(fig)


def plot_all_reports(
    df, variable, plot_types, base_name,
    experiment=None,
    instrument=None,
    plot_labels=None,
    outdir_pdf="figures/pdf",
    outdir_png="figures/png",

    # Leg window controls passed through
    leg=None,
    leg_start_end_path=None,
    leg_sheet_name=0,
    clip_to_leg_window=True,
    debug=False,
):
    last_fig = None

    for plot_type in plot_types:
        if plot_type == "time":
            fig, ax = plot_property_over_time_pub(
                df,
                property_column=variable,
                experiment=experiment,
                instrument=instrument,
                plot_labels=plot_labels,
                x_label=None,
                y_label=None,
                title=None,
                kind="line",

                # pass through:
                leg=leg,
                leg_start_end_path=leg_start_end_path,
                leg_sheet_name=leg_sheet_name,
                clip_to_leg_window=clip_to_leg_window,
                debug=debug,
            )
        elif plot_type == "time_pts":
            fig, ax = plot_property_over_time_pub(
                df,
                property_column=variable,
                experiment=experiment,
                instrument=instrument,
                plot_labels=plot_labels,
                x_label=None,
                y_label=None,
                title=None,
                kind="scatter",
                marker_size=0.5,

                # pass through:
                leg=leg,
                leg_start_end_path=leg_start_end_path,
                leg_sheet_name=leg_sheet_name,
                clip_to_leg_window=clip_to_leg_window,
                debug=debug,
            )
        elif plot_type == "distribution":
            fig, axes = plot_property_distribution(
                df,
                column_name=variable,
                instrument=instrument,
                plot_labels=plot_labels,
            )
        else:
            raise ValueError(f"Unknown plot_type '{plot_type}'. Use 'time', 'time_pts', or 'distribution'.")

        last_fig = fig

        process_fig(
            fig,
            name=plot_type,
            base_name=base_name,
            outdir_pdf=outdir_pdf,
            outdir_png=outdir_png,
        )
    return last_fig