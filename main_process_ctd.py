"""
Seabird_CTD processing.

Seabird casts are vertical profiles (one position, many depths), not a
moving-platform time series, so they don't go through the generic
run_processing loop in main_process_sensors.py. Same stages, profile-safe:

  1. combined raw CSV  -- {base}.csv, built by input_tools.ensure_combined_csv
                          exactly as for every other instrument.
  2. geotag            -- {base}_geotag.csv: position looked up from the leg's
                          GPS-MERGED-SOURCES table at each cast's start time,
                          so every scan of a cast carries the cast position.
  3. rename/whitelist, coerce numerics, cleaning() -> {base}_geotag_cleaned.csv
                          (or {base}_cleaned.csv when no GPS table exists yet).

No _1min/_3min/_5min/_1h/_1D subsampled files: averaging over time is
meaningless for a profile. Row order is kept exactly as recorded (scan order
inside each cast), and a `cast` column (source .cnv file stem) is added to
group by.

Timestamps: the SBE header carries one time per cast (cast start), not one
per scan, so `time` is constant within a cast.
"""
import os
from pathlib import Path

import pandas as pd

from DataPipeline.globals import RENAME_COLUMNS, PREFERRED_TIME_COLUMN
from DataPipeline.input_tools import (
    input_folders_processer,
    ensure_combined_csv,
    load_combined_csv,
    update_csv,
    RELEVANT_INPUT_EXTS,
    _has_relevant_inputs,
    _has_output_csvs,
    _stale_raw_files,
    _process_raw_files,
)
from DataPipeline.preprocessing import from_csvs_to_csv, to_utc
from DataPipeline.data_processing_sensors import (
    keep_and_rename,
    coerce_numeric_columns,
    add_gps_coordinates_from_df,
    _derive_geotag_paths,
    _stale_relative_to,
)
from DataPipeline.cleaning_Ferrybox import cleaning
from DataPipeline.manual_data_read import get_logsheet_paths
from DataPipeline.main_process_sensors import _load_existing_gps
from DataPipeline.main_globals import CRUISE, LEG, ONLY_EXPERIMENTS, ONLY_INSTRUMENTS
from DataPipeline.globals import LEGS

EXPERIMENT = "OCEANOGRAPHY"
INSTRUMENT = "Seabird_CTD"

SOURCE_COL = "Source_File"   # added by fileformatconversion.convert_cnv_to_csv
CAST_COL = "cast"
TEXT_COLS = [CAST_COL, "ship_name", "cruise"]   # kept as text, not coerced to numeric


def _ensure_ctd_combined(input_folder, output_folder, exp_folder, output_file):
    """
    Like ensure_combined_csv, but converting raw files one at a time and
    tolerating the odd unreadable one: aborted casts leave a header-only
    .cnv ("No data section") that never yields a CSV, so it would look stale
    on every run and fail the whole leg if left to ensure_combined_csv.

    Also rebuilds, once, a combined file (and its per-file CSVs) written
    before convert_cnv_to_csv recorded `Source_File`: per-file staleness is
    mtime-based, so those CSVs would otherwise never gain the column.
    """
    preferred = PREFERRED_TIME_COLUMN.get(INSTRUMENT)
    combined_path = os.path.join(exp_folder, output_file)
    if not _has_relevant_inputs(input_folder):
        return ensure_combined_csv(input_folder, output_folder, exp_folder, output_file, preferred_time_col=preferred)

    legacy = os.path.exists(combined_path) and SOURCE_COL not in pd.read_csv(combined_path, nrows=0).columns
    if legacy:
        print(f"      [CTD] {os.path.basename(combined_path)} predates '{SOURCE_COL}'; reconverting raw files")
        files = [f for f in os.listdir(input_folder) if f.lower().endswith(RELEVANT_INPUT_EXTS)]
    else:
        files = _stale_raw_files(input_folder, output_folder, RELEVANT_INPUT_EXTS)

    os.makedirs(output_folder, exist_ok=True)
    converted = 0
    for f in files:
        try:
            _process_raw_files(input_folder, output_folder, [f])
            converted += 1
        except Exception as exc:
            print(f"      [WARN] Skipping unreadable raw file {f}: {exc}")

    if legacy or converted or not os.path.exists(combined_path):
        if not _has_output_csvs(output_folder):
            raise FileNotFoundError(f"No readable Seabird_CTD files to combine in {input_folder}")
        os.makedirs(exp_folder, exist_ok=True)
        from_csvs_to_csv(output_folder, combined_path, preferred_time_col=preferred)
    return combined_path


def add_cast_column(df):
    """
    Label every row with the cast (source .cnv file stem) it came from.
    Falls back to the SBE header FileName for rows without a Source_File
    (older combined files / non-.cnv sources), then to "unknown".
    """
    cast = pd.Series(pd.NA, index=df.index, dtype="object")
    if SOURCE_COL in df.columns:
        cast = df[SOURCE_COL].map(lambda s: Path(str(s)).stem if pd.notna(s) else pd.NA)
    if "FileName" in df.columns:
        # header FileName is a Windows path (C:\Seabird\CTDDATA\x.hex); split manually
        header = df["FileName"].map(lambda s: str(s).replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0] if pd.notna(s) else pd.NA)
        cast = cast.fillna(header)
    df = df.copy()
    df[CAST_COL] = cast.fillna("unknown")
    return df


