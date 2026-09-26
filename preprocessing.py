from pathlib import Path
import os
import pandas as pd
import numpy as np
from DataPipeline.fromzipxmltojson import read_csv
import csv
from DataPipeline.fileformatconversion import get_time_from_filename


TIME_ALIAS = ["System Date and Time","timestamp", "Timestamp", "NMEA_UTC_(Time)"]

# ---------------------------------------------------------------------------
# Canonical time. Everything is UTC. In memory a time column is always
# datetime64[ns, UTC] (to_utc); on disk it is always a string in TIME_FORMAT
# (format_time) -- fixed width, always microseconds, so a CSV never mixes
# "10:00:00+00:00" and "10:00:01.249000+00:00" rows.
#
# Never call pd.to_datetime directly on a pipeline time column: with the
# default format inference it locks onto the first row's format and turns
# every row that doesn't match into NaT (silent data loss).
# ---------------------------------------------------------------------------

# Literal "+00:00" is correct because to_utc() always yields UTC.
TIME_FORMAT = "%Y-%m-%d %H:%M:%S.%f+00:00"


# Compact all-digit timestamps (str or int), by digit count. Year-first is
# tried before day-first; they can't be confused for 4-digit years 19xx/20xx,
# since read as yyyymmdd a ddmmyyyy value has month = "19"/"20" (invalid).
_COMPACT_FORMATS = {
    8:  ("%Y%m%d",       "%d%m%Y"),
    12: ("%Y%m%d%H%M",   "%d%m%Y%H%M"),
    14: ("%Y%m%d%H%M%S", "%d%m%Y%H%M%S"),
}


def _parse_compact(s: pd.Series):
    """
    yyyymmdd[HHMM[SS]] or ddmmyyyy[HHMM[SS]] values (strings or integers) as
    UTC datetimes, or None if the column isn't of that kind (then the caller
    treats it as epoch / ISO / free text).

    Integers lose a leading zero (ddmmyyyy 01092025 -> 1092025), so 7 and 11
    digit integers are zero-padded back to 8 and 12. A 13-digit value stays
    ambiguous with epoch milliseconds and is treated as that.
    """
    nn = s.dropna()
    if nn.empty:
        return None
    numeric = pd.api.types.is_numeric_dtype(s)
    first = nn.iloc[0]
    if not numeric and not (isinstance(first, str) and first.strip().isdigit()):
        return None  # cheap exit for the common case (ISO strings)

    if numeric:
        v = pd.to_numeric(nn, errors="coerce")
        if v.isna().any() or (v < 0).any() or (v != v.round()).any():
            return None
        d = v.astype("int64").astype(str)
        n = d.str.len()
        d = d.where(n != 7, d.str.zfill(8)).where(n != 11, d.str.zfill(12))
    else:
        d = nn.astype(str).str.strip()
        if not d.str.fullmatch(r"\d+").all():
            return None

    lengths = d.str.len().unique()
    if len(lengths) != 1 or int(lengths[0]) not in _COMPACT_FORMATS:
        return None
    year_first, day_first = _COMPACT_FORMATS[int(lengths[0])]

    parsed = pd.to_datetime(d, format=year_first, errors="coerce", utc=True)
    missed = parsed.isna()
    if missed.any():
        parsed[missed] = pd.to_datetime(d[missed], format=day_first, errors="coerce", utc=True)
    return parsed.reindex(s.index)


def to_utc(values, *, dayfirst=True) -> pd.Series:
    """
    Parse timestamps in any supported format to a tz-aware UTC Series.

    - datetime64: tz-aware is converted to UTC, naive is taken to already be UTC
    - compact digits: yyyymmdd[HHMM[SS]] or ddmmyyyy[HHMM[SS]], str or int
    - numeric: epoch seconds, or milliseconds when the values are that large
    - strings: ISO 8601 with any mix of precision / offset / "Z" / no offset
      (naive strings are taken to be UTC). Anything else (dd/mm/yyyy,
      dd.mm.yyyy, "Apr 21 2025 ...") is parsed per element, and ambiguous
      day/month values are read DAY-first (`dayfirst`, European convention).
      Unparseable values become NaT.

    The index of a Series input is preserved.
    """
    s = values if isinstance(values, pd.Series) else pd.Series(values)

    if isinstance(s.dtype, pd.DatetimeTZDtype):
        return s.dt.tz_convert("UTC")
    if pd.api.types.is_datetime64_any_dtype(s):
        return s.dt.tz_localize("UTC")

    compact = _parse_compact(s)
    if compact is not None:
        return compact

    # All-digit strings that aren't a compact date are epoch values ("1756720800")
    if not pd.api.types.is_numeric_dtype(s):
        nn = s.dropna()
        if len(nn) and isinstance(nn.iloc[0], str) and nn.iloc[0].strip().isdigit():
            num = pd.to_numeric(s, errors="coerce")
            if num.notna().sum() == s.notna().sum():
                s = num

    if pd.api.types.is_numeric_dtype(s):
        vals = pd.to_numeric(s.dropna(), errors="coerce")
        med = vals.abs().median() if len(vals) else 0
        unit = "ms" if med > 1e11 else "s"
        return pd.to_datetime(s, unit=unit, utc=True, errors="coerce")

    # ISO 8601 first, element by element, so ISO strings are never subject to
    # dayfirst (a stray blank/garbage row must not push a whole ISO column
    # onto the free-text path).
    out = pd.to_datetime(s, utc=True, errors="coerce", format="ISO8601")
    left = out.isna() & s.notna() & (s.astype(str).str.strip() != "")
    if left.any():
        # _parse_compact requires the WHOLE column to share one digit length
        # (see its docstring), so a column mixing ISO rows with compact
        # ddmmyyyy/yyyymmdd rows fails that check for the column as a whole --
        # retry it on just the leftover (non-ISO) subset, which is often
        # homogeneous on its own, before falling to per-element free-text parsing.
        compact_left = _parse_compact(s[left])
        if compact_left is not None:
            out.loc[left] = compact_left
            left = out.isna() & s.notna() & (s.astype(str).str.strip() != "")
    if left.any():
        # format="mixed" parses each element on its own (default inference
        # would lock onto the first row's format instead).
        out.loc[left] = pd.to_datetime(s[left], utc=True, errors="coerce", format="mixed", dayfirst=dayfirst)
    return out


