import os
from DataPipeline.globals import LEGS, EXPERIMENTS, INSTRUMENTS, RENAME_COLUMNS, get_variables
from DataPipeline.input_tools import import_and_process_sources, input_folders_processer, update_csv
from DataPipeline.data_processing_sensors import data_process, keep_and_rename
from DataPipeline.manual_data_read import get_logsheet_paths
from DataPipeline.main_globals import (
    CRUISE, LEG, ONLY_EXPERIMENTS, ONLY_INSTRUMENTS, ONLY_VARIABLES,
    GGA_GAP_FILL_THRESHOLD_MINUTES,
)
from DataPipeline.gps_gap_fill import (
    find_gga_gaps,
    extract_ek80_gap_positions,
    extract_ferrybox_positions,
    merge_gga_with_gap_fill,
    ECHOSOUNDER_NC_SUBFOLDER,
    ECHOSOUNDER_CSV_SUBFOLDER,
)



def _apply_gga_gap_fill(cruise, current_leg, gga_df, gga_cleaned_csv, exp_folder_name, leg_start_end_path):
    """
    Check the just-processed GGA for gaps and, if any exceed
    GGA_GAP_FILL_THRESHOLD_MINUTES, fill them from EK80/Ferrybox positions
    (both share GGA's MRU) and return the merged position set to use as
    gga_df for georeferencing everything else. Returns gga_df unchanged if
    there are no gaps worth filling.
    """
    gap_windows = find_gga_gaps(
        gga_cleaned_csv,
        current_leg,
        leg_start_end_path,
        threshold_minutes=GGA_GAP_FILL_THRESHOLD_MINUTES,
    )

    if not gap_windows:
        return gga_df

    print(
        f"      [GAP-FILL] {len(gap_windows)} GGA gap(s) > {GGA_GAP_FILL_THRESHOLD_MINUTES} min "
        f"in LEG {current_leg}; pulling positions from EK80/Ferrybox"
    )
    for start, end in gap_windows:
        print(f"      [GAP-FILL]   gap window: {start} -> {end}")

    (
        ek80_input_folder,
        _ek80_output_folder,
        ek80_exp_folder,
        _ek80_fig_png,
        _ek80_fig_pdf,
        _ek80_cleaned_output_file,
        _ek80_output_file,
        _ek80_base_name,
    ) = input_folders_processer(current_leg, "ACOUSTIC", "EK80-RAW", cruise=cruise)
    try:
        ek80_positions = extract_ek80_gap_positions(
            ek80_input_folder,
            os.path.join(ek80_exp_folder, ECHOSOUNDER_NC_SUBFOLDER),
            os.path.join(ek80_exp_folder, ECHOSOUNDER_CSV_SUBFOLDER),
            gap_windows,
        )
    except Exception as exc:
        print(f"      [WARN] Could not load EK80 positions for gap-fill: {exc}")
        ek80_positions = None
    if ek80_positions is None:
        print("      [GAP-FILL] extract_ek80_gap_positions returned: None")
    else:
        time_dtype = ek80_positions["time"].dtype if len(ek80_positions) else "n/a"
        source_values = ek80_positions["source"].unique().tolist() if len(ek80_positions) else []
        print(
            f"      [GAP-FILL] extract_ek80_gap_positions returned: {len(ek80_positions)} row(s), "
            f"dtype(time)={time_dtype}, source values={source_values}"
        )

    (
        ferry_input_folder,
        ferry_output_folder,
        ferry_exp_folder,
        _ferry_fig_png,
        _ferry_fig_pdf,
        _ferry_cleaned_output_file,
        ferry_output_file,
        _ferry_base_name,
    ) = input_folders_processer(current_leg, "OCEANOGRAPHY", "Ferrybox_CTD", cruise=cruise)
    try:
        ferry_raw_df = import_and_process_sources(
            ferry_input_folder, ferry_output_folder, ferry_exp_folder, ferry_output_file,
        )
        print(
            f"      [GAP-FILL] Ferrybox raw: {len(ferry_raw_df)} row(s); "
            f"time/latitude/longitude sample: "
            f"{ferry_raw_df[['time', 'latitude', 'longitude']].head(3).to_dict('records') if {'time', 'latitude', 'longitude'}.issubset(ferry_raw_df.columns) else 'columns missing'}"
        )
        ferry_df = keep_and_rename(
            ferry_raw_df, RENAME_COLUMNS["OCEANOGRAPHY"]["Ferrybox_CTD"], warn_missing=True,
        )
    except Exception as exc:
        print(f"      [WARN] Could not load Ferrybox positions for gap-fill: {exc}")
        ferry_df = None
    ferrybox_positions = extract_ferrybox_positions(ferry_df, gap_windows)
    print(f"      [GAP-FILL] extract_ferrybox_positions returned: {len(ferrybox_positions)} row(s)")

    print(f"      [GAP-FILL] gga_df: {len(gga_df)} row(s), dtype(time)={gga_df['time'].dtype}")
    merged = merge_gga_with_gap_fill(gga_df, ek80_positions, ferrybox_positions)
    print(f"      [GAP-FILL] merged source counts: {merged['source'].value_counts().to_dict()}")

    n_ek80 = (merged["source"] == "EK80_gps").sum()
    n_ferrybox = (merged["source"] == "Ferrybox").sum()
    print(f"      [GAP-FILL] added {n_ek80} EK80 fix(es), {n_ferrybox} Ferrybox fix(es) inside gaps")

    merged_path = os.path.join(exp_folder_name, f"{cruise}_LEG{current_leg}_NAVIGATION_GPS_merged.csv")
    update_csv(merged, merged_path)

    return merged


