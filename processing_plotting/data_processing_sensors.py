"""
Set of functions to work on data
"""
import pandas as pd
import os
import numpy as np
from input_tools import *
from cleaning import cleaning

from globals import RENAME_COLUMNS
from preprocessing import TIME_ALIAS
import numpy as np
import pandas as pd
from pathlib import Path



def keep_and_rename(df, rename_map, warn_missing=True):
    """
    Keep only columns present in rename_map, rename them,
    and canonicalize the time column *after* renaming.
    """

    # 1. keep only known columns
    keep_raw = [c for c in rename_map if c in df.columns]
    df = df[keep_raw].copy()

    if warn_missing:
        missing = [c for c in rename_map if c not in df.columns]
        if missing:
            print(f"      Warning: missing expected columns: {missing}")

    # 2. rename to standardized names
    df = df.rename(columns=rename_map)

    return df


def _derive_geotag_paths(cleaned_csv: str) -> tuple[str, str]:
    p = Path(cleaned_csv)
    stem = p.stem
    if stem.endswith("_cleaned"):
        stem = stem[:-len("_cleaned")]
    geotag_csv = str(p.with_name(stem + "_geotag" + p.suffix))
    geotag_cleaned_csv = str(p.with_name(stem + "_geotag_cleaned" + p.suffix))
    return geotag_csv, geotag_cleaned_csv


def _try_load_unique_gga_df(cleaned_csv: str, *, time_col: str = "time") -> pd.DataFrame | None:
    """
    Looks for the unique *GGA.csv in the same folder as cleaned_csv.
    Returns None if not found; raises if multiple are found.
    """
    folder = Path(cleaned_csv).parent
    matches = sorted(folder.glob("*GGA.csv"))

    if not matches:
        return None
    if len(matches) > 1:
        raise RuntimeError(f"Expected a single *GGA.csv in {folder}, found: {matches}")

    gga_df = pd.read_csv(matches[0])
    if time_col in gga_df.columns:
        gga_df[time_col] = pd.to_datetime(gga_df[time_col], errors="coerce")
        gga_df = gga_df.sort_values(by=time_col)
    return gga_df


def add_gps_coordinates_from_df(df, gga_df, time_col="time",
                               gps_time_col="time",
                               lat_col="latitude_deg", lon_col="longitude_deg"):

    df = df.copy()
    gps = gga_df.copy()

    df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
    gps[gps_time_col] = pd.to_datetime(gps[gps_time_col], errors="coerce")

    # prep gps
    gps = gps.dropna(subset=[gps_time_col]).sort_values(gps_time_col)
    gps = gps.drop_duplicates(subset=[gps_time_col])  # helps avoid weird matches

    # only merge rows with valid time, but keep overall df length
    mask = df[time_col].notna()
    left = df.loc[mask].sort_values(time_col)

    merged = pd.merge_asof(
        left,
        gps[[gps_time_col, lat_col, lon_col]],
        left_on=time_col,
        right_on=gps_time_col,
        direction="nearest",
        tolerance=pd.Timedelta("1s"),  # strongly consider adding one
    )

    # write results back aligned by index (no length mismatch)
    df.loc[merged.index, lat_col] = merged[lat_col].to_numpy()
    df.loc[merged.index, lon_col] = merged[lon_col].to_numpy()

    return df




def subsample(df: pd.DataFrame, freq: str, time_col: str = "time") -> pd.DataFrame:
    """
    Resample to `freq` (e.g., '1min', '3min') averaging numeric columns.
    Non-numeric columns are dropped by default (since "averaging all other values").
    """
    if time_col not in df.columns:
        raise KeyError(f"Cannot resample: '{time_col}' not in columns")

    out = df.copy()
    out[time_col] = pd.to_datetime(out[time_col], errors="coerce")
    out = out.dropna(subset=[time_col])

    # Keep only numeric for mean aggregation (common expectation for averaging)
    numeric_cols = out.select_dtypes(include="number").columns.tolist()

    # If you want to keep lat/lon even if not numeric for some reason, ensure they are numeric upstream.
    resampled = (
        out.set_index(time_col)[numeric_cols]
           .resample(freq)
           .mean()
           .reset_index()
    )
    return resampled

def coerce_numeric_columns(df, time_col="time", exclude=None):
    """Force likely-numeric columns to numeric, coercing bad values to NaN.
    Skips time_col, known timestamp alias columns, any user-specified exclude
    columns, and any column already datetime-typed.
    """
    exclude = set(exclude or []) | {time_col} | set(TIME_ALIAS)

    cols = []
    for c in df.columns:
        if c in exclude:
            continue
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            continue  # already datetime, leave it alone
        cols.append(c)

    if cols:
        df[cols] = df[cols].apply(pd.to_numeric, errors="coerce")
    return df