def format_time(values) -> pd.Series:
    """Timestamps of any supported format as canonical TIME_FORMAT strings (NaT -> NaN)."""
    return to_utc(values).dt.strftime(TIME_FORMAT)


def format_timestamp(ts) -> str:
    """One scalar timestamp as a canonical string ('' for NaT/None)."""
    if pd.isna(ts):
        return ""
    ts = pd.Timestamp(ts)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.strftime(TIME_FORMAT)


def add_canonical_time(df, *, utc=True, dayfirst=True, preferred_time_col=None):
    """
    Find a timestamp column and always add a canonical 'time' column,
    preserving any existing 'time' column by renaming it first.

    `preferred_time_col`, when given and present in df.columns, is used
    as-is instead of searching TIME_ALIAS -- for sources with more than one
    candidate timestamp column (e.g. Ferrybox, see
    globals.PREFERRED_TIME_COLUMN), TIME_ALIAS's generic priority order
    can't be trusted to pick the one that's actually authoritative for that
    source.
    """
    df = df.copy()

    # 1. Find timestamp source FIRST
    if preferred_time_col is not None and preferred_time_col in df.columns:
        time_col = preferred_time_col
    else:
        time_col = next((col for col in TIME_ALIAS if col in df.columns), None)
    if time_col is None:
        print("Warning: No timestamp column found. Skipping canonical time.")
        return df

    # 2. If the source column is already named 'time', preserve it
    if time_col == "time":
        new_name = "timestamp"
        if new_name in df.columns:
            i = 2
            while f"{new_name}_{i}" in df.columns:
                i += 1
            new_name = f"{new_name}_{i}"

        df = df.rename(columns={"time": new_name})
        time_col = new_name  # update source name

    # 3. Parse timestamps (any format -> UTC), 4. always add canonical time.
    # (`utc` is kept in the signature for callers but everything is UTC.)
    df["time"] = to_utc(df[time_col], dayfirst=dayfirst)

    return df


def ensure_time(df, filename, preferred_time_col=None):
    """
    Ensure the DataFrame has a canonical 'time' column.
    If no timestamp column exists, create one from the filename first.
    """
    has_known_time_source = (
        (preferred_time_col is not None and preferred_time_col in df.columns)
        or any(col in df.columns for col in TIME_ALIAS)
    )
    if not has_known_time_source:
        df = df.copy()
        df["timestamp"] = get_time_from_filename(filename, len(df))

    return add_canonical_time(df, preferred_time_col=preferred_time_col)


def from_csvs_to_csv(output_folder_name, output_file, preferred_time_col=None):
    """
    Combine multiple CSVs into one, adding canonical time where possible.
    """
    data_rows, keywords = [], []

    for filecsv in os.listdir(output_folder_name):
        if not filecsv.endswith(".csv"):
            continue

        # Read CSV as list of dicts
        data_row, file_keywords = read_csv(os.path.join(output_folder_name, filecsv))
        if not data_row:
            continue

        # Convert to DataFrame
        df = pd.DataFrame(data_row)

        # Add canonical time if possible
        try:
            df = ensure_time(df, os.path.join(output_folder_name, filecsv), preferred_time_col=preferred_time_col)

            # Sync keywords with whatever columns ensure_time/add_canonical_time produced
            for col in df.columns:
                if col not in file_keywords:
                    file_keywords.append(col)

        except ValueError as e:
            print(f"Warning: {e}")

        # Update global keywords
        for k in file_keywords:
            if k not in keywords:
                keywords.append(k)

        # Append rows
        data_rows += df.to_dict(orient="records")

    # Sort chronologically before writing, so the combined CSV is always
    # time-ordered regardless of the order files were read from disk.
    # Rows with missing/unparseable time are kept, sorted last. A plain
    # key-sort (not a DataFrame round-trip) keeps every other column's
    # value/formatting untouched.
    if data_rows and "time" in keywords:
        def _time_sort_key(row):
            t = row.get("time")
            return (pd.isna(t), t if pd.notna(t) else pd.Timestamp.min.tz_localize("UTC"))

        data_rows.sort(key=_time_sort_key)

        # Write time in the one canonical format (csv.DictWriter would
        # otherwise str() each Timestamp, giving mixed precision per row).
        for row in data_rows:
            row["time"] = format_timestamp(row.get("time"))

    # Ensure output folder exists
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    # Write combined CSV
    with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=keywords)
        writer.writeheader()
        writer.writerows(data_rows)

    print(f"      Successfully wrote {len(data_rows)} records")

