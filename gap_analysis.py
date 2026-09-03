from __future__ import annotations

import argparse
import itertools
import sys
import time
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from DataPipeline.globals import EXPERIMENTS, INSTRUMENTS, LEGS
from DataPipeline.main_globals import CRUISE, LEG
from DataPipeline.manual_data_read import get_logsheet_paths, load_leg_windows

TIMERS = {"exists_check": 0.0, "csv_read": 0.0, "parse_and_gaps": 0.0}

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))



GAP_COLUMNS = [
    "cruise",
    "leg",
    "experiment",
    "instrument",
    "file_name",
    "gap_type",
    "previous_time",
    "current_time",
    "gap_minutes",
    "gap_hours",
]


def resolve_time_column(columns: Iterable[str]) -> str:
    """Return the time column to use for a file, given its column names."""
    columns = list(columns)
    if "time" in columns:
        return "time"

    lower_map = {str(col).lower(): col for col in columns}
    for candidate in ("timestamp", "timestamp_utc", "datetime", "time_utc", "date_time"):
        if candidate in lower_map:
            return lower_map[candidate]

    for col in columns:
        if "time" in str(col).lower():
            return col

    raise ValueError(f"No time-like column found among columns: {columns}")


def iter_target_files(cruise: str, legs: Iterable[str] | None = None) -> Iterable[dict]:
    """Yield every expected LEG{leg}_{experiment}_{instrument}.csv combo, whether or not it exists.

    Missing files are yielded too (with exists=False) so callers can report
    "No data" for them instead of silently skipping them.
    """
    base_dir = Path(
        f"/run/user/1000/gvfs/smb-share:server=sl-nas.local,share=processed_data/{cruise}"
    )

    if legs is None:
        legs = LEGS

    for leg in legs:
        for experiment in EXPERIMENTS:
            for instrument in INSTRUMENTS.get(experiment, []):
                file_path = base_dir / f"LEG{leg}" / experiment / f"{cruise}_LEG{leg}_{experiment}_{instrument}.csv"
                t0 = time.perf_counter()
                exists = file_path.exists()
                TIMERS["exists_check"] += time.perf_counter() - t0
                yield {
                    "cruise": cruise,
                    "leg": leg,
                    "experiment": experiment,
                    "instrument": instrument,
                    "file_path": file_path,
                    "exists": exists,
                }


def _cache_path_for(file_path: Path, cache_dir: Path) -> Path:
    """Build a local cache filename that changes if the source file changes."""
    stat = file_path.stat()
    key = f"{file_path.stem}_{stat.st_size}_{int(stat.st_mtime)}"
    return cache_dir / f"{key}.parquet"


def _read_time_column(file_path: Path, cache_dir: Path | None, time_format: str | None) -> pd.Series:
    """Read + parse just the time column, using a local cache when possible.

    On a network share, re-reading a multi-GB CSV on every run is the
    expensive part. If cache_dir is given, the parsed datetime column is
    saved locally the first time and reused on later runs, as long as the
    source file's size/mtime haven't changed.
    """
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = _cache_path_for(file_path, cache_dir)
        if cache_path.exists():
            t0 = time.perf_counter()
            cached = pd.read_parquet(cache_path)["__time_dt"]
            TIMERS["csv_read"] += time.perf_counter() - t0
            return cached

    t0 = time.perf_counter()
    # Only read the header first to find the time column, so we don't have
    # to load every column of a potentially huge file just to discard them.
    header = pd.read_csv(file_path, nrows=0)
    time_col = resolve_time_column(header.columns)

    # Read only the time column, as a string, so pandas doesn't waste time
    # trying to infer dtypes for columns we don't need.
    # NOTE: usecols reduces parsing/CPU work, but for a CSV over a network
    # share the full file still has to be streamed byte-by-byte to find
    # row boundaries, so this does NOT reduce network transfer time.
    try:
        # The pyarrow engine is substantially faster than the default C
        # engine for multi-million-row CSVs. Falls back automatically if
        # pyarrow isn't installed.
        df = pd.read_csv(file_path, usecols=[time_col], dtype={time_col: str}, engine="pyarrow")
    except (ImportError, ValueError):
        df = pd.read_csv(file_path, usecols=[time_col], dtype={time_col: str})
    TIMERS["csv_read"] += time.perf_counter() - t0

    if df.empty:
        return pd.Series(dtype="datetime64[ns, UTC]")

    parsed = pd.to_datetime(df[time_col], errors="coerce", utc=True, format=time_format).dropna()

    if cache_dir is not None:
        cache_path = _cache_path_for(file_path, cache_dir)
        parsed.rename("__time_dt").to_frame().to_parquet(cache_path)

    return parsed



