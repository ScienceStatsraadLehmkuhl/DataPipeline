"""
Seabird_CTD profile plotting driver (companion of main_process_ctd.py).

Per leg, reads the processed `_geotag_cleaned.csv` (or `_cleaned.csv` when the
leg was processed without GPS) and writes to LEG{leg}/FIGURES/{PDF,PNG}:
  {base}_profile_ST{nn}.*         one figure per station, all its casts
  {base}_profiles_all_stations.*  one figure with every station/cast of the leg
and, across all legs, combined_files/FIGURES/{PDF,PNG}:
  {cruise}_LEGSALL_OCEANOGRAPHY_Seabird_CTD_profiles_all_stations.*
                                  every cast of the expedition, one colour per station
"""
import os

import pandas as pd

from DataPipeline.globals import LEGS
from DataPipeline.input_tools import input_folders_processer
from DataPipeline.data_processing_sensors import _derive_geotag_paths
from DataPipeline.plotters_by_leg import process_fig
from DataPipeline.plotters_ctd import (
    prepare_frame, cast_table, plot_station_profile, plot_leg_profiles, plot_expedition_profiles,
)
from DataPipeline.plotters_all_legs import expedition_figures_root
from DataPipeline.main_process_ctd import EXPERIMENT, INSTRUMENT
from DataPipeline.main_globals import CRUISE, LEG, ONLY_EXPERIMENTS, ONLY_INSTRUMENTS


def run_plotting_ctd(cruise, leg, only_experiments=None, only_instruments=None):
    if only_experiments is not None and EXPERIMENT not in only_experiments:
        return
    if only_instruments is not None and INSTRUMENT not in only_instruments:
        return

    print(f"\nPLOTTING LEG {leg}: {EXPERIMENT}/{INSTRUMENT} (profiles)")
    (
        _in, _out, _exp, fig_png, fig_pdf, cleaned_csv, _combined, base_name,
    ) = input_folders_processer(leg, EXPERIMENT, INSTRUMENT, cruise=cruise)
    _geotag_csv, geotag_cleaned_csv = _derive_geotag_paths(cleaned_csv)
    src = next((p for p in (geotag_cleaned_csv, cleaned_csv) if os.path.exists(p)), None)
    if src is None:
        print(f"      [SKIP] No processed Seabird_CTD file for LEG {leg}; run main_process_ctd first")
        return

    try:
        df = prepare_frame(pd.read_csv(src, low_memory=False))
        info, skipped = cast_table(df)
        for cast in skipped:
            print(f"      [SKIP] '{cast}' is not a station cast (test cast?)")
        if info.empty:
            print("      [SKIP] No station casts found")
            return

        save = dict(base_name=base_name, outdir_pdf=fig_pdf, outdir_png=fig_png)

        for station, casts in info.groupby("station", sort=True):
            fig = plot_station_profile(df, casts, leg, station)
            if fig is not None:
                process_fig(fig, name=f"profile_ST{station:02d}", **save)
                print(f"      [OK] Plotted station {station:02d} ({len(casts)} cast(s))")

        fig = plot_leg_profiles(df, info, leg)
        if fig is not None:
            process_fig(fig, name="profiles_all_stations", **save)
            print(f"      [OK] Plotted all stations ({len(info)} cast(s))")
    except Exception as exc:
        print(f"      [ERROR] Failed plotting LEG {leg}: {INSTRUMENT}\n{exc}")


def _cast_signature(g):
    """(rows, content hash): identifies a cast independently of its file name."""
    cols = [c for c in ("pressure_dbar", "temperature_C", "conductivity_S_m") if c in g.columns]
    return len(g), int(pd.util.hash_pandas_object(g[cols], index=False).sum())


def run_expedition_plotting_ctd(cruise, only_experiments=None, only_instruments=None):
    """
    One figure with every cast of every leg overlaid, one colour per station.
    Uses the legs whose processed file already carries the `cast` column
    (i.e. processed with main_process_ctd); older-format legs are skipped.
    Returns the number of figures written (0 or 1).
    """
    if only_experiments is not None and EXPERIMENT not in only_experiments:
        return 0
    if only_instruments is not None and INSTRUMENT not in only_instruments:
        return 0

    print(f"\nPLOTTING EXPEDITION {cruise}: {EXPERIMENT}/{INSTRUMENT} (profiles)")
    frames, seen, outdated = [], {}, []
    for leg in LEGS:
        cleaned_csv = input_folders_processer(leg, EXPERIMENT, INSTRUMENT, cruise=cruise)[5]
        candidates = _derive_geotag_paths(cleaned_csv)[1], cleaned_csv
        src = next((p for p in candidates if os.path.exists(p)), None)
        if src is None:
            continue
        df = pd.read_csv(src, low_memory=False)
        if "cast" not in df.columns:
            outdated.append(leg)
            continue
        df = prepare_frame(df)
        # the same file can sit in two legs' folders (a copied cast): keep the first
        for cast, g in df.groupby("cast", sort=False):
            sig = _cast_signature(g)
            if sig in seen and seen[sig][0] != leg:
                print(f"      [SKIP] LEG {leg} '{cast}' duplicates LEG {seen[sig][0]} '{seen[sig][1]}'")
                df = df[df["cast"] != cast]
            else:
                seen.setdefault(sig, (leg, cast))
        df["cast"] = f"LEG{leg}|" + df["cast"].astype(str)
        frames.append(df)

    if outdated:
        print(f"      [WARN] Skipped legs processed before main_process_ctd (no 'cast' column): {outdated}")
    if not frames:
        print("      [SKIP] No processed Seabird_CTD legs to plot")
        return 0

    try:
        df = pd.concat(frames, ignore_index=True)
        info, skipped = cast_table(df)
        for cast in skipped:
            print(f"      [SKIP] '{cast}' is not a station cast (test cast?)")
        if info.empty:
            print("      [SKIP] No station casts found")
            return 0
        fig = plot_expedition_profiles(df, info, cruise)
        if fig is not None:
            fig_root = expedition_figures_root(cruise)
            process_fig(
                fig, name="profiles_all_stations",
                base_name=f"{cruise}_LEGSALL_{EXPERIMENT}_{INSTRUMENT}",
                outdir_pdf=fig_root / "PDF", outdir_png=fig_root / "PNG",
            )
            print(f"      [OK] Plotted expedition ({info['station'].nunique()} stations, {len(info)} cast(s))")
            return 1
    except Exception as exc:
        print(f"      [ERROR] Failed expedition plotting: {INSTRUMENT}\n{exc}")
    return 0


if __name__ == "__main__":
    legs_to_run = LEGS if LEG is None else (LEG if isinstance(LEG, (list, tuple)) else [LEG])
    for leg in legs_to_run:
        run_plotting_ctd(CRUISE, leg, ONLY_EXPERIMENTS, ONLY_INSTRUMENTS)
    run_expedition_plotting_ctd(CRUISE, ONLY_EXPERIMENTS, ONLY_INSTRUMENTS)
