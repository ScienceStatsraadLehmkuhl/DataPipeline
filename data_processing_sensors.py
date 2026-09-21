"""
Set of functions to work on data
"""
import pandas as pd
import os
import numpy as np
from DataPipeline.input_tools import *
from DataPipeline.cleaning_Ferrybox import cleaning

from DataPipeline.globals import RENAME_COLUMNS, get_categorical_codes
from DataPipeline.preprocessing import TIME_ALIAS
import numpy as np
import pandas as pd
from pathlib import Path



def keep_and_rename(df, rename_map, warn_missing=True, extra_keep=()):
    """
    Keep only columns present in rename_map, rename them,
    and canonicalize the time column *after* renaming.

    `extra_keep` lists columns that are passed through untouched even though
    they aren't in rename_map (e.g. the geotag latitude/longitude columns).
    """

    # 1. keep only known columns
    keep_raw = [c for c in rename_map if c in df.columns]
    keep_raw += [c for c in extra_keep if c in df.columns and c not in keep_raw]
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
    # A merged-GPS CSV mixes "...:30+00:00" and "...:30.249000+00:00" rows;
    # default inference locks onto the first row's format and turns every
    # row of the other kind into NaT (silently dropped below). Try ISO8601
    # first, which handles both, and only then fall back to inference.
    try:
        gps[gps_time_col] = pd.to_datetime(gps[gps_time_col], format="ISO8601")
    except (ValueError, TypeError):
        gps[gps_time_col] = pd.to_datetime(gps[gps_time_col], errors="coerce")

    # prep gps -- rows without a usable position must not take part in the
    # "nearest" match, or they would shadow a valid neighbouring fix
    gps = gps.dropna(subset=[gps_time_col, lat_col, lon_col]).sort_values(gps_time_col)
    gps = gps.drop_duplicates(subset=[gps_time_col])  # helps avoid weird matches

    # only merge rows with valid time, but keep overall df length
    mask = df[time_col].notna()
    # drop any stale position columns so the merge can't create _x/_y suffixes
    left = df.loc[mask].drop(columns=[lat_col, lon_col], errors="ignore").sort_values(time_col)
    left_index = left.index  # merge_asof resets the index; remember which df rows these were

    merged = pd.merge_asof(
        left,
        gps[[gps_time_col, lat_col, lon_col]],
        left_on=time_col,
        right_on=gps_time_col,
        direction="nearest",
        tolerance=pd.Timedelta("1s"),  # strongly consider adding one
    )

    # write results back to the original row labels (merged has a fresh 0..n-1
    # index, so using it directly misaligns whenever df isn't already sorted
    # by time with a default index)
    df[lat_col] = np.nan
    df[lon_col] = np.nan
    df.loc[left_index, lat_col] = merged[lat_col].to_numpy()
    df.loc[left_index, lon_col] = merged[lon_col].to_numpy()

    return df