def analyze_gaps_for_file(
    file_path: Path,
    leg: str,
    experiment: str,
    instrument: str,
    threshold_minutes: float = 1.0,
    leg_window: pd.Series | None = None,
    time_format: str | None = None,
    cache_dir: Path | None = None,
) -> tuple[pd.DataFrame, str]:
    """Return (gap rows, status) for one file.

    Only gaps occurring within the leg window are reported.
    """
    empty = pd.DataFrame(columns=GAP_COLUMNS)

    parsed = _read_time_column(file_path, cache_dir, time_format)

    if parsed.empty:
        return empty, "no_data"

    # Data is expected to already be chronological.
    if not parsed.is_monotonic_increasing:
        print(f"  ! {file_path.name}: time column is NOT sorted, sorting now (check source data)")
        parsed = parsed.sort_values()

    parsed = parsed.reset_index(drop=True)

    threshold_delta = pd.Timedelta(minutes=threshold_minutes)

    # ------------------------------------------------------------------
    # Restrict analysis to the leg window
    # ------------------------------------------------------------------
    if leg_window is not None:
        leg_start = leg_window.get("start")
        leg_end = leg_window.get("end")

        if pd.notna(leg_start):
            parsed = parsed[parsed >= leg_start]

        if pd.notna(leg_end):
            parsed = parsed[parsed <= leg_end]

        parsed = parsed.reset_index(drop=True)

    # No usable data inside the leg
    if parsed.empty:
        return empty, "no_data"

    t0 = time.perf_counter()

    values = parsed.values
    gap_rows = []

    # ------------------------------------------------------------------
    # Start/end gaps
    # ------------------------------------------------------------------
    if leg_window is not None:
        first_time = parsed.iloc[0]
        last_time = parsed.iloc[-1]

        if pd.notna(leg_start):
            start_gap = first_time - leg_start
            if start_gap > threshold_delta:
                gap_rows.append({
                    "cruise": "",
                    "leg": leg,
                    "experiment": experiment,
                    "instrument": instrument,
                    "file_name": file_path.name,
                    "gap_type": "start",
                    "previous_time": leg_start,
                    "current_time": first_time,
                    "gap_minutes": round(start_gap.total_seconds() / 60.0, 3),
                    "gap_hours": round(start_gap.total_seconds() / 3600.0, 3),
                })

        if pd.notna(leg_end):
            end_gap = leg_end - last_time
            if end_gap > threshold_delta:
                gap_rows.append({
                    "cruise": "",
                    "leg": leg,
                    "experiment": experiment,
                    "instrument": instrument,
                    "file_name": file_path.name,
                    "gap_type": "end",
                    "previous_time": last_time,
                    "current_time": leg_end,
                    "gap_minutes": round(end_gap.total_seconds() / 60.0, 3),
                    "gap_hours": round(end_gap.total_seconds() / 3600.0, 3),
                })

    # ------------------------------------------------------------------
    # Internal gaps (only within the leg window)
    # ------------------------------------------------------------------
    if len(parsed) >= 2:
        diffs = np.diff(values)
        threshold_ns = np.timedelta64(int(threshold_delta.total_seconds() * 1e9), "ns")
        gap_positions = np.flatnonzero(diffs > threshold_ns)

        if gap_positions.size:
            prev_times = values[gap_positions]
            current_times = values[gap_positions + 1]
            gap_minutes = (
                diffs[gap_positions].astype("timedelta64[ns]").astype("int64")
                / 1e9
                / 60.0
            ).round(3)
            gap_hours = (
                diffs[gap_positions].astype("timedelta64[ns]").astype("int64")
                / 1e9
                / 3600.0
            ).round(3)

            for prev_t, cur_t, gm, gh in zip(prev_times, current_times, gap_minutes, gap_hours):
                gap_rows.append({
                    "cruise": "",
                    "leg": leg,
                    "experiment": experiment,
                    "instrument": instrument,
                    "file_name": file_path.name,
                    "gap_type": "internal",
                    "previous_time": pd.Timestamp(prev_t).tz_localize("UTC"),
                    "current_time": pd.Timestamp(cur_t).tz_localize("UTC"),
                    "gap_minutes": gm,
                    "gap_hours": gh,
                })

    TIMERS["parse_and_gaps"] += time.perf_counter() - t0

    return (
        pd.DataFrame(gap_rows, columns=GAP_COLUMNS) if gap_rows else empty,
        "ok",
    )


