import os

import pandas as pd

from DataPipeline.vocabulary import LEGS, EXPERIMENTS, INSTRUMENTS, PLOT_LABELS, get_variables
from DataPipeline.ingest.input_tools import input_folders_processer
from DataPipeline.plotting.plotters_by_leg import plot_all_reports, plot_ferrybox_ctd_panel, process_fig
from DataPipeline.ingest.manual_data_read import get_logsheet_paths
from DataPipeline.plotting.plotters_all_legs import plot_expedition_report
from DataPipeline.plotting.plotters_diagnostics import (
    plot_wind_rose, plot_cleaning_diagnostics, plot_gps_sources, run_expedition_diagnostics,
)
from DataPipeline.settings import CRUISE, LEG, DEFAULT_PLOT_TYPES, ONLY_EXPERIMENTS, ONLY_INSTRUMENTS, ONLY_VARIABLES
from pathlib import Path

# Plot types drawn once per processed file from the whole frame (each gated to
# the instrument it applies to; returns None for the others), as opposed to
# the per-variable types handled by plot_all_reports ("time", "time_pts",
# "distribution"). "ferrybox_colour_pannel" is handled separately below.
DIAGNOSTIC_PLOTS = {
    "wind_rose": plot_wind_rose,
    "cleaning_diagnostics": plot_cleaning_diagnostics,
    "gps_sources": plot_gps_sources,
}
SPECIAL_PLOT_TYPES = ("ferrybox_colour_pannel", *DIAGNOSTIC_PLOTS)


def load_processed_frame(leg, experiment, instrument, cruise):
    (
        _input_folder_name,
        _output_folder_name,
        exp_folder_name,
        fig_png_folder_name,
        fig_pdf_folder_name,
        cleaned_output_file,
        output_file,
        base_name,
    ) = input_folders_processer(leg, experiment, instrument, cruise=cruise)

    return {
        "exp_folder_name": exp_folder_name,
        "fig_png_folder_name": fig_png_folder_name,
        "fig_pdf_folder_name": fig_pdf_folder_name,
        "cleaned_output_file": cleaned_output_file,
        "combined_output_file": output_file,   # the leg's combined raw CSV
        "base_name": base_name,
    }


