import os
import traceback

from globals import LEGS
from input_tools import input_folders_processer
from input_tools_ek80_echosounder import ensure_ek80_echosounder_combined_csv
from main_globals import *


EXPERIMENT = "ACOUSTIC"
INSTRUMENT_RAW = "EK80-RAW"  # actual raw-file folder name on the input share
ECHOSOUNDER_NCDF_SUBFOLDER = "EK80_echos_ncdf"
ECHOSOUNDER_CSV_SUBFOLDER = "EK80_echos_csv"




def run_processing_ek80_echosounder(cruise, leg=None, sonar_model="EK80"):
    if cruise is None:
        raise ValueError("run_processing_ek80_echosounder requires cruise to be provided.")

    legs = LEGS if leg is None else (leg if isinstance(leg, (list, tuple)) else [leg])

    for current_leg in legs:
        print(f"\n{'=' * 80}")
        print(f"                 PROCESSING EK80 ECHOSOUNDER: {cruise} - LEG {current_leg}")
        print(f"{'=' * 80}")

        (
            input_folder_name,
            _output_folder_name,
            exp_folder_name,
            _fig_png_folder_name,
            _fig_pdf_folder_name,
            _cleaned_output_file,
            output_file,
            _base_name,
        ) = input_folders_processer(current_leg, EXPERIMENT, INSTRUMENT_RAW, cruise=cruise)

        echosounder_nc_folder_name = os.path.join(exp_folder_name, ECHOSOUNDER_NCDF_SUBFOLDER)
        echosounder_csv_folder_name = os.path.join(exp_folder_name, ECHOSOUNDER_CSV_SUBFOLDER)

        try:
            combined_path = ensure_ek80_echosounder_combined_csv(
                input_folder_name=input_folder_name,
                nc_folder_name=echosounder_nc_folder_name,
                csv_folder_name=echosounder_csv_folder_name,
                exp_folder_name=exp_folder_name,
                output_file=output_file,
                sonar_model=sonar_model,
            )
            print(f"      [OK] LEG {current_leg} EK80 combined CSV -> {combined_path}")
        except Exception as exc:
            print(f"      [ERROR] Failed processing LEG {current_leg} EK80 echosounder:\n{exc}")
            traceback.print_exc()
            continue

        print(f"\n{'-' * 33}")
        print(f"    FINISHED PROCESSING LEG {current_leg} EK80 ECHOSOUNDER")
        print(f"{'-' * 33}")


if __name__ == "__main__":
    run_processing_ek80_echosounder(cruise=CRUISE, leg=LEG)