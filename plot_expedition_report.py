"""
Expedition-length time series plots, built from the combined 5-min files
that combine_dataset_new.py produces (one CSV per experiment/instrument,
spanning all legs). Figures are saved under combined_files/figures/{pdf,png}.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd

from DataPipeline.globals import EXPERIMENTS, INSTRUMENTS, PLOT_LABELS, get_variables
from DataPipeline.main_globals import ONLY_EXPERIMENTS, ONLY_INSTRUMENTS, ONLY_VARIABLES
from DataPipeline.combine_dataset_new import combined_output_folder, combined_output_root, resolve_cruise
from DataPipeline.plotters_reports import plot_property_over_time_pub, process_fig

EXPEDITION_INTERVAL = "5min"
# Data points are 5 min apart; break the line only on gaps well beyond that
# (e.g. in-port time between legs) rather than on every normal sample step.
EXPEDITION_MAX_GAP = "15min"
EXPEDITION_FIGSIZE = (10, 3.5)


def expedition_figures_root(cruise: str | None = None) -> Path:
    return combined_output_root(cruise) / "FIGURES"


def combined_csv_path(experiment: str, instrument: str, interval: str = EXPEDITION_INTERVAL, cruise: str | None = None) -> Path:
    return combined_output_folder(interval, cruise=cruise) / f"{experiment}_{instrument}_{interval}_COMBINED.csv"


def plot_expedition_report(
    cruise: str | None = None,
    only_experiments: list[str] | None = ONLY_EXPERIMENTS,
    only_instruments: list[str] | None = ONLY_INSTRUMENTS,
    only_variables: list[str] | None = ONLY_VARIABLES,
    interval: str = EXPEDITION_INTERVAL,
    max_gap: str = EXPEDITION_MAX_GAP,
) -> int:
    fig_root = expedition_figures_root(cruise)
    outdir_pdf = fig_root / "PDF"
    outdir_png = fig_root / "PNG"
    selected_cruise = resolve_cruise(cruise)

    experiments = EXPERIMENTS
    if only_experiments is not None:
        experiments = [e for e in experiments if e in only_experiments]

    n_written = 0
    for experiment in experiments:
        instruments = INSTRUMENTS.get(experiment, [])
        if only_instruments is not None:
            instruments = [i for i in instruments if i in only_instruments]

        for instrument in instruments:
            csv_path = combined_csv_path(experiment, instrument, interval=interval, cruise=cruise)
            if not csv_path.exists():
                print(f"      [SKIP] No combined {interval} file for {experiment}/{instrument}: {csv_path.name}")
                continue

            try:
                df = pd.read_csv(csv_path)
            except Exception as e:
                print(f"      [WARN] Failed reading {csv_path}: {e}")
                continue

            variables = get_variables(experiment, instrument)
            if only_variables is not None:
                variables = [v for v in variables if v in only_variables]

            for variable in variables:
                if variable not in df.columns:
                    continue

                for plot_type, kind, extra_kwargs in (
                    ("time", "line", {}),
                    ("time_pts", "scatter", {"marker_size": 0.5}),
                ):
                    try:
                        fig, ax = plot_property_over_time_pub(
                            df,
                            property_column=variable,
                            experiment=experiment,
                            instrument=instrument,
                            plot_labels=PLOT_LABELS,
                            kind=kind,
                            figsize=EXPEDITION_FIGSIZE,
                            max_gap=max_gap,
                            clip_to_leg_window=False,
                            **extra_kwargs,
                        )
                    except ValueError as e:
                        print(f"      [SKIP] {experiment}/{instrument}/{variable} ({plot_type}): {e}")
                        continue

                    fig.text(0.98, 0.95, "(5 min average)", ha="right", va="top", fontsize=7, color="0.4")

                    process_fig(
                        fig,
                        name=plot_type,
                        base_name=f"{selected_cruise}_LEGSALL_{experiment}_{instrument}_{variable}",
                        outdir_pdf=outdir_pdf,
                        outdir_png=outdir_png,
                    )
                    n_written += 1
                    print(f"      [OK] Plotted expedition-length {experiment}/{instrument}/{variable} ({plot_type})")

    print(f"\nWrote {n_written} expedition figure(s) to: {fig_root}")
    return n_written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot expedition-length time series from combined interval files.")
    parser.add_argument("--cruise", default=None, help="Cruise folder name under processed_data. Defaults to main_globals.CRUISE.")
    parser.add_argument("--interval", default=EXPEDITION_INTERVAL, help="Combined interval to plot from (must already exist under combined_files/).")
    parser.add_argument("--max-gap", default=EXPEDITION_MAX_GAP, help="Break the line when the time gap exceeds this (pandas offset string).")
    parser.add_argument("--only-experiments", nargs="+", default=ONLY_EXPERIMENTS, help="Filter to specific experiment names.")
    parser.add_argument("--only-instruments", nargs="+", default=ONLY_INSTRUMENTS, help="Filter to specific instrument names.")
    parser.add_argument("--only-variables", nargs="+", default=ONLY_VARIABLES, help="Filter to specific variable names.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    start = time.time()
    plot_expedition_report(
        cruise=args.cruise,
        only_experiments=args.only_experiments,
        only_instruments=args.only_instruments,
        only_variables=args.only_variables,
        interval=args.interval,
        max_gap=args.max_gap,
    )
    print(f"Elapsed: {time.time() - start:.1f}s")
