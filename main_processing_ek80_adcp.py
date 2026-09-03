import os
import traceback

from globals import LEGS
from input_tools import input_folders_processer
from input_tools_ek80_adcp import ensure_ek80_adcp_netcdfs
from main_globals import *


EXPERIMENT = "ACOUSTIC"
INSTRUMENT_RAW = "EK80-RAW"  # the ADCP channels are embedded in the EK80 raw files
ADCP_OUTPUT_SUBFOLDER = "CP300-ADCP"


def run_processing_ek80_adcp(cruise, leg=None, sonar_model="EK80"):
    if cruise is None:
        raise ValueError("run_processing_ek80_adcp requires cruise to be provided.")

    legs = LEGS if leg is None else [leg]

    for current_leg in legs:
        print(f"\n{'=' * 80}")
        print(f"                 PROCESSING EK80 ADCP (CP300, embedded in EK80 raw files): {cruise} - LEG {current_leg}")
        print(f"{'=' * 80}")

        (
            input_folder_name,
            _output_folder_name,
            exp_folder_name,
            _fig_png_folder_name,
            _fig_pdf_folder_name,
            _cleaned_output_file,
            _output_file,
            _base_name,
        ) = input_folders_processer(current_leg, EXPERIMENT, INSTRUMENT_RAW, cruise=cruise)

        adcp_output_folder_name = os.path.join(exp_folder_name, ADCP_OUTPUT_SUBFOLDER)

        try:
            written = ensure_ek80_adcp_netcdfs(
                input_folder_name=input_folder_name,
                output_folder_name=adcp_output_folder_name,
                sonar_model=sonar_model,
            )
            print(
                f"      [OK] LEG {current_leg} EK80 ADCP netCDFs -> "
                f"{len(written)} file(s) in {adcp_output_folder_name}"
            )
        except Exception as exc:
            print(f"      [ERROR] Failed processing LEG {current_leg} EK80 ADCP:\n{exc}")
            traceback.print_exc()
            continue

        print(f"\n{'-' * 33}")
        print(f"    FINISHED PROCESSING LEG {current_leg} EK80 ADCP")
        print(f"{'-' * 33}")


if __name__ == "__main__":
    run_processing_ek80_adcp(cruise=CRUISE, leg=LEG)