def run_plotting(
    cruise,
    leg,
    plot_types_list=None,
    only_experiments=None,
    only_instruments=None,
    only_variables=None,
):
    if cruise is None or leg is None:
        raise ValueError("run_plotting requires cruise and leg to be provided by main.py")
    if plot_types_list is None:
        plot_types_list = ["time"]

    # The special plot types are handled below (once per cleaned file, using
    # the full dataframe) rather than through plot_all_reports (which
    # dispatches per variable and doesn't recognize them).
    run_ferrybox_panel = "ferrybox_colour_pannel" in plot_types_list
    diagnostic_plot_types = [t for t in plot_types_list if t in DIAGNOSTIC_PLOTS]
    per_variable_plot_types = [t for t in plot_types_list if t not in SPECIAL_PLOT_TYPES]

    leg_start_end_path, _sooguard_log_path = get_logsheet_paths(cruise)
    legs = LEGS if leg is None else [leg]

    for current_leg in legs:
        print(f"\n{'=' * 80}")
        print(f"                 PLOTTING: {cruise} - LEG {current_leg}")
        print(f"{'=' * 80}")

        experiments = EXPERIMENTS
        if only_experiments is not None:
            experiments = [e for e in experiments if e in only_experiments]

        for experiment in experiments:
            instruments = INSTRUMENTS.get(experiment, [])
            if only_instruments is not None:
                instruments = [i for i in instruments if i in only_instruments]
            # Seabird_CTD casts are vertical profiles: main_plot_ctd.py plots them.
            instruments = [i for i in instruments if i != "Seabird_CTD"]

            if not instruments:
                print(f"      [SKIP] No instruments configured for {experiment}")
                continue

            for instrument in instruments:
                variables = get_variables(experiment, instrument)
                if only_variables is not None:
                    variables = [v for v in variables if v in only_variables]

                # ferrybox_colour_pannel/DIAGNOSTIC_PLOTS use the whole frame, not
                # `variables` -- an --only-variables filter that excludes every one
                # of this instrument's variables must not also suppress those.
                has_instrument_level_work = run_ferrybox_panel or diagnostic_plot_types
                if not variables and not has_instrument_level_work:
                    print(f"      [SKIP] No variables configured for {experiment}/{instrument}")
                    continue
                if not variables and per_variable_plot_types:
                    print(f"      [INFO] No variables configured for {experiment}/{instrument}; only running instrument-level plot type(s)")

                try:
                    paths = load_processed_frame(current_leg, experiment, instrument, cruise=cruise)
                except Exception as e:
                    print(f"      [SKIP] Could not resolve folders for {experiment}/{instrument}: {e}")
                    continue
                exp_folder = paths.get("exp_folder_name")
                if not exp_folder:
                    print(f"      [SKIP] No experiment folder found for {experiment}/{instrument}")
                    continue

                p = Path(exp_folder)
                # only consider cleaned files that correspond to this experiment/instrument
                all_cleaned = sorted(p.glob("*_cleaned.csv"))
                cleaned_files = [f for f in all_cleaned if f.stem.find(f"_{experiment}_{instrument}") != -1]
                if not cleaned_files:
                    print(f"      [SKIP] No *_cleaned.csv files for {experiment}/{instrument} in {exp_folder}")
                    continue

                for cleaned_path in cleaned_files:
                    try:
                        df = pd.read_csv(cleaned_path)
                    except Exception as e:
                        print(f"      [WARN] Failed reading {cleaned_path}: {e}")
                        continue

                    # base_prefix = everything up to and including "_{experiment}_{instrument}"
                    stem = cleaned_path.stem
                    marker = f"_{experiment}_{instrument}"
                    marker_idx = stem.find(marker)
                    if marker_idx == -1:
                        # shouldn't happen since cleaned_files was already filtered on this marker
                        print(f"      [WARN] Could not locate '{marker}' in {cleaned_path.name}, skipping")
                        continue
                    base_prefix = stem[: marker_idx + len(marker)]

                    if run_ferrybox_panel:
                        try:
                            fig = plot_ferrybox_ctd_panel(
                                df,
                                experiment=experiment,
                                instrument=instrument,
                                plot_labels=PLOT_LABELS,
                                leg=current_leg,
                                leg_start_end_path=leg_start_end_path,
                            )
                            if fig is not None:
                                process_fig(
                                    fig,
                                    name="colour_pannel",
                                    base_name=base_prefix,
                                    outdir_pdf=paths.get("fig_pdf_folder_name"),
                                    outdir_png=paths.get("fig_png_folder_name"),
                                )
                                print(f"      [OK] Plotted {cleaned_path.name}: {experiment}/{instrument}/ferrybox_colour_pannel")
                        except Exception as e:
                            print(f"      [SKIP] {experiment}/{instrument}/ferrybox_colour_pannel in {cleaned_path.name}: {e}")

                    for plot_type in diagnostic_plot_types:
                        try:
                            fig = DIAGNOSTIC_PLOTS[plot_type](
                                df,
                                experiment=experiment,
                                instrument=instrument,
                                plot_labels=PLOT_LABELS,
                                leg=current_leg,
                                leg_start_end_path=leg_start_end_path,
                                raw_csv_path=paths.get("combined_output_file"),
                            )
                            if fig is not None:   # None: this plot doesn't apply to this instrument
                                process_fig(
                                    fig,
                                    name=plot_type,
                                    base_name=base_prefix,
                                    outdir_pdf=paths.get("fig_pdf_folder_name"),
                                    outdir_png=paths.get("fig_png_folder_name"),
                                )
                                print(f"      [OK] Plotted {cleaned_path.name}: {experiment}/{instrument}/{plot_type}")
                        except Exception as e:
                            print(f"      [SKIP] {experiment}/{instrument}/{plot_type} in {cleaned_path.name}: {e}")

                    for variable in (variables if per_variable_plot_types else []):
                        if variable not in df.columns:
                            print(f"      [SKIP] Variable '{variable}' not found in {cleaned_path.name}")
                            continue

                        run_base_name = f"{base_prefix}_{variable}"
                        try:
                            plot_all_reports(
                                df,
                                variable,
                                per_variable_plot_types,
                                base_name=run_base_name,
                                experiment=experiment,
                                instrument=instrument,
                                plot_labels=PLOT_LABELS,
                                outdir_pdf=paths.get("fig_pdf_folder_name"),
                                outdir_png=paths.get("fig_png_folder_name"),
                                leg=current_leg,
                                leg_start_end_path=leg_start_end_path,
                            )
                        except Exception as e:
                            print(f"      [SKIP] {experiment}/{instrument}/{variable} in {cleaned_path.name}: {e}")
                            continue

                        print(f"      [OK] Plotted {cleaned_path.name}: {experiment}/{instrument}/{variable}")


def run_expedition_plotting(
    cruise,
    only_experiments=None,
    only_instruments=None,
    only_variables=None,
):
    """
    Expedition-length time series (all legs combined), built from the
    combined 5min files. Not per-leg, so this should be called once,
    separate from run_plotting()'s per-leg loop.
    """
    print(f"\n{'=' * 80}")
    print(f"                 PLOTTING: {cruise} - FULL EXPEDITION")
    print(f"{'=' * 80}")
    plot_expedition_report(
        cruise=cruise,
        only_experiments=only_experiments,
        only_instruments=only_instruments,
        only_variables=only_variables,
    )
    run_expedition_diagnostics(
        cruise=cruise,
        only_experiments=only_experiments,
        only_instruments=only_instruments,
    )


if __name__ == "__main__":
    legs_to_run = LEGS if LEG is None else (LEG if isinstance(LEG, (list, tuple)) else [LEG])
    for leg in legs_to_run:
        try:
            run_plotting(
                cruise=CRUISE,
                leg=leg,
                plot_types_list=DEFAULT_PLOT_TYPES,
                only_experiments=ONLY_EXPERIMENTS,
                only_instruments=ONLY_INSTRUMENTS,
                only_variables=ONLY_VARIABLES,
            )
        except Exception as e:
            print(f"[SKIP] LEG {leg} plotting failed: {e}")

    try:
        run_expedition_plotting(
            cruise=CRUISE,
            only_experiments=ONLY_EXPERIMENTS,
            only_instruments=ONLY_INSTRUMENTS,
            only_variables=ONLY_VARIABLES,
        )
    except Exception as e:
        print(f"[SKIP] Expedition plotting failed: {e}")
