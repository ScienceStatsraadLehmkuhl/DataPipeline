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
EK80/GPS setup.

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

A third, last-resort source -- "Bridge" -- is tried only for whatever gap
windows EK80/Ferrybox still leave uncovered (see
gap_windows_without_coverage). It comes from the ship's own bridge
navigation software, manually exported to one xlsx file per covered leg
under NAVIGATION/Bridge (sibling to SEAPATH, not nested under an
instrument). Unlike every other source here, Bridge files are not raw
instrument output run through the pipeline's usual combine/clean step --
they're read directly in this module (see extract_bridge_gap_positions),
since they exist purely to patch position gaps, not to be processed as an
instrument in their own right.
"""
import os
import re
from pathlib import Path

import pandas as pd

from DataPipeline.gap_analysis import analyze_gaps_for_file
from DataPipeline.manual_data_read import load_leg_windows
from DataPipeline.input_tools import give_me_full_folder_name
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


def gps_merged_sources_path(cruise: str, leg, exp_folder_name: str) -> str:
    """Path to a leg's merged GPS product (GGA plus any EK80/Ferrybox gap-fill).

    Single source of truth for this filename -- both _apply_gga_gap_fill
    (which writes it) and run_processing (which needs the path to pass to
    every other instrument's data_process call, for geotag staleness
    checking) must agree on it exactly.
    """
    return os.path.join(exp_folder_name, f"{cruise}_LEG{leg}_NAVIGATION_GPS-MERGED-SOURCES.csv")


def load_cached_merged_positions(merged_path: str, gga_source_csv: str) -> pd.DataFrame | None:
    """
    Return the existing merged GPS product at `merged_path` if it's already
    at least as fresh as `gga_source_csv`, else None.

    `gga_source_csv` must be a staleness-protected file -- GGA's *raw
    combined* CSV (built by ensure_combined_csv, which only rewrites it
    when new raw data actually lands), not its cleaned CSV. data_process's
    cleaning step (data_processing_sensors.py) rewrites the cleaned CSV
    unconditionally on every run with no staleness check of its own, so
    comparing against that would make this look stale every time too --
    the same trap this function exists to avoid for merged_path itself.

    Gap-checking is cheap, but a full gap-fill (EK80 file selection +
    conversion + Ferrybox pull) is not -- and none of it is worth redoing
    when GGA's own data hasn't changed since the merged product was last
    built. This also keeps merged_path's own mtime meaningful: it's only
    ever rewritten when something actually changed, rather than on every
    run, which is what a downstream "is the GPS file newer than X" check
    would need to make sense in the first place.
    """
    if not os.path.exists(merged_path) or not os.path.exists(gga_source_csv):
        return None
    if os.path.getmtime(merged_path) < os.path.getmtime(gga_source_csv):
        return None
    return pd.read_csv(merged_path, parse_dates=["time"])


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


def gap_windows_without_coverage(
    gap_windows: list[tuple],
    *position_frames: pd.DataFrame | None,
) -> list[tuple]:
    """
    Subset of gap_windows that none of position_frames put a fix inside.

    Used to gate the Bridge (last-resort) source to only the gaps
    EK80/Ferrybox actually left open, rather than always consulting it
    alongside them -- see module docstring.
    """
    non_empty = [df["time"] for df in position_frames if df is not None and not df.empty]
    filled_times = pd.concat(non_empty, ignore_index=True) if non_empty else pd.Series(dtype="object")

    return [
        (start, end) for start, end in gap_windows
        if filled_times.empty or not ((filled_times >= start) & (filled_times <= end)).any()
    ]


def find_bridge_input_folder(cruise: str, leg) -> str | None:
    """
    Path to this leg's ship-navigation "Bridge" xlsx folder.

    Sits at NAVIGATION/Bridge on the raw geomatics share -- a sibling of
    SEAPATH, not nested under an instrument name like GGA/HDT/etc. -- so
    built directly here with the same LEG{leg} resolution
    input_folders_processer uses, rather than through its SEAPATH-specific
    branch (input_tools.input_folders_processer), which doesn't fit this
    source.
    """
    leg_root = f"/run/user/1000/gvfs/smb-share:server=sl-nas.local,share=geomatics/{cruise}/LEG{leg}"
    leg_root = give_me_full_folder_name(leg_root, str(leg))
    if leg_root is None:
        return None
    return f"{leg_root}/NAVIGATION/Bridge"


BRIDGE_HEADER_SKIPROWS = 8
_DMS_RE = re.compile(r"^\s*(\d+)\s*\xb0\s*(\d+(?:\.\d+)?)\s*'\s*([NSEW])\s*$")


def _parse_dms(value) -> float:
    """
    Decimal degrees from a bridge-nav DMS string, e.g. "37\xb0 44.2464' N" or
    "025\xb0 39.7979' W". Returns NaN for anything that doesn't match (blank
    spacer rows, stray text), same as a failed numeric parse elsewhere in
    this module.
    """
    if not isinstance(value, str):
        return float("nan")
    match = _DMS_RE.match(value)
    if not match:
        return float("nan")
    degrees, minutes, hemisphere = match.groups()
    dd = float(degrees) + float(minutes) / 60.0
    return -dd if hemisphere in ("S", "W") else dd


def _read_bridge_xlsx(path: str) -> pd.DataFrame:
    """
    One bridge-nav xlsx file's Time/Lat/Lon columns, past its metadata
    header block (vessel name, from/to, total miles/avg speed) -- see
    module docstring. BRIDGE_HEADER_SKIPROWS assumes every bridge export
    shares that fixed layout; it's the one constant to adjust if a future
    export shifts it.
    """
    raw = pd.read_excel(path, skiprows=BRIDGE_HEADER_SKIPROWS)
    columns = {str(c).strip().lower(): c for c in raw.columns}
    missing = {"time", "lat", "lon"} - set(columns)
    if missing:
        print(
            f"      [GAP-FILL] Bridge file {path} missing column(s) {missing} after "
            f"skiprows={BRIDGE_HEADER_SKIPROWS} (columns found: {list(raw.columns)})"
        )
        return pd.DataFrame(columns=POSITION_COLUMNS)

    positions = pd.DataFrame({
        "time": pd.to_datetime(raw[columns["time"]], format="%d/%m/%Y %H:%M", errors="coerce"),
        "latitude_deg": raw[columns["lat"]].map(_parse_dms),
        "longitude_deg": raw[columns["lon"]].map(_parse_dms),
    })
    # Bridge nav logs carry no explicit UTC offset in the timestamp string
    # (just "08/07/2025 00:03"), unlike GGA/Ferrybox's own logging software --
    # assumed already UTC (same assumption as EK80's echopype timestamps, see
    # module docstring), just unlabeled. Localize (not convert).
    positions["time"] = positions["time"].dt.tz_localize("UTC")
    positions["source"] = "Bridge"
    return positions.dropna(subset=["time", "latitude_deg", "longitude_deg"])[POSITION_COLUMNS]


def extract_bridge_gap_positions(
    bridge_folder: str | None,
    gap_windows: list[tuple],
) -> pd.DataFrame:
    """
    GPS positions from the ship's own bridge-navigation xlsx export --
    last-resort gap-fill source, meant to be called only with whatever gap
    windows EK80/Ferrybox left uncovered (see gap_windows_without_coverage).
    Bridge files aren't run through the pipeline's usual raw-source
    processing (no CSV combine/clean step); they're a manually-supplied nav
    log read directly here (see _read_bridge_xlsx), for whichever legs the
    bridge crew happened to export one for.
    """
    empty = pd.DataFrame(columns=POSITION_COLUMNS)

    if not gap_windows:
        return empty
    if not bridge_folder or not os.path.isdir(bridge_folder):
        print(f"      [GAP-FILL] Bridge input folder not found or missing: {bridge_folder!r}")
        return empty

    xlsx_files = sorted(Path(bridge_folder).glob("*.xlsx"))
    if not xlsx_files:
        print(f"      [GAP-FILL] No .xlsx files in {bridge_folder}")
        return empty

    frames = []
    for path in xlsx_files:
        try:
            frames.append(_read_bridge_xlsx(str(path)))
        except Exception as exc:
            print(f"      [GAP-FILL] Could not read Bridge file {path}: {exc}")

    frames = [f for f in frames if not f.empty]
    if not frames:
        return empty

    positions = pd.concat(frames, ignore_index=True)
    n_before_window_filter = len(positions)
    positions = positions.loc[_in_any_window(positions["time"], gap_windows)]
    if n_before_window_filter and positions.empty:
        print(
            f"      [GAP-FILL] Found {n_before_window_filter} Bridge position row(s) but none fall "
            f"inside the gap window(s) {gap_windows} -- check Bridge file timestamps vs. gap times"
        )
    return positions.sort_values("time").reset_index(drop=True)


def merge_gga_with_gap_fill(
    gga_df: pd.DataFrame,
    ek80_positions: pd.DataFrame | None,
    ferrybox_positions: pd.DataFrame | None,
    bridge_positions: pd.DataFrame | None = None,
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
    for extra in (ek80_positions, ferrybox_positions, bridge_positions):
        if extra is not None and not extra.empty:
            extra = extra.rename(columns={"time": time_col}).copy()
            extra[time_col] = pd.to_datetime(extra[time_col], errors="coerce")
            parts.append(extra)

    merged = pd.concat(parts, ignore_index=True)
    merged = merged.dropna(subset=[time_col, "latitude_deg", "longitude_deg"])
    return merged.sort_values(time_col).reset_index(drop=True)