def subsample(df: pd.DataFrame, freq: str, time_col: str = "time", categorical_col: str | None = None) -> pd.DataFrame:
    """
    Resample to `freq` (e.g., '1min', '3min') averaging numeric columns.
    Non-numeric columns are dropped by default (since "averaging all other values").

    `categorical_col` names a code-valued column (e.g. Lufft precipitation type)
    that can't be averaged. Per bin it takes the dominant (most frequent) code
    -- ties go to the higher code, so precipitation wins over "none" -- and the
    other columns are averaged over only the rows carrying that code, so e.g. a
    rain intensity isn't mixed with the zeros of a dry spell in the same bin.
    Bins with no valid code fall back to a plain mean of the other columns.
    """
    if time_col not in df.columns:
        raise KeyError(f"Cannot resample: '{time_col}' not in columns")

    out = df.copy()
    out[time_col] = pd.to_datetime(out[time_col], errors="coerce")
    out = out.dropna(subset=[time_col])

    # Keep only numeric for mean aggregation (common expectation for averaging)
    numeric_cols = out.select_dtypes(include="number").columns.tolist()

    # If you want to keep lat/lon even if not numeric for some reason, ensure they are numeric upstream.
    data = out.set_index(time_col)[numeric_cols]

    if categorical_col in data.columns:
        bins = data.index.floor(freq)
        codes = data[categorical_col].to_numpy()
        counts = (
            pd.DataFrame({"bin": bins, "code": codes}).dropna()
              .groupby(["bin", "code"]).size().rename("n").reset_index()
        )
        dominant = (
            counts.sort_values(["bin", "n", "code"])
                  .drop_duplicates("bin", keep="last")
                  .set_index("bin")["code"]
        )
        bin_code = dominant.reindex(bins).to_numpy()
        # Rows of a bin's dominant code, plus every row of a bin with no valid code.
        # The mean of the kept categorical values is the dominant code itself.
        keep = (codes == bin_code) | pd.isna(bin_code)
        data = data[keep]

    return data.resample(freq).mean().reset_index()

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
    gps_source_path=None,       # NEW: path to GPS-MERGED-SOURCES.csv (gga_df's own on-disk source)
    ):

    # Resolve the per-instrument rename map up front (fail early if missing).
    if rename_map is not None and experiment is not None and instrument is not None:
        rename_map = rename_map.get(experiment, {}).get(instrument, None)
        if rename_map is None:
            raise KeyError(f"No rename map for {experiment}/{instrument}")

    def _rename_and_coerce(frame, extra_keep=()):
        """Whitelist/rename columns, then parse+sort time and coerce numerics."""
        if rename_map is not None:
            frame = keep_and_rename(frame, rename_map, extra_keep=extra_keep)
        if time_col in frame.columns:
            frame[time_col] = pd.to_datetime(frame[time_col], errors="coerce")
            frame = frame.sort_values(by=time_col)
        return coerce_numeric_columns(frame, time_col=time_col, exclude=exclude_numeric_cols)

    # Code-valued column (if any) that subsample() must take the dominant value of, not the mean
    categorical_col = next(iter(get_categorical_codes(experiment, instrument)), None)

    # --- Step 1: Auto-load a companion GGA (GPS) file if not provided ---
    if gga_df is None and autoload_gga_csv and cleaned_csv is not None:
        gga_df = _try_load_unique_gga_df(cleaned_csv, time_col=time_col)

    # --- Step 2: Geotagging branch ---
    # Geotag the *first* CSV (the combined raw one, original column names,
    # before whitelisting/renaming) and save it as `_geotag.csv`. Only then
    # rename/whitelist and clean.
    if gga_df is not None and not (experiment == "NAVIGATION" and instrument == "GGA"):

        geotag_csv = geotag_cleaned_csv = None
        if cleaned_csv is not None:
            geotag_csv, geotag_cleaned_csv = _derive_geotag_paths(cleaned_csv)

        # Force a full rebuild if the raw/combined source, OR the GPS
        # source gga_df came from (GPS-MERGED-SOURCES.csv), is newer than
        # the existing geotag file -- either means the reused file would
        # otherwise silently miss new data or an updated position source.
        force_reprocess = (
            _stale_relative_to(geotag_csv, raw_source_path)
            or _stale_relative_to(geotag_csv, gps_source_path)
        )

        # A geotag file written by the older pipeline held the already
        # renamed/whitelisted columns; it can't be renamed again, so treat any
        # file that doesn't carry every column of the raw frame as stale.
        if (
            not force_reprocess and reuse_existing_geotag
            and geotag_csv and os.path.exists(geotag_csv)
        ):
            geo_cols = set(pd.read_csv(geotag_csv, nrows=0).columns)
            force_reprocess = not set(df.columns) <= geo_cols

        # --- Step 2a: Reuse a previously computed geotag file, only if fresh ---
        if reuse_existing_geotag and not force_reprocess and geotag_csv and os.path.exists(geotag_csv):
            df_geo = pd.read_csv(geotag_csv, low_memory=False)
            df_geo = df_geo.loc[:, ~df_geo.columns.str.contains("^Unnamed")]
        else:
            # --- Step 2b: recompute (no existing file, reuse disabled,
            # or the raw data / GPS source changed since the last run) ---
            df_geo = add_gps_coordinates_from_df(df, gga_df, time_col=time_col)
            if geotag_csv:
                update_csv(df_geo, geotag_csv)

        # --- Step 3: rename/whitelist (keeping lat/lon), parse time, coerce numerics ---
        df_geo = _rename_and_coerce(df_geo, extra_keep=("latitude_deg", "longitude_deg"))

        # --- Step 4: Clean the geotagged data ---
        df_clean = cleaning(
            df_geo,
            time_col=time_col,
            leg=int(leg) if leg is not None else None,
            legs_path=legs_path,
            experiment=experiment,
            instrument=instrument,
            sooguard_path=sooguard_path,
        )

        # --- Step 5: Save cleaned geotagged data + subsampled versions ---
        if geotag_cleaned_csv is not None:
            update_csv(df_clean, geotag_cleaned_csv)
            out_p = Path(geotag_cleaned_csv)
            for freq in ("1min", "3min", "5min", "1h", "1D"):
                df_sub = subsample(df_clean, freq=freq, time_col=time_col, categorical_col=categorical_col)
                out_csv = str(out_p.with_name(out_p.stem + f"_{freq}" + out_p.suffix))
                update_csv(df_sub, out_csv)

        return df_clean

    # --- Step 6: Non-geotag branch (rename -> clean) ---
    df = _rename_and_coerce(df)

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
            df_sub = subsample(df_clean, freq=freq, time_col=time_col, categorical_col=categorical_col)
            out_csv = str(out_p.with_name(out_p.stem + f"_{freq}" + out_p.suffix))
            update_csv(df_sub, out_csv)

    return df_clean

