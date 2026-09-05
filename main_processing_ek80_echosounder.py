import os
import sys
import traceback
from pathlib import Path

# Bare imports below (not DataPipeline.xxx) so this module can still be run
# standalone from inside DataPipeline/. Ensure this file's own directory is on
# sys.path so the same bare imports also resolve when this module is instead
# imported as DataPipeline.main_processing_ek80_echosounder (e.g. from
# DataPipeline.main_processing_acoustics) -- same fix as
# DataPipeline/gap_analysis.py and DataPipeline/input_tools_ek80_echosounder.py.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from globals import LEGS
from input_tools import input_folders_processer
from input_tools_ek80_echosounder import ensure_ek80_echosounder_combined_csv
from main_globals import *


EXPERIMENT = "ACOUSTIC"
INSTRUMENT_RAW = "EK80-RAW"  # actual raw-file folder name on the input share
ECHOSOUNDER_NCDF_SUBFOLDER = "EK80_echos_ncdf"
ECHOSOUNDER_CSV_SUBFOLDER = "EK80_echos_csv"




def run_processing_ek80_echosounder(cruise, leg=None, sonar_model="EK80"):
    """Convert raw EK80 echosounder files to a combined per-leg CSV.

    Returns {leg: combined_csv_path} for legs that succeeded (failed legs are
    omitted, matching the [ERROR]-and-continue behavior below).
    """
    if cruise is None:
        raise ValueError("run_processing_ek80_echosounder requires cruise to be provided.")

    legs = LEGS if leg is None else (leg if isinstance(leg, (list, tuple)) else [leg])
    combined_paths = {}

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
            _output_file_for_raw_instrument,
            _base_name,
        ) = input_folders_processer(current_leg, EXPERIMENT, INSTRUMENT_RAW, cruise=cruise)

        # The combined CSV must be named after the "EK80_echos_csv" instrument slot
        # (see DataPipeline.globals.INSTRUMENTS), not INSTRUMENT_RAW above -- that's
        # only the raw-file instrument used to locate the .raw input files. Naming it
        # after EK80_echos_csv is what lets gap_analysis.py and combine_dataset_new.py
        # find it.
        base_name = f"{cruise}_LEG{current_leg}_{EXPERIMENT}_{ECHOSOUNDER_CSV_SUBFOLDER}"
        output_file = str(Path(exp_folder_name) / f"{base_name}.csv")

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
            combined_paths[current_leg] = combined_path
        except Exception as exc:
            print(f"      [ERROR] Failed processing LEG {current_leg} EK80 echosounder:\n{exc}")
            traceback.print_exc()
            continue

        print(f"\n{'-' * 33}")
        print(f"    FINISHED PROCESSING LEG {current_leg} EK80 ECHOSOUNDER")
        print(f"{'-' * 33}")

    return combined_paths


if __name__ == "__main__":
    run_processing_ek80_echosounder(cruise=CRUISE, leg=LEG)