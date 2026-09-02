import os
from DataPipeline.globals import LEGS, EXPERIMENTS, INSTRUMENTS, RENAME_COLUMNS, get_variables
from DataPipeline.input_tools import import_and_process_sources, input_folders_processer
from DataPipeline.data_processing_sensors import data_process
from DataPipeline.manual_data_read import get_logsheet_paths
from DataPipeline.main_globals import CRUISE, LEG, ONLY_EXPERIMENTS, ONLY_INSTRUMENTS, ONLY_VARIABLES



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