STATS_COLUMNS = [
    "cruise",
    "leg",
    "experiment",
    "instrument",
    "status",
    "n_gaps",
    "total_gap_minutes",
    "total_gap_hours",
    "max_gap_minutes",
    "max_gap_hours",
    "mean_gap_minutes",
    "mean_gap_hours",
    "median_gap_minutes",
    "median_gap_hours",
    "min_gap_minutes",
    "min_gap_hours",
]


def build_statistics(gaps_df: pd.DataFrame, coverage_df: pd.DataFrame) -> pd.DataFrame:
    """Build one summary row per leg / experiment / instrument.

    Every combo in coverage_df gets a row, even ones with no data at all -
    those get status="No data" and blank numeric columns, so they're
    distinguishable from a combo that had data but zero gaps (status="OK", n_gaps=0).
    """
    if gaps_df.empty:
        gap_stats = pd.DataFrame({
            "cruise": pd.Series(dtype="object"),
            "leg": pd.Series(dtype="object"),
            "experiment": pd.Series(dtype="object"),
            "instrument": pd.Series(dtype="object"),
            "n_gaps": pd.Series(dtype="float64"),
            "total_gap_minutes": pd.Series(dtype="float64"),
            "max_gap_minutes": pd.Series(dtype="float64"),
            "mean_gap_minutes": pd.Series(dtype="float64"),
            "median_gap_minutes": pd.Series(dtype="float64"),
            "min_gap_minutes": pd.Series(dtype="float64"),
        })
    else:
        gap_stats = (
            gaps_df.groupby(["cruise", "leg", "experiment", "instrument"], dropna=False)["gap_minutes"]
            .agg(["count", "sum", "max", "mean", "median", "min"])
            .reset_index()
        )
        gap_stats = gap_stats.rename(columns={
            "count": "n_gaps",
            "sum": "total_gap_minutes",
            "max": "max_gap_minutes",
            "mean": "mean_gap_minutes",
            "median": "median_gap_minutes",
            "min": "min_gap_minutes",
        })
        gap_stats["total_gap_minutes"] = gap_stats["total_gap_minutes"].round(3)

    minute_to_hour_cols = {
        "total_gap_minutes": "total_gap_hours",
        "max_gap_minutes": "max_gap_hours",
        "mean_gap_minutes": "mean_gap_hours",
        "median_gap_minutes": "median_gap_hours",
        "min_gap_minutes": "min_gap_hours",
    }
    for minute_col, hour_col in minute_to_hour_cols.items():
        gap_stats[hour_col] = (gap_stats[minute_col] / 60.0).round(3)

    merged = coverage_df.merge(
        gap_stats, on=["cruise", "leg", "experiment", "instrument"], how="left"
    )

    numeric_cols = [
        "n_gaps",
        "total_gap_minutes", "total_gap_hours",
        "max_gap_minutes", "max_gap_hours",
        "mean_gap_minutes", "mean_gap_hours",
        "median_gap_minutes", "median_gap_hours",
        "min_gap_minutes", "min_gap_hours",
    ]
    ok_mask = merged["status"] == "ok"
    # ok combos with no matching gap rows genuinely had zero gaps.
    for col in ["n_gaps", "total_gap_minutes", "total_gap_hours"]:
        merged.loc[ok_mask, col] = merged.loc[ok_mask, col].fillna(0)

    # Cast to object dtype so "No data" (a string) can sit alongside numbers in the same column.
    for col in numeric_cols:
        merged[col] = merged[col].astype(object)
    merged.loc[~ok_mask, numeric_cols] = "No data"
    merged.loc[~ok_mask, "status"] = "No data"
    merged.loc[ok_mask, "status"] = "OK"

    return merged[STATS_COLUMNS]


def _strip_tz_for_excel(df: pd.DataFrame) -> pd.DataFrame:
    """Excel has no concept of timezone-aware datetimes; drop the tz (values stay UTC, just unlabeled).

    Handles both a clean tz-aware dtype column AND an `object` dtype column
    that ended up with a mix of tz-aware/tz-naive Timestamps (which
    isinstance-on-dtype alone won't catch, since the column dtype itself
    just reads as `object` in that case).
    """
    df = df.copy()
    datetime_like_cols = {"previous_time", "current_time"}
    for col in df.columns:
        if isinstance(df[col].dtype, pd.DatetimeTZDtype):
            df[col] = df[col].dt.tz_localize(None)
        elif col in datetime_like_cols and not df[col].empty:
            # Coerce to a single consistent tz-aware dtype first (treats any
            # already-naive values as UTC), then strip the tz label.
            coerced = pd.to_datetime(df[col], utc=True, errors="coerce")
            df[col] = coerced.dt.tz_localize(None)
    return df


def _no_data_gap_rows(cruise: str, stats_df: pd.DataFrame, leg_windows: pd.DataFrame) -> pd.DataFrame:
    """Build one 'No data' row per combo in stats_df whose status is 'No data'.

    Lets the gaps sheet show exactly which experiment/instrument combos had
    no usable data, even when other instruments in the same leg had plenty.

    When the leg's start/end window is known, the row's gap length is the
    full leg window (leg_end - leg_start): with no data at all, the entire
    leg is uncovered. Falls back to "No data" text if the window is missing.
    """
    if stats_df.empty:
        return pd.DataFrame(columns=GAP_COLUMNS)
    no_data = stats_df[stats_df["status"] == "No data"]
    if no_data.empty:
        return pd.DataFrame(columns=GAP_COLUMNS)

    rows = []
    for _, row in no_data.iterrows():
        leg_window = leg_windows.loc[leg_windows["leg"] == int(row["leg"])]
        leg_start = leg_window.iloc[0].get("start") if not leg_window.empty else None
        leg_end = leg_window.iloc[0].get("end") if not leg_window.empty else None

        if pd.notna(leg_start) and pd.notna(leg_end):
            gap_delta = leg_end - leg_start
            previous_time = leg_start
            current_time = leg_end
            gap_minutes = round(gap_delta.total_seconds() / 60.0, 3)
            gap_hours = round(gap_delta.total_seconds() / 3600.0, 3)
        else:
            previous_time = ""
            current_time = ""
            gap_minutes = "No data"
            gap_hours = "No data"

        rows.append({
            "cruise": cruise,
            "leg": row["leg"],
            "experiment": row["experiment"],
            "instrument": row["instrument"],
            "file_name": "",
            "gap_type": "No data",
            "previous_time": previous_time,
            "current_time": current_time,
            "gap_minutes": gap_minutes,
            "gap_hours": gap_hours,
        })
    return pd.DataFrame(rows, columns=GAP_COLUMNS)


