"""
Fills GGA position gaps from EK80 and Ferrybox, which share the same MRU.

Design: GGA is the primary position source and is processed/geotagged as
before. When a GGA gap exceeds a threshold, we pull in positions already
present in the other sensors' own data streams (EK80's Platform group,
Ferrybox's own lat/lon columns) rather than treating them as a new raw
source for GGA -- see the brainstorm this module implements for why (raw
files from different instruments use different parsers/formats, so merging
them at the raw-file level was ruled out).

For EK80's Platform group, only the time3/latitude_mru1/longitude_mru1
(secondary MRU position feed) columns carry data on this cruise's
EK80/GPS setup -- time1/latitude/longitude (the primary GPS fix variables)
were confirmed all-NaN, so only the MRU feed is extracted.

For EK80 specifically, only the .raw files overlapping a gap window are
converted (by parsing the timestamp out of each filename, no need to open
files that can't matter), instead of paying for the full leg -- EK80
conversion is the expensive step and, per DataPipeline/main_globals.py,
full EK80 processing is often deferred to its own separate run anyway.
Files converted here are reused (not reconverted) whenever that full run
happens later, via the existing staleness check in
DataPipeline.input_tools_ek80_echosounder.ensure_ek80_echosounder_combined_csv.

Because EK80/Ferrybox positions are only ever pulled from inside a
confirmed GGA gap, there is no overlap with GGA's own coverage and thus no
need for a source-priority merge -- concatenation is enough, and per-target
geotagging (add_gps_coordinates_from_df's merge_asof) picks whichever
candidate is nearest in time regardless of source.
"""
import os
import re
from pathlib import Path

import pandas as pd

from DataPipeline.gap_analysis import analyze_gaps_for_file
from DataPipeline.manual_data_read import load_leg_windows
from DataPipeline.input_tools_ek80_echosounder import process_ek80_echosounder_raw_file


POSITION_COLUMNS = ["time", "latitude_deg", "longitude_deg", "source"]

ECHOSOUNDER_NC_SUBFOLDER = "EK80_echos_ncdf"
ECHOSOUNDER_CSV_SUBFOLDER = "EK80_echos_csv"

_EK80_FNAME_RE = re.compile(r"D(\d{8})-T(\d{6})")


def _in_any_window(times: pd.Series, windows: list[tuple]) -> pd.Series:
    mask = pd.Series(False, index=times.index)
    for start, end in windows:
        mask |= (times >= start) & (times <= end)
    return mask


