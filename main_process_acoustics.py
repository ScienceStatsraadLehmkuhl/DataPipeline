"""
Acoustics processing pipeline, run independently of "process sensors"
(main_process_sensors.py no longer touches the ACOUSTIC experiment at all --
raw acoustic acquisition doesn't fit its generic zip/json/csv/cnv import).

Each acoustic source owns its own raw-conversion module. Only EK80's
echosounder and ADCP channels are implemented so far; Teledyne ADCP and the
three hydrophones (wav + txt) are not yet -- add their conversion modules and
list them in ACOUSTIC_SOURCES as they land.

Sources that produce a combined CSV with a time column (currently only
EK80_echos_csv) are run through the same data_process() cleaning +
interval-resampling step main_process_sensors.py uses for every other
instrument, so they still flow into combine_dataset_new.py / gap_analysis.py
automatically -- both of those already discover files by name via
DataPipeline.globals.INSTRUMENTS/EXPERIMENTS, they don't care which pipeline
produced them. Sources without a flat time series (EK80_CP300-ADCP -> one
netCDF per raw file) are just converted and left as-is; there is nothing to
clean/interval-resample/gap-check about a velocity-profile netCDF the way
there is about a plain time series CSV.
"""
import traceback

import pandas as pd

from DataPipeline.data_processing_sensors import data_process
from DataPipeline.globals import RENAME_COLUMNS
from DataPipeline.input_tools import input_folders_processer
from DataPipeline.main_globals import CRUISE, LEG, ONLY_ACOUSTICS
from DataPipeline.main_process_ek80_adcp import run_processing_ek80_adcp
from DataPipeline.main_process_ek80_echosounder import run_processing_ek80_echosounder
from DataPipeline.manual_data_read import get_logsheet_paths

EXPERIMENT = "ACOUSTIC"

# Acoustic sources implemented so far, named after their DataPipeline.globals
# INSTRUMENTS["ACOUSTIC"] slot so ONLY_ACOUSTICS filtering matches the same
# vocabulary as ONLY_INSTRUMENTS elsewhere. Extend as Teledyne ADCP / the
# three hydrophones get their own conversion modules.
ACOUSTIC_SOURCES = ["EK80_echos_csv", "EK80_CP300-ADCP"]


def _clean_and_interval_resample_echosounder(cruise, leg, combined_path, leg_start_end_path, sooguard_log_path):
    """Run the standard clean + interval-resample step on one leg's EK80 echosounder CSV."""
    (
        _input_folder_name,
        _output_folder_name,
        exp_folder_name,
        _fig_png_folder_name,
        _fig_pdf_folder_name,
        cleaned_output_file,
        _output_file,
        _base_name,
    ) = input_folders_processer(leg, EXPERIMENT, "EK80_echos_csv", cruise=cruise)

    df = pd.read_csv(combined_path)

    data_process(
        df,
        cleaned_output_file,
        rename_map=RENAME_COLUMNS,
        experiment=EXPERIMENT,
        instrument="EK80_echos_csv",
        leg=leg,
        legs_path=leg_start_end_path,
        sooguard_path=sooguard_log_path,
        raw_source_path=combined_path,
        # EK80 carries its own latitude/longitude (and latitude_mru1/longitude_mru1)
        # from the Platform group -- no GGA geotagging here (unlike instruments with
        # no position of their own).
        autoload_gga_csv=False,
    )


def run_processing_acoustics(cruise, leg=None, only_acoustics=None, sonar_model="EK80"):
    if cruise is None:
        raise ValueError("run_processing_acoustics requires cruise to be provided.")

    sources = ACOUSTIC_SOURCES
    if only_acoustics is not None:
        sources = [s for s in sources if s in only_acoustics]

    if not sources:
        print("      [SKIP] No acoustic sources selected")
        return

    print(f"\n{'=' * 80}")
    print(f"                 PROCESSING ACOUSTICS: {cruise} -- sources: {sources}")
    print(f"{'=' * 80}")

    leg_start_end_path, sooguard_log_path = get_logsheet_paths(cruise)

    if "EK80_echos_csv" in sources:
        combined_paths = run_processing_ek80_echosounder(cruise, leg=leg, sonar_model=sonar_model)
        for current_leg, combined_path in (combined_paths or {}).items():
            try:
                _clean_and_interval_resample_echosounder(
                    cruise, current_leg, combined_path, leg_start_end_path, sooguard_log_path
                )
                print(f"      [OK] LEG {current_leg} EK80 echosounder cleaned + interval files written")
            except Exception as exc:
                print(f"      [ERROR] Failed cleaning LEG {current_leg} EK80 echosounder:\n{exc}")
                traceback.print_exc()

    if "EK80_CP300-ADCP" in sources:
        run_processing_ek80_adcp(cruise, leg=leg, sonar_model=sonar_model)


if __name__ == "__main__":
    run_processing_acoustics(cruise=CRUISE, leg=LEG, only_acoustics=ONLY_ACOUSTICS)