def _write_gap_workbook(path: Path, gaps_df: pd.DataFrame, stats_df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        _strip_tz_for_excel(gaps_df).to_excel(writer, sheet_name="gaps", index=False)
        _strip_tz_for_excel(stats_df).to_excel(writer, sheet_name="statistics", index=False)


def run_gap_analysis(
    cruise: str,
    leg: str | None = None,
    threshold_minutes: float = 1.0,
    time_format: str | None = None,
    cache_dir: Path | None = None,
) -> Path:
    """Analyze all target files, write one Excel workbook per leg, and one combined workbook.

    leg: single leg to run. Omit / pass None to run all legs (same convention
    as the other run_* entry points).
    """
    cruise_dir = Path(
        f"/run/user/1000/gvfs/smb-share:server=sl-nas.local,share=processed_data/{cruise}"
    )
    combined_output_dir = cruise_dir / "combined_files"
    combined_output_dir.mkdir(parents=True, exist_ok=True)

    legs_to_run = LEGS if leg is None else [leg]

    all_gaps: list[pd.DataFrame] = []
    coverage_rows: list[dict] = []
    processed_files = 0
    found_gap_files = 0
    missing_files = 0

    leg_start_end_path, _ = get_logsheet_paths(cruise)
    leg_windows = load_leg_windows(str(leg_start_end_path))

    print(f"Gap analysis for '{cruise}': Threshold {threshold_minutes} minute(s)")


    # iter_target_files iterates legs as the outer loop, so entries for the
    # same leg are contiguous - groupby lets us write each leg's workbook
    # the moment that leg is done, instead of waiting for the whole cruise.
    for leg, leg_entries in itertools.groupby(iter_target_files(cruise, legs=legs_to_run), key=lambda e: e["leg"]):
        leg_gap_frames: list[pd.DataFrame] = []
        leg_coverage_rows: list[dict] = []

        for entry in leg_entries:
            file_path = entry["file_path"]

            if not entry["exists"]:
                missing_files += 1
                print(f"  ! missing: {file_path}")
                leg_coverage_rows.append({
                    "cruise": cruise,
                    "leg": entry["leg"],
                    "experiment": entry["experiment"],
                    "instrument": entry["instrument"],
                    "status": "no_data",
                })
                continue

            processed_files += 1
            print(f"Analyzing {file_path.name}")
            leg_window = leg_windows.loc[leg_windows["leg"] == int(entry["leg"])]
            if not leg_window.empty:
                leg_window = leg_window.iloc[0]
            else:
                leg_window = None
            gaps_df, status = analyze_gaps_for_file(
                file_path=file_path,
                leg=entry["leg"],
                experiment=entry["experiment"],
                instrument=entry["instrument"],
                threshold_minutes=threshold_minutes,
                leg_window=leg_window,
                time_format=time_format,
                cache_dir=cache_dir,
            )
            leg_coverage_rows.append({
                "cruise": cruise,
                "leg": entry["leg"],
                "experiment": entry["experiment"],
                "instrument": entry["instrument"],
                "status": status,
            })
            if not gaps_df.empty:
                found_gap_files += 1
                gaps_df["cruise"] = cruise
                leg_gap_frames.append(gaps_df)
                print(f"  -> found {len(gaps_df)} gap(s)")
            elif status == "no_data":
                print("  -> no usable data (empty file or unparseable timestamps)")
            else:
                print("  -> no gaps found")

        leg_gaps_df = pd.concat(leg_gap_frames, ignore_index=True) if leg_gap_frames else pd.DataFrame(columns=GAP_COLUMNS)
        leg_coverage_df = pd.DataFrame(leg_coverage_rows)
        leg_stats_df = build_statistics(leg_gaps_df, leg_coverage_df)

        leg_no_data_rows = _no_data_gap_rows(cruise, leg_stats_df, leg_windows)
        if not leg_no_data_rows.empty:
            leg_gaps_df = pd.concat([leg_gaps_df, leg_no_data_rows], ignore_index=True)

        leg_path = cruise_dir / f"LEG{leg}" / f"{cruise}_LEG{leg}_gap_analysis.xlsx"
        print(f"Writing leg {leg} results to {leg_path}")
        _write_gap_workbook(leg_path, leg_gaps_df, leg_stats_df)

        # Keep every leg's data around too, for the combined workbook at the end.
        if leg_gap_frames:
            all_gaps.extend(leg_gap_frames)
        coverage_rows.extend(leg_coverage_rows)

    if all_gaps:
        gaps_df = pd.concat(all_gaps, ignore_index=True)
    else:
        gaps_df = pd.DataFrame(columns=GAP_COLUMNS)

    coverage_df = pd.DataFrame(coverage_rows)
    stats_df = build_statistics(gaps_df, coverage_df)

    combined_no_data_rows = _no_data_gap_rows(cruise, stats_df, leg_windows)
    if not combined_no_data_rows.empty:
        gaps_df = pd.concat([gaps_df, combined_no_data_rows], ignore_index=True)

    # Combined workbook across all legs, in the existing combined_files/ folder.
    # This one still has to wait until every leg is done, since it needs all of them.
    output_path = combined_output_dir / f"{cruise}_gap_analysis.xlsx"
    print(f"Writing combined results to {output_path}")
    _write_gap_workbook(output_path, gaps_df, stats_df)

    print(f"Finished: processed {processed_files} file(s), {missing_files} missing, found gaps in {found_gap_files} file(s)")
    print("--- Timing breakdown ---")
    print(f"  Existence checks (network stat calls): {TIMERS['exists_check']:.2f}s")
    print(f"  CSV reads (network transfer + parse):  {TIMERS['csv_read']:.2f}s")
    print(f"  Datetime parsing + gap detection:      {TIMERS['parse_and_gaps']:.2f}s")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze time gaps in the base processed CSV files.")
    parser.add_argument("--cruise", default=CRUISE, help="Cruise folder name under processed_data")
    parser.add_argument("--leg", default=LEG, help="Single leg to run. Omit / use None in settings to run all legs.")
    parser.add_argument("--gap-threshold-minutes", type=float, default=1.0, help="Only report gaps larger than this threshold")
    parser.add_argument(
        "--time-format",
        default=None,
        help='Explicit strptime format for the time column (e.g. "%%Y-%%m-%%d %%H:%%M:%%S"). '
             "Providing this speeds up parsing significantly.",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="Local folder to cache parsed time columns in, keyed by source file size/mtime. "
             "Speeds up re-runs a lot since it avoids re-reading unchanged files over the network. "
             "Pass --no-cache to disable.",
    )
    parser.add_argument("--no-cache", action="store_true", help="Disable local caching entirely.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.no_cache:
        cache_dir = None
    elif args.cache_dir:
        cache_dir = Path(args.cache_dir)
    else:
        cache_dir = Path.home() / ".cache" / "gap_analysis" / args.cruise

    output_path = run_gap_analysis(
        args.cruise,
        threshold_minutes=args.gap_threshold_minutes,
        time_format=args.time_format,
        cache_dir=cache_dir,
    )
    print(f"Gap analysis saved to: {output_path}")


if __name__ == "__main__":
    main()