"""
Hydrophone spectrum-log (LUF*.txt) processing, one instrument per serial.

Per leg and hydrophone: txt -> per-file CSV -> combined
{cruise}_LEG{leg}_ACOUSTIC_Hydrophone_{serial}.csv (input_tools_hydrophones),
then the same data_process() every sensor gets: geotag against the leg's
GPS-MERGED-SOURCES table, cleaning, and the _1min/_3min/_5min/_1h/_1D
averages (dB levels averaged energetically, see vocabulary.DB_LEVEL_PREFIXES).
cleaning() has no rules for the hydrophones, so no row or value is removed.

The wav recordings (LUW*.wav) are not processed here; their anomaly detection
is main_process_hydrophone_wav.py.
"""
import os
import traceback

import pandas as pd

from DataPipeline.sensors.data_processing_sensors import data_process
from DataPipeline.vocabulary import HYDROPHONE_SERIALS, LEGS
from DataPipeline.ingest.input_tools import _has_output_csvs, input_folders_processer
from DataPipeline.acoustics.input_tools_hydrophones import (
    HYDROPHONE_RAW_SUBFOLDER,
    TEXT_COLUMNS,
    ensure_hydrophone_combined_csv,
    list_spectrum_txts,
)
from DataPipeline.settings import CRUISE, LEG
from DataPipeline.sensors.gps_gap_fill import load_existing_gps
from DataPipeline.ingest.manual_data_read import get_logsheet_paths

EXPERIMENT = "ACOUSTIC"


def run_processing_hydrophones(cruise, leg=None, serials=None):
    """Process every hydrophone's spectrum txts for the given leg(s) (None = all legs)."""
    if cruise is None:
        raise ValueError("run_processing_hydrophones requires cruise to be provided.")

    legs = LEGS if leg is None else (leg if isinstance(leg, (list, tuple)) else [leg])
    serials = HYDROPHONE_SERIALS if serials is None else serials
    leg_start_end_path, sooguard_log_path = get_logsheet_paths(cruise)

    for current_leg in legs:
        input_folder, _out, exp_folder_name, *_rest = input_folders_processer(
            current_leg, EXPERIMENT, HYDROPHONE_RAW_SUBFOLDER, cruise=cruise
        )
        # Raw txts removed (or the whole raw folder gone): a serial is still
        # reprocessed from its per-file CSVs already in processed_data.
        has_raw = os.path.isdir(input_folder)
        txts_by_serial = list_spectrum_txts(input_folder) if has_raw else {}
        processed_serials = {
            s for s in serials if _has_output_csvs(os.path.join(exp_folder_name, f"Hydrophone_{s}"))
        }
        if not txts_by_serial and not processed_serials:
            print(f"      [SKIP] LEG {current_leg}: no {EXPERIMENT}/{HYDROPHONE_RAW_SUBFOLDER} data, raw or processed")
            continue

        print(f"\n{'=' * 80}")
        print(f"                 PROCESSING HYDROPHONES: {cruise} - LEG {current_leg}")
        print(f"{'=' * 80}")
        if not has_raw:
            print("      [HYDROPHONES] No raw folder; reprocessing from existing per-file CSVs")

        unknown = sorted(set(txts_by_serial) - set(HYDROPHONE_SERIALS))
        if unknown:
            print(f"      [WARN] Spectrum files from serial(s) not in HYDROPHONE_SERIALS, skipped: {unknown}")

        gga_df = gps_merged_path = None
        for serial in serials:
            txt_names = txts_by_serial.get(serial, [])
            if not txt_names and serial not in processed_serials:
                continue
            instrument = f"Hydrophone_{serial}"
            print(f"   PROCESSING LEG {current_leg}: {instrument} ({len(txt_names)} raw txt files)")

            try:
                (
                    _input_folder_name,
                    csv_folder,
                    exp_folder_name,
                    _fig_png_folder_name,
                    _fig_pdf_folder_name,
                    cleaned_output_file,
                    output_file,
                    _base_name,
                ) = input_folders_processer(current_leg, EXPERIMENT, instrument, cruise=cruise)

                combined_path = ensure_hydrophone_combined_csv(input_folder, txt_names, csv_folder, output_file)
                if combined_path is None:
                    print(f"      [SKIP] No readable spectrum files for {instrument}")
                    continue

                if gga_df is None:
                    gga_df, gps_merged_path = load_existing_gps(cruise, current_leg)

                df = pd.read_csv(combined_path, low_memory=False)
                data_process(
                    df,
                    cleaned_output_file,
                    rename_map=None,  # per-file CSVs already carry the canonical column names
                    experiment=EXPERIMENT,
                    instrument=instrument,
                    gga_df=gga_df,
                    autoload_gga_csv=False,
                    leg=current_leg,
                    legs_path=leg_start_end_path,
                    sooguard_path=sooguard_log_path,
                    exclude_numeric_cols=TEXT_COLUMNS,
                    raw_source_path=combined_path,
                    gps_source_path=gps_merged_path,
                )
                print(f"      [OK] Processed LEG {current_leg}: {instrument}")
            except Exception as exc:
                print(f"      [ERROR] Failed processing LEG {current_leg}: {instrument}\n{exc}")
                traceback.print_exc()


if __name__ == "__main__":
    run_processing_hydrophones(cruise=CRUISE, leg=LEG)