def run_processing(
    cruise,
    leg,
    update_flag=True,
    only_experiments=None,
    only_instruments=None,
    only_variables=None,
):
    if cruise is None or leg is None:
        raise ValueError("run_processing requires cruise and leg to be provided by main_globals.py")
    leg_start_end_path, sooguard_log_path = get_logsheet_paths(cruise)

    legs = LEGS if leg is None else [leg]

    for current_leg in legs:
        print(f"\n{'=' * 80}")
        print(f"                 PROCESSING: {cruise} - LEG {current_leg}")
        print(f"{'=' * 80}")

        gga_df = None

        if isinstance(LEGS, (list, tuple, set)) and current_leg not in LEGS:
            print(f"[WARN] leg='{current_leg}' not found in LEGS (continuing anyway).")

        experiments = EXPERIMENTS
        if only_experiments is not None:
            experiments = [e for e in experiments if e in only_experiments]

        for experiment in experiments:
            print(f"\nPROCESSING LEG {current_leg}: {experiment}")

            instruments = INSTRUMENTS.get(experiment, [])
            if only_instruments is not None:
                instruments = [i for i in instruments if i in only_instruments]

            if not instruments:
                print(f"      [SKIP] No instruments configured for {experiment}")
                continue

            for instrument in instruments:
                print(f"   PROCESSING LEG {current_leg}: {instrument}")

                variables = get_variables(experiment, instrument)
                if only_variables is not None:
                    variables = [v for v in variables if v in only_variables]

                if not variables:
                    print(f"      [SKIP] No variables configured for {instrument}")
                    continue

                try:
                    (
                        _input_folder_name,
                        _output_folder_name,
                        exp_folder_name,
                        _fig_png_folder_name,
                        _fig_pdf_folder_name,
                        cleaned_output_file,
                        output_file,
                        _base_name,
                    ) = input_folders_processer(
                        current_leg,
                        experiment,
                        instrument,
                        cruise=cruise,
                    )

                    df = import_and_process_sources(
                        _input_folder_name,
                        _output_folder_name,
                        exp_folder_name,
                        output_file,
                    )

                    combined_path = os.path.join(exp_folder_name, output_file)  # NEW: same join ensure_combined_csv uses

                    if update_flag:
                        df = data_process(
                            df,
                            cleaned_output_file,
                            rename_map=RENAME_COLUMNS,
                            experiment=experiment,
                            instrument=instrument,
                            gga_df=gga_df,
                            leg=current_leg,
                            legs_path=leg_start_end_path,
                            sooguard_path=sooguard_log_path,
                            raw_source_path=combined_path,  # NEW
                        )

                    if experiment == "NAVIGATION" and instrument == "GGA":
                        if update_flag:
                            # gap-fill needs the cleaned GGA CSV on disk (written by
                            # data_process above), so only run it when that happened.
                            gga_df = _apply_gga_gap_fill(
                                cruise,
                                current_leg,
                                df.copy(),
                                cleaned_output_file,
                                exp_folder_name,
                                leg_start_end_path,
                            )
                        else:
                            gga_df = df.copy()

                    print(f"      [OK] Processed LEG {current_leg}: {instrument}")

                except Exception as exc:
                    print(
                        f"      [ERROR] Failed processing LEG {current_leg}: {instrument}\n"
                        f"{exc}"
                    )
                    continue

        print(f"\n{'-' * 33}")
        print(f"    FINISHED PROCESSING LEG {current_leg}")
        print(f"{'-' * 33}")


if __name__ == "__main__":
    legs_to_run = LEGS if LEG is None else [LEG]
    for leg in legs_to_run:
        run_processing(
            cruise=CRUISE,
            leg=leg,
            only_experiments=ONLY_EXPERIMENTS,
            only_instruments=ONLY_INSTRUMENTS,
            only_variables=ONLY_VARIABLES,
        )
