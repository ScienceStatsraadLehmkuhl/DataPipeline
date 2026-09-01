from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import pandas as pd

from OneOceanExpedition_DataPipeline.globals import EXPERIMENTS, INSTRUMENTS, LEGS
from OneOceanExpedition_DataPipeline.main_globals import CRUISE as DEFAULT_CRUISE
from OneOceanExpedition_DataPipeline.main_globals import ONLY_EXPERIMENTS, ONLY_INSTRUMENTS

# ---- Intervals to process ----
INTERVALS = ["1D", "1h", "5min", "3min", "1min"]    #add "cleaned" when it's really necessary

# ---- Paths ----
PROCESSED_DATA_ROOT = Path(
    "/run/user/1000/gvfs/smb-share:server=sl-nas.local,share=processed_data"
)

def base_path(cruise: str | None = None) -> Path:
    selected_cruise = cruise or DEFAULT_CRUISE
    return PROCESSED_DATA_ROOT / selected_cruise


def combined_output_root(cruise: str | None = None) -> Path:
    return base_path(cruise) / "combined_files"


def combined_output_folder(interval: str, cruise: str | None = None) -> Path:
    """One subfolder per interval, e.g. combined_files/5min/."""
    return combined_output_root(cruise) / interval


def exp_folder_path(leg: str, experiment: str, cruise: str | None = None) -> Path:
    return base_path(cruise) / f"LEG{leg}" / experiment


def target_end(interval: str) -> str:
    return f"_{interval}.csv"


def extract_instrument(filename: str, leg: str, experiment: str, interval: str) -> str | None:
    """
    Accept:
      LEG{leg}_{experiment}_{instrument}_..._{interval}.csv
      LEG{leg}_{experiment}_{instrument}_{interval}.csv
    Instrument is detected by matching the known instrument list for that experiment.
    """
    prefix = f"LEG{leg}_{experiment}_"
    end = target_end(interval)
    if not filename.startswith(prefix) or not filename.endswith(end):
        return None

    middle = filename[len(prefix) : -len(end)]  # starts with instrument, then maybe "_stuff"
    for inst in sorted(INSTRUMENTS.get(experiment, []), key=len, reverse=True):
        if middle == inst or middle.startswith(inst + "_"):
            return inst
    return None


def iter_files_for_experiment(experiment: str, interval: str, cruise: str | None = None) -> Iterable[Tuple[str, Path]]:
    """Yield (leg, filepath) for all files of a given interval across all legs for one experiment."""
    end = target_end(interval)
    for leg in LEGS:
        in_dir = exp_folder_path(leg, experiment, cruise=cruise)
        if not in_dir.exists():
            continue
        # Only files for this leg+experiment that end with the interval suffix
        yield from ((leg, p) for p in in_dir.glob(f"LEG{leg}_{experiment}_*{end}"))


def combine_and_sort(files_with_leg: List[Tuple[str, Path]], time_col: str = "time") -> pd.DataFrame:
    dfs = []
    for leg, f in files_with_leg:
        df = pd.read_csv(f)

        # Skip completely empty files (headers only) or zero rows
        if df.empty:
            continue

        # Drop columns that are entirely NA in this chunk (prevents the warning)
        df = df.dropna(axis=1, how="all")

        df["__leg"] = leg
        df["__source_file"] = f.name
        dfs.append(df)

    if not dfs:
        return pd.DataFrame()

    out = pd.concat(dfs, ignore_index=True)

    if time_col in out.columns:
        out["__time_dt"] = pd.to_datetime(out[time_col], errors="coerce", utc=True)
        out = out.sort_values("__time_dt", kind="mergesort").drop(columns="__time_dt")
    else:
        print(f"Warning: no '{time_col}' column found; leaving unsorted.")

    return out


