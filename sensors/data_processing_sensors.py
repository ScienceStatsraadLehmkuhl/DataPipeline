"""
Set of functions to work on data
"""
import pandas as pd
import os
import numpy as np
from DataPipeline.ingest.input_tools import *
from DataPipeline.sensors.cleaning import cleaning

from DataPipeline.vocabulary import RENAME_COLUMNS, get_categorical_codes, get_db_level_columns
from DataPipeline.ingest.preprocessing import TIME_ALIAS, to_utc
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
        # A canonical name can have several raw variants (the export format
        # changed mid-cruise); it's only missing if none of them is present.
        missing = sorted({t for c, t in rename_map.items() if t not in {rename_map.get(k, k) for k in keep_raw}})
        if missing:
            print(f"      Warning: missing expected columns: {missing}")

    # 2. rename to standardized names. When several raw variants map to the
    # same name (e.g. HDT "Heading,_degrees_true" from the whole-leg export and
    # "Heading, degrees true" from the 15-min files), a plain rename would give
    # duplicate columns and one variant's values would be lost downstream, so
    # coalesce them: per row, the first non-empty variant (rename_map order).
    targets = [rename_map.get(c, c) for c in keep_raw]
    if len(set(targets)) == len(targets):
        return df.rename(columns=rename_map)

    merged = {}
    for raw, target in zip(keep_raw, targets):
        merged[target] = df[raw] if target not in merged else merged[target].combine_first(df[raw])
    return pd.DataFrame(merged, index=df.index)


def drop_duplicate_records(df, time_col="time"):
    """
    Drop rows that are exact duplicates once renamed: same time and same
    value in every column. Happens when two exports of the same instrument
    overlap (e.g. leg 21 Seapath: the whole-leg file and the 15-min files
    both cover 2025-12-03..09). The raw time-string columns (TIME_ALIAS)
    are ignored, since each export formats them differently. Rows sharing a
    time but differing in any value are kept.
    """
    subset = [c for c in df.columns if c not in TIME_ALIAS]
    if time_col not in subset:
        return df
    dup = df.duplicated(subset=subset, keep="first")
    if dup.any():
        print(f"      [DEDUP] dropped {int(dup.sum())} duplicate record(s) (same time and values)")
        df = df[~dup]
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
        gga_df[time_col] = to_utc(gga_df[time_col])
        gga_df = gga_df.sort_values(by=time_col)
    return gga_df


def add_gps_coordinates_from_df(df, gga_df, time_col="time",
                               gps_time_col="time",
                               lat_col="latitude_deg", lon_col="longitude_deg"):

    df = df.copy()
    gps = gga_df.copy()

    df[time_col] = to_utc(df[time_col])
    gps[gps_time_col] = to_utc(gps[gps_time_col])

    # gga_df may come straight from a CSV read (e.g. GPS-MERGED-SOURCES) whose
    # lat/lon columns weren't necessarily coerced to numeric beforehand; an
    # object-dtype column here would make merge_asof's output object-dtype
    # too, which then can't be written into df's float64 lat/lon columns.
    gps[lat_col] = pd.to_numeric(gps[lat_col], errors="coerce")
    gps[lon_col] = pd.to_numeric(gps[lon_col], errors="coerce")

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




def subsample(df: pd.DataFrame, freq: str, time_col: str = "time", categorical_col: str | None = None,
              db_cols=()) -> pd.DataFrame:
    """
    Resample to `freq` (e.g., '1min', '3min') averaging numeric columns.
    Non-numeric columns are dropped by default (since "averaging all other values").

    `db_cols` names sound-level columns in dB: those are averaged
    energetically, 10*log10(mean(10^(L/10))), not arithmetically.

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
    out[time_col] = to_utc(out[time_col])
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

    db_cols = [c for c in db_cols if c in data.columns]
    if db_cols:
        data = data.copy()
        data[db_cols] = np.power(10.0, data[db_cols] / 10.0)
    out = data.resample(freq).mean()
    if db_cols:
        out[db_cols] = 10.0 * np.log10(out[db_cols])
    return out.reset_index()

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
    cruise=None,                # needed by cleaning rules that read other processed files (Gill true wind)
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
            frame[time_col] = to_utc(frame[time_col])
            frame = frame.sort_values(by=time_col)
        frame = coerce_numeric_columns(frame, time_col=time_col, exclude=exclude_numeric_cols)
        return drop_duplicate_records(frame, time_col=time_col)

    # Code-valued column (if any) that subsample() must take the dominant value of, not the mean
    categorical_col = next(iter(get_categorical_codes(experiment, instrument)), None)
    # Sound-level columns that subsample() must average energetically
    db_cols = get_db_level_columns(experiment, instrument, df.columns)

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
            cruise=cruise,
        )

        # --- Step 5: Save cleaned geotagged data + subsampled versions ---
        if geotag_cleaned_csv is not None:
            update_csv(df_clean, geotag_cleaned_csv)
            out_p = Path(geotag_cleaned_csv)
            for freq in ("1min", "3min", "5min", "1h", "1D"):
                df_sub = subsample(df_clean, freq=freq, time_col=time_col, categorical_col=categorical_col, db_cols=db_cols)
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
            cruise=cruise,
        )
        if do_clean else df
    )

    # --- Step 7: Save cleaned data + subsampled versions (non-geotag path) ---
    if cleaned_csv is not None:
        update_csv(df_clean, cleaned_csv)

        out_p = Path(cleaned_csv)
        for freq in ("1min", "3min", "5min"):
            df_sub = subsample(df_clean, freq=freq, time_col=time_col, categorical_col=categorical_col, db_cols=db_cols)
            out_csv = str(out_p.with_name(out_p.stem + f"_{freq}" + out_p.suffix))
            update_csv(df_sub, out_csv)

    return df_clean