def find_gga_gaps(
    gga_cleaned_csv: str,
    leg,
    leg_start_end_path,
    threshold_minutes: float = 5.0,
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """
    Gaps (start, current) in the already-processed GGA cleaned CSV, reusing
    DataPipeline.gap_analysis.analyze_gaps_for_file -- same gap-finding logic
    as the standalone gap-analysis report, just called directly in-memory
    (no workbook written for GGA here).

    Every gap_type (start / internal / end) counts.
    """
    leg_windows = load_leg_windows(str(leg_start_end_path))
    leg_window_rows = leg_windows.loc[leg_windows["leg"] == int(leg)]
    leg_window = leg_window_rows.iloc[0] if not leg_window_rows.empty else None

    gaps_df, _status = analyze_gaps_for_file(
        file_path=Path(gga_cleaned_csv),
        leg=str(leg),
        experiment="NAVIGATION",
        instrument="GGA",
        threshold_minutes=threshold_minutes,
        leg_window=leg_window,
    )

    if gaps_df.empty:
        return []

    return [
        (row["previous_time"], row["current_time"])
        for _, row in gaps_df.iterrows()
    ]


def _parse_ek80_filename_time(filename: str) -> pd.Timestamp | None:
    match = _EK80_FNAME_RE.search(filename)
    if not match:
        return None
    date_str, time_str = match.groups()
    return pd.to_datetime(f"{date_str}{time_str}", format="%Y%m%d%H%M%S")


def select_ek80_raw_files_for_gaps(
    input_folder_name: str | None,
    gap_windows: list[tuple],
) -> list[str]:
    """
    .raw filenames overlapping any gap window, picked from filename
    timestamps alone (no file needs to be opened to decide this).

    A file's filename only gives its start time, not its duration, so for
    each gap we include the last file starting at/before the gap (it may
    run into the gap) plus every file starting inside the gap window.
    """
    if not gap_windows or not input_folder_name or not os.path.isdir(input_folder_name):
        return []

    files_with_time = []
    for fname in os.listdir(input_folder_name):
        if not fname.lower().endswith(".raw"):
            continue
        ts = _parse_ek80_filename_time(fname)
        if ts is not None:
            files_with_time.append((ts, fname))

    if not files_with_time:
        return []

    files_with_time.sort(key=lambda item: item[0])
    times = [t for t, _ in files_with_time]

    import bisect

    selected = set()
    for gap_start, gap_end in gap_windows:
        # Filenames carry no tz marker at all (just "D<date>-T<time>"), so
        # `times` here is naive -- localize the (possibly tz-aware) gap
        # boundaries down to naive just for this comparison, independently
        # of how gap_windows is used elsewhere against tz-aware CSV data.
        gap_start = pd.Timestamp(gap_start).tz_localize(None) if pd.Timestamp(gap_start).tzinfo else gap_start
        gap_end = pd.Timestamp(gap_end).tz_localize(None) if pd.Timestamp(gap_end).tzinfo else gap_end
        before_idx = bisect.bisect_right(times, gap_start) - 1
        if before_idx >= 0:
            selected.add(files_with_time[before_idx][1])

        start_idx = bisect.bisect_left(times, gap_start)
        end_idx = bisect.bisect_right(times, gap_end)
        for _t, fname in files_with_time[start_idx:end_idx]:
            selected.add(fname)

    return sorted(selected)


def extract_ek80_gap_positions(
    input_folder_name: str | None,
    nc_folder_name: str,
    csv_folder_name: str,
    gap_windows: list[tuple],
    sonar_model: str = "EK80",
) -> pd.DataFrame:
    """
    GPS positions from EK80's own Platform group, time3 coordinate
    (latitude_mru1/longitude_mru1 -- the secondary MRU position feed; the
    primary time1/latitude/longitude fix is confirmed all-NaN on this
    cruise's EK80/GPS setup, see module docstring), for .raw files
    overlapping the given gap windows only. Converts just those files --
    see module docstring for why the rest of the leg is left untouched here.
    """
    empty = pd.DataFrame(columns=POSITION_COLUMNS)

    if not gap_windows:
        return empty
    if not input_folder_name or not os.path.isdir(input_folder_name):
        print(f"      [GAP-FILL] EK80 input folder not found or missing: {input_folder_name!r}")
        return empty

    raw_filenames = select_ek80_raw_files_for_gaps(input_folder_name, gap_windows)
    if not raw_filenames:
        print(
            f"      [GAP-FILL] No .raw files in {input_folder_name} overlap gap window(s) {gap_windows} "
            f"(by filename timestamp) -- EK80 may not have been recording during these gaps"
        )
        return empty
    print(f"      [GAP-FILL] {len(raw_filenames)} EK80 .raw file(s) selected for gap-fill: {raw_filenames}")

    os.makedirs(nc_folder_name, exist_ok=True)
    os.makedirs(csv_folder_name, exist_ok=True)

    per_file_csvs = []
    for fname in raw_filenames:
        raw_path = os.path.join(input_folder_name, fname)
        csv_path = os.path.join(csv_folder_name, f"{Path(fname).stem}.csv")

        if not os.path.exists(csv_path) or os.path.getmtime(raw_path) > os.path.getmtime(csv_path):
            print(f"      [GAP-FILL] Converting {fname} for GPS gap-fill")
            process_ek80_echosounder_raw_file(raw_path, nc_folder_name, csv_folder_name, sonar_model=sonar_model)
        per_file_csvs.append(csv_path)

    frames = [pd.read_csv(p) for p in per_file_csvs if os.path.exists(p)]
    if not frames:
        print(f"      [GAP-FILL] None of the selected EK80 per-file CSVs exist on disk: {per_file_csvs}")
        return empty

    df = pd.concat(frames, ignore_index=True)
    if "timestamp" not in df.columns or "time_source" not in df.columns:
        print(
            f"      [GAP-FILL] EK80 per-file CSVs have no 'timestamp'/'time_source' column "
            f"(columns found: {sorted(df.columns)}) -- skipping EK80 gap-fill"
        )
        return empty
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    # Unlike GGA/Ferrybox's own logging software, echopype writes this column
    # from a naive numpy datetime64 with no UTC offset in the string at all
    # (e.g. "2025-09-02 18:51:51.476097"), so it parses naive here while
    # everything else in the pipeline is tz-aware UTC. Localize (not
    # convert) since the values are already UTC, just unlabeled.
    if df["timestamp"].dt.tz is None:
        df["timestamp"] = df["timestamp"].dt.tz_localize("UTC")

    if not {"latitude_mru1", "longitude_mru1"}.issubset(df.columns):
        print(
            f"      [GAP-FILL] EK80 per-file CSVs have no latitude_mru1/longitude_mru1 columns "
            f"(columns found: {sorted(df.columns)}) -- the secondary MRU feed (time3) wasn't decoded, "
            f"or _PLATFORM_TIME_VARS names it differently for this sonar model/echopype version"
        )
        return empty

    time3_rows = df.loc[df["time_source"] == "time3"]
    positions = time3_rows[["timestamp", "latitude_mru1", "longitude_mru1"]].dropna()
    if len(time3_rows) and positions.empty:
        print(
            f"      [GAP-FILL] {len(time3_rows)} EK80 time3 row(s) found but latitude_mru1/longitude_mru1 "
            f"are all NaN (sample: {time3_rows[['latitude_mru1', 'longitude_mru1']].head(3).to_dict('records')})"
        )
        return empty
    if positions.empty:
        print(
            f"      [GAP-FILL] No EK80 time3 rows in the file(s) selected for this gap "
            f"(time_source values found: {sorted(df['time_source'].dropna().unique().tolist())}) "
            f"-- the GPS/MRU burst may not have landed in the specific .raw file(s) picked for this gap window"
        )
        return empty

    positions = positions.rename(
        columns={"timestamp": "time", "latitude_mru1": "latitude_deg", "longitude_mru1": "longitude_deg"}
    )
    positions["source"] = "EK80_gps"
    positions = positions[POSITION_COLUMNS]

    # The MRU feed (time3) logs far faster than GGA (observed: tens-hundreds of Hz,
    # vs. GGA's ~1Hz) -- downstream geotagging only ever uses the single nearest
    # candidate per target row (merge_asof, 1s tolerance), so sub-second duplicates
    # add no value while inflating gga_df/Position_merged.csv by orders of magnitude.
    # Thin to one fix per second before returning.
    n_raw = len(positions)
    positions = positions.sort_values("time")
    positions = positions.loc[~positions["time"].dt.floor("1s").duplicated(keep="first")]
    if n_raw != len(positions):
        print(f"      [GAP-FILL] Thinned EK80 MRU positions from {n_raw} to {len(positions)} row(s) (1 per second)")

    n_before_window_filter = len(positions)
    positions = positions.loc[_in_any_window(positions["time"], gap_windows)]
    if n_before_window_filter and positions.empty:
        print(
            f"      [GAP-FILL] Found {n_before_window_filter} EK80 position row(s) but none fall "
            f"inside the gap window(s) {gap_windows} -- check EK80 file timestamps vs. gap times"
        )
    return positions.sort_values("time").reset_index(drop=True)


def extract_ferrybox_positions(
    ferrybox_df: pd.DataFrame | None,
    gap_windows: list[tuple],
    time_col: str = "time",
) -> pd.DataFrame:
    """GPS positions from Ferrybox's own lat/lon columns, inside gap windows only."""
    empty = pd.DataFrame(columns=POSITION_COLUMNS)

    if not gap_windows:
        return empty
    if ferrybox_df is None or ferrybox_df.empty:
        print("      [GAP-FILL] No Ferrybox data loaded for gap-fill (empty or failed to load)")
        return empty
    if not {time_col, "latitude", "longitude"}.issubset(ferrybox_df.columns):
        print(
            f"      [GAP-FILL] Ferrybox data has no '{time_col}'/'latitude'/'longitude' columns "
            f"(columns found: {sorted(ferrybox_df.columns)}) -- skipping Ferrybox gap-fill"
        )
        return empty

    positions = ferrybox_df[[time_col, "latitude", "longitude"]].rename(
        columns={time_col: "time", "latitude": "latitude_deg", "longitude": "longitude_deg"}
    ).copy()
    positions["time"] = pd.to_datetime(positions["time"], errors="coerce")
    n_raw_rows = len(positions)
    positions = positions.dropna(subset=["time", "latitude_deg", "longitude_deg"])
    if n_raw_rows and positions.empty:
        raw_sample = ferrybox_df[[time_col, "latitude", "longitude"]].head(3).to_dict("records")
        print(
            f"      [GAP-FILL] {n_raw_rows} Ferrybox row(s) found but latitude/longitude/time "
            f"are all NaN after parsing (raw sample: {raw_sample}) -- this Ferrybox stream may not "
            f"populate its own position fields on this cruise"
        )
    positions["source"] = "Ferrybox"

    n_before_window_filter = len(positions)
    positions = positions.loc[_in_any_window(positions["time"], gap_windows)]
    if n_before_window_filter and positions.empty:
        print(
            f"      [GAP-FILL] Found {n_before_window_filter} Ferrybox position row(s) but none fall "
            f"inside the gap window(s) {gap_windows} -- check Ferrybox timestamps vs. gap times"
        )
    return positions.sort_values("time").reset_index(drop=True)


def merge_gga_with_gap_fill(
    gga_df: pd.DataFrame,
    ek80_positions: pd.DataFrame | None,
    ferrybox_positions: pd.DataFrame | None,
    time_col: str = "time",
) -> pd.DataFrame:
    """GGA positions plus whatever gap-window fill-in was found, tagged by source."""
    base = gga_df[[time_col, "latitude_deg", "longitude_deg"]].copy()
    base["source"] = "GGA"
    # Parse each part's time column independently before concatenation, same
    # as add_gps_coordinates_from_df does for its two sides -- every raw
    # source here carries an explicit UTC offset in its timestamp strings, so
    # pd.to_datetime naturally produces matching tz-aware dtypes on its own.
    # Parsing *after* concat instead (on an already-mixed column) silently
    # turns whichever side's dtype disagreed into NaT.
    base[time_col] = pd.to_datetime(base[time_col], errors="coerce")

    parts = [base]
    for extra in (ek80_positions, ferrybox_positions):
        if extra is not None and not extra.empty:
            extra = extra.rename(columns={"time": time_col}).copy()
            extra[time_col] = pd.to_datetime(extra[time_col], errors="coerce")
            parts.append(extra)

    merged = pd.concat(parts, ignore_index=True)
    merged = merged.dropna(subset=[time_col, "latitude_deg", "longitude_deg"])
    return merged.sort_values(time_col).reset_index(drop=True)