def _stale_relative_to(output_path, source_path):
    """
    True if output_path doesn't exist yet, or source_path is newer than it
    (meaning the raw/combined data changed since output_path was built).
    False if source_path is missing/unknown — we can't verify freshness,
    so we don't force an unnecessary rebuild.
    """
    if output_path is None or not os.path.exists(output_path):
        return True
    if source_path is None or not os.path.exists(source_path):
        return False
    return os.path.getmtime(source_path) > os.path.getmtime(output_path)


def data_process(
    df, cleaned_csv=None,
    rename_map=None,
    *,
    experiment=None,
    instrument=None,
    time_col="time",
    gga_df=None,
    do_clean=True,
    reuse_existing_geotag=True,
    autoload_gga_csv=True,
    leg=None,
    legs_path=None,
    sooguard_path=None,
    exclude_numeric_cols=None,  # columns to skip during numeric coercion (flags, IDs, notes, etc.)
    raw_source_path=None,       # NEW: path to the combined raw CSV `df` was loaded from
    ):

    # --- Step 1: Optionally rename/select columns using a rename map ---
    if rename_map is not None:
        if experiment is not None and instrument is not None:
            rename_map = rename_map.get(experiment, {}).get(instrument, None)
            if rename_map is None:
                raise KeyError(f"No rename map for {experiment}/{instrument}")
        df = keep_and_rename(df, rename_map)

    # --- Step 2: Parse and sort the time column ---
    if time_col in df.columns:
        df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
        df = df.sort_values(by=time_col)

    # --- Step 3: Coerce numeric dtypes early ---
    df = coerce_numeric_columns(df, time_col=time_col, exclude=exclude_numeric_cols)

    # --- Step 4: Auto-load a companion GGA (GPS) file if not provided ---
    if gga_df is None and autoload_gga_csv and cleaned_csv is not None:
        gga_df = _try_load_unique_gga_df(cleaned_csv, time_col=time_col)

    # --- Step 5: Geotagging branch ---
    if gga_df is not None and not (experiment == "NAVIGATION" and instrument == "GGA"):

        geotag_csv = geotag_cleaned_csv = None
        if cleaned_csv is not None:
            geotag_csv, geotag_cleaned_csv = _derive_geotag_paths(cleaned_csv)

        # NEW: force a full rebuild if the raw/combined source is newer
        # than the existing geotag file — new data means the reused file
        # would otherwise silently miss it.
        force_reprocess = _stale_relative_to(geotag_csv, raw_source_path)

        # --- Step 5a: Reuse a previously computed geotag file, only if fresh ---
        if reuse_existing_geotag and not force_reprocess and geotag_csv and os.path.exists(geotag_csv):
            df_geo = pd.read_csv(geotag_csv)
            if time_col in df_geo.columns:
                df_geo[time_col] = pd.to_datetime(df_geo[time_col], errors="coerce")
                df_geo = df_geo.sort_values(by=time_col)

            # re-coerce after CSV round-trip, since read_csv re-infers dtypes from scratch
            df_geo = coerce_numeric_columns(df_geo, time_col=time_col, exclude=exclude_numeric_cols)
        else:
            # --- Step 5b: recompute (either no existing file, reuse disabled,
            # or the raw data changed since the last run) ---
            df_geo = add_gps_coordinates_from_df(df, gga_df, time_col=time_col)
            if geotag_csv:
                update_csv(df_geo, geotag_csv)

        # --- Step 5c: Clean the geotagged data ---
        df_clean = cleaning(
            df_geo,
            time_col=time_col,
            leg=int(leg) if leg is not None else None,
            legs_path=legs_path,
            experiment=experiment,
            instrument=instrument,
            sooguard_path=sooguard_path,
        )

        # --- Step 5d: Save cleaned geotagged data + subsampled versions ---
        if geotag_cleaned_csv is not None:
            update_csv(df_clean, geotag_cleaned_csv)
            out_p = Path(geotag_cleaned_csv)
            for freq in ("1min", "3min", "5min", "1h", "1D"):
                df_sub = subsample(df_clean, freq=freq, time_col=time_col)
                out_csv = str(out_p.with_name(out_p.stem + f"_{freq}" + out_p.suffix))
                update_csv(df_sub, out_csv)

        return df_clean

    # --- Step 6: Non-geotag branch ---
    df_clean = (
        cleaning(
            df,
            time_col=time_col,
            leg=int(leg) if leg is not None else None,
            legs_path=legs_path,
            experiment=experiment,
            instrument=instrument,
            sooguard_path=sooguard_path,
        )
        if do_clean else df
    )

    # --- Step 7: Save cleaned data + subsampled versions (non-geotag path) ---
    if cleaned_csv is not None:
        update_csv(df_clean, cleaned_csv)

        out_p = Path(cleaned_csv)
        for freq in ("1min", "3min", "5min"):
            df_sub = subsample(df_clean, freq=freq, time_col=time_col)
            out_csv = str(out_p.with_name(out_p.stem + f"_{freq}" + out_p.suffix))
            update_csv(df_sub, out_csv)

    return df_clean