def _geotag(df, gga_df, geotag_csv, raw_source_path, gps_source_path):
    """
    Geotag the combined raw frame (original column names) against the GPS
    table and cache it as `_geotag.csv`; reuse that file only while it's
    newer than both the raw combined CSV and the GPS table, and carries
    every column of `df`.
    """
    stale = (
        _stale_relative_to(geotag_csv, raw_source_path)
        or _stale_relative_to(geotag_csv, gps_source_path)
    )
    if not stale and os.path.exists(geotag_csv):
        geo_cols = set(pd.read_csv(geotag_csv, nrows=0).columns)
        stale = not set(df.columns) <= geo_cols

    if not stale:
        df_geo = pd.read_csv(geotag_csv, low_memory=False)
        return df_geo.loc[:, ~df_geo.columns.str.contains("^Unnamed")]

    df_geo = add_gps_coordinates_from_df(df, gga_df, time_col="time")
    update_csv(df_geo, geotag_csv)
    return df_geo


def _warn_cast_issues(df):
    """Report casts without a position and casts that are exact duplicates."""
    if "latitude_deg" in df.columns:
        pos = df.groupby(CAST_COL, sort=False)["latitude_deg"].apply(lambda s: s.notna().any())
        missing = pos.index[~pos].tolist()
        if missing:
            print(f"      [WARN] {len(missing)}/{len(pos)} cast(s) got no GPS position: {missing}")

    sig_cols = [c for c in ("pressure_dbar", "temperature_C", "conductivity_S_m") if c in df.columns]
    if sig_cols:
        sig = df.groupby(CAST_COL, sort=False)[sig_cols].apply(
            lambda g: (len(g), int(pd.util.hash_pandas_object(g, index=False).sum()))
        )
        for _key, casts in sig.groupby(sig).groups.items():
            if len(casts) > 1:
                print(f"      [WARN] identical cast data in {len(casts)} files: {list(casts)}")


def process_ctd_leg(cruise, leg, gga_df, gps_source_path, leg_start_end_path, sooguard_path, update_flag=True):
    (
        input_folder, output_folder, exp_folder, _png, _pdf, cleaned_output_file, output_file, _base,
    ) = input_folders_processer(leg, EXPERIMENT, INSTRUMENT, cruise=cruise)
    combined_path = os.path.join(exp_folder, output_file)

    if not _has_relevant_inputs(input_folder) and not os.path.exists(combined_path):
        print(f"      [SKIP] No Seabird_CTD data for LEG {leg}")
        return None

    # --- 1. combined raw CSV (kept as is) ---
    combined_path = _ensure_ctd_combined(input_folder, output_folder, exp_folder, output_file)
    if not update_flag:
        return None

    df = add_cast_column(load_combined_csv(combined_path))

    # --- 2. geotag ---
    geotag_csv, geotag_cleaned_csv = _derive_geotag_paths(cleaned_output_file)
    if gga_df is not None:
        df = _geotag(df, gga_df, geotag_csv, combined_path, gps_source_path)
        out_csv, extra_keep = geotag_cleaned_csv, (CAST_COL, "latitude_deg", "longitude_deg")
    else:
        out_csv, extra_keep = cleaned_output_file, (CAST_COL,)

    # --- 3. rename/whitelist, parse time, coerce numerics ---
    df = keep_and_rename(df, RENAME_COLUMNS[EXPERIMENT][INSTRUMENT], extra_keep=extra_keep)
    df["time"] = to_utc(df["time"])
    # Stable sort: casts can share a start time, and the default quicksort
    # would interleave their scans (scrambling every profile).
    df = df.sort_values("time", kind="mergesort")
    df = coerce_numeric_columns(df, time_col="time", exclude=TEXT_COLS)

    # --- 4. cleaning (Seabird_CTD has no rules today; kept as the hook) ---
    df = cleaning(
        df,
        time_col="time",
        leg=int(leg),
        legs_path=leg_start_end_path,
        experiment=EXPERIMENT,
        instrument=INSTRUMENT,
        sooguard_path=sooguard_path,
    )

    _warn_cast_issues(df)
    update_csv(df, out_csv)
    print(f"      [CTD] {df[CAST_COL].nunique()} cast(s), {len(df)} rows -> {os.path.basename(out_csv)}")
    return df


def run_processing_ctd(
    cruise,
    leg,
    update_flag=True,
    only_experiments=None,
    only_instruments=None,
):
    if cruise is None or leg is None:
        raise ValueError("run_processing_ctd requires cruise and leg")
    if only_experiments is not None and EXPERIMENT not in only_experiments:
        return
    if only_instruments is not None and INSTRUMENT not in only_instruments:
        return

    leg_start_end_path, sooguard_log_path = get_logsheet_paths(cruise)

    print(f"\nPROCESSING LEG {leg}: {EXPERIMENT}/{INSTRUMENT} (profiles)")
    gga_df, gps_source_path = _load_existing_gps(cruise, leg)
    try:
        process_ctd_leg(cruise, leg, gga_df, gps_source_path, leg_start_end_path, sooguard_log_path, update_flag)
        print(f"      [OK] Processed LEG {leg}: {INSTRUMENT}")
    except Exception as exc:
        print(f"      [ERROR] Failed processing LEG {leg}: {INSTRUMENT}\n{exc}")


if __name__ == "__main__":
    legs_to_run = LEGS if LEG is None else (LEG if isinstance(LEG, (list, tuple)) else [LEG])
    for leg in legs_to_run:
        run_processing_ctd(
            cruise=CRUISE,
            leg=leg,
            only_experiments=ONLY_EXPERIMENTS,
            only_instruments=ONLY_INSTRUMENTS,
        )