def combine_one_experiment_instrument(experiment: str, instrument: str, interval: str, cruise: str | None = None) -> Path | None:
    """
    Create ONE combined file across ALL legs for a given (experiment, instrument, interval),
    using only files that end with _{interval}.csv (with any middle tokens allowed).
    """
    out_folder = combined_output_folder(interval, cruise=cruise)
    out_folder.mkdir(parents=True, exist_ok=True)

    matched: List[Tuple[str, Path]] = []
    for leg, path in iter_files_for_experiment(experiment, interval, cruise=cruise):
        inst = extract_instrument(path.name, leg, experiment, interval)
        if inst == instrument:
            matched.append((leg, path))

    if not matched:
        return None

    df = combine_and_sort(matched, time_col="time")
    if df.empty:
        return None

    out_file = out_folder / f"{experiment}_{instrument}_{interval}_COMBINED.csv"

    with open(out_file, "w", newline="") as f:
        df.to_csv(f, index=False)
    return out_file


def ensure_outdir(interval: str, cruise: str | None = None) -> None:
    out_folder = combined_output_folder(interval, cruise=cruise)
    out_folder.mkdir(parents=True, exist_ok=True)
    if not out_folder.is_dir():
        raise RuntimeError(f"Output folder is not a directory: {out_folder}")


def combine_all_experiments_instruments_for_interval(
    interval: str,
    cruise: str | None = None,
    only_experiments: List[str] | None = None,
    only_instruments: List[str] | None = None,
) -> Dict[Tuple[str, str], Path]:
    """Write ONE file per (experiment, instrument) for a given interval, across the whole cruise."""
    ensure_outdir(interval, cruise=cruise)
    outputs: Dict[Tuple[str, str], Path] = {}

    experiments = EXPERIMENTS
    if only_experiments is not None:
        experiments = [e for e in experiments if e in only_experiments]

    for experiment in experiments:
        instruments = INSTRUMENTS.get(experiment, [])
        if only_instruments is not None:
            instruments = [i for i in instruments if i in only_instruments]

        for instrument in instruments:
            print(f"[{interval}] Combining {experiment} - {instrument}")
            out = combine_one_experiment_instrument(experiment, instrument, interval, cruise=cruise)
            if out is not None:
                outputs[(experiment, instrument)] = out
    return outputs


def combine_all_intervals(
    intervals: Iterable[str] | None = None,
    cruise: str | None = None,
    only_experiments: List[str] | None = None,
    only_instruments: List[str] | None = None,
) -> Dict[str, Dict[Tuple[str, str], Path]]:
    """Run the full combine process for every interval in INTERVALS."""
    selected_intervals = list(intervals or INTERVALS)
    all_outputs: Dict[str, Dict[Tuple[str, str], Path]] = {}
    for interval in selected_intervals:
        print(f"=== Combining files: {interval} ===")
        outputs = combine_all_experiments_instruments_for_interval(
            interval,
            cruise=cruise,
            only_experiments=only_experiments,
            only_instruments=only_instruments,
        )
        all_outputs[interval] = outputs
        print(f"Wrote {len(outputs)} combined file(s) to: {combined_output_folder(interval, cruise=cruise)}")
    return all_outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Combine processed files for a cruise.")
    parser.add_argument("--cruise", default=None, help="Cruise folder name under processed_data. Defaults to main_globals.CRUISER.")
    parser.add_argument("--intervals", nargs="+", default=None, help="Intervals to process. Defaults to all supported intervals.")
    parser.add_argument("--only-experiments", nargs="+", default=ONLY_EXPERIMENTS, help="Filter to specific experiment names.")
    parser.add_argument("--only-instruments", nargs="+", default=ONLY_INSTRUMENTS, help="Filter to specific instrument names.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    start = time.time()
    all_outputs = combine_all_intervals(
        intervals=args.intervals,
        cruise=args.cruise,
        only_experiments=args.only_experiments,
        only_instruments=args.only_instruments,
    )
    total = sum(len(v) for v in all_outputs.values())
    print(f"\nDone. Wrote {total} combined file(s) across {len(all_outputs)} interval(s) "
          f"to: {combined_output_root(args.cruise)}")
    print(f"Elapsed: {time.time() - start:.1f}s")