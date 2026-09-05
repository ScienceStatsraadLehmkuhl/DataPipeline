import os
from pathlib import Path
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
    extract_bridge_gap_positions,
    find_bridge_input_folder,
    gap_windows_without_coverage,
    merge_gga_with_gap_fill,
    load_cached_merged_positions,
    gps_merged_sources_path,
    ECHOSOUNDER_NC_SUBFOLDER,
    ECHOSOUNDER_CSV_SUBFOLDER,
)



def _run_merged_gps_through_pipeline(merged, merged_path, current_leg, leg_start_end_path, sooguard_log_path):
    """
    Run GPS-MERGED-SOURCES (the final position product, GGA plus any
    EK80/Ferrybox gap-fill) through the same cleaning + subsampling routine
    every other instrument gets from data_process -- producing its own
    _cleaned/_1min/_3min/_5min companions alongside merged_path.

    Uses data_process's non-geotag branch (gga_df=None, autoload disabled):
    this file already *is* a position product, not something to geotag
    against another one. "source" is excluded from numeric coercion since
    it's a string tag (GGA/EK80_gps/Ferrybox), not a measurement -- it's
    still dropped from the _1min/_3min/_5min outputs same as any other
    non-numeric column, since subsample() only averages numeric columns.
    """
    merged_cleaned_csv = str(Path(merged_path).with_name(Path(merged_path).stem + "_cleaned.csv"))
    data_process(
        merged.copy(),
        merged_cleaned_csv,
        rename_map=None,
        experiment="NAVIGATION",
        instrument="GPS-MERGED-SOURCES",
        gga_df=None,
        autoload_gga_csv=False,
        leg=current_leg,
        legs_path=leg_start_end_path,
        sooguard_path=sooguard_log_path,
        exclude_numeric_cols=["source"],
    )


def _apply_gga_gap_fill(cruise, current_leg, gga_df, gga_cleaned_csv, gga_combined_csv, exp_folder_name, leg_start_end_path, sooguard_log_path):
    """
    Check the just-processed GGA for gaps and, if any exceed
    GGA_GAP_FILL_THRESHOLD_MINUTES, fill them from EK80/Ferrybox positions
    (both share GGA's MRU). Always ensures GPS-MERGED-SOURCES.csv exists and
    returns it as the position set to use as gga_df for georeferencing
    everything else -- GGA-only (source == "GGA" for every row) when there
    are no gaps, so this is a single, predictable position product per leg
    regardless of gap status. EK80/Ferrybox are only ever touched when a
    gap actually needs filling.

    Reuses the existing merged file (skipping the gap check and any
    EK80/Ferrybox work entirely) whenever it's already at least as fresh as
    `gga_combined_csv` -- GGA's *raw combined* CSV, not its cleaned one,
    since the cleaned CSV is rewritten unconditionally every run (see
    load_cached_merged_positions) and would never look stable enough to
    reuse against.
    """
    merged_path = gps_merged_sources_path(cruise, current_leg, exp_folder_name)

    cached = load_cached_merged_positions(merged_path, gga_combined_csv)
    if cached is not None:
        print(f"      [GAP-FILL] Reusing existing {os.path.basename(merged_path)} (up to date with GGA)")
        _run_merged_gps_through_pipeline(cached, merged_path, current_leg, leg_start_end_path, sooguard_log_path)
        return cached

    gap_windows = find_gga_gaps(
        gga_cleaned_csv,
        current_leg,
        leg_start_end_path,
        threshold_minutes=GGA_GAP_FILL_THRESHOLD_MINUTES,
    )

    if not gap_windows:
        merged = merge_gga_with_gap_fill(gga_df, None, None)
        update_csv(merged, merged_path)
        _run_merged_gps_through_pipeline(merged, merged_path, current_leg, leg_start_end_path, sooguard_log_path)
        return merged

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

    # Bridge (ship's own nav log export) is last resort: only tried for
    # whatever gap windows EK80/Ferrybox still leave open, never alongside
    # them -- see gps_gap_fill module docstring.
    remaining_gap_windows = gap_windows_without_coverage(gap_windows, ek80_positions, ferrybox_positions)
    bridge_positions = None
    if remaining_gap_windows:
        print(
            f"      [GAP-FILL] {len(remaining_gap_windows)} gap(s) still uncovered after EK80/Ferrybox; "
            f"trying Bridge nav log (last resort)"
        )
        try:
            bridge_folder = find_bridge_input_folder(cruise, current_leg)
            bridge_positions = extract_bridge_gap_positions(bridge_folder, remaining_gap_windows)
        except Exception as exc:
            print(f"      [WARN] Could not load Bridge positions for gap-fill: {exc}")
            bridge_positions = None
        print(f"      [GAP-FILL] extract_bridge_gap_positions returned: {0 if bridge_positions is None else len(bridge_positions)} row(s)")

    print(f"      [GAP-FILL] gga_df: {len(gga_df)} row(s), dtype(time)={gga_df['time'].dtype}")
    merged = merge_gga_with_gap_fill(gga_df, ek80_positions, ferrybox_positions, bridge_positions)
    print(f"      [GAP-FILL] merged source counts: {merged['source'].value_counts().to_dict()}")

    n_ek80 = (merged["source"] == "EK80_gps").sum()
    n_ferrybox = (merged["source"] == "Ferrybox").sum()
    n_bridge = (merged["source"] == "Bridge").sum()
    print(f"      [GAP-FILL] added {n_ek80} EK80 fix(es), {n_ferrybox} Ferrybox fix(es), {n_bridge} Bridge fix(es) inside gaps")

    update_csv(merged, merged_path)
    _run_merged_gps_through_pipeline(merged, merged_path, current_leg, leg_start_end_path, sooguard_log_path)

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
        gps_merged_path = None

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
                            gps_source_path=gps_merged_path,  # NEW: also invalidate geotag if GPS source changed
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
                                combined_path,
                                exp_folder_name,
                                leg_start_end_path,
                                sooguard_log_path,
                            )
                            gps_merged_path = gps_merged_sources_path(cruise, current_leg, exp_folder_name)
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
    legs_to_run = LEGS if LEG is None else (LEG if isinstance(LEG, (list, tuple)) else [LEG])
    for leg in legs_to_run:
        run_processing(
            cruise=CRUISE,
            leg=leg,
            only_experiments=ONLY_EXPERIMENTS,
            only_instruments=ONLY_INSTRUMENTS,
            only_variables=ONLY_VARIABLES,
        )
