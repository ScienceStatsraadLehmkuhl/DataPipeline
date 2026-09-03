import os
from pathlib import Path
import traceback

import pandas as pd
import echopype as ep

from input_tools_ek80_adcp import exclude_ek80_adcp_channels


RELEVANT_INPUT_EXTS_EK80_ECHOSOUNDER = (".raw",)

# The Platform group bundles several independent sensor streams, each on its
# own timestamp coordinate and sampling rate: time1 is GPS position fixes
# (decoded from NMEA), time2 is motion-sensor (MRU) readings, time3 is a
# second, typically much faster, MRU-derived position feed. These do not
# share a common per-ping row -- e.g. a file can have 1 GPS fix, 3 motion
# readings, and 757 secondary-position readings, all for the same period.
#
# NOTE: this mapping reflects current echopype conventions but can shift
# slightly between versions/sonar models. First run: inspect ed["Platform"]
# for your actual files and adjust the mapping below if a variable you
# expect is on a different coordinate or missing.
_PLATFORM_TIME_VARS = {
    "time1": ("latitude", "longitude"),
    "time2": ("heading", "pitch", "roll", "vertical_offset"),
    "time3": ("latitude_mru1", "longitude_mru1"),
}


# ---------------------------------------------------------------------------
# Per-file conversion + extraction
# ---------------------------------------------------------------------------

def _extract_easy_parameters(ed: "ep.echodata.EchoData", raw_filename: str) -> pd.DataFrame:
    """
    Pull the 'easy' parameters out of a converted EchoData object with no
    calibration or computation: GPS position, attitude, and a few static
    environment values.

    Long/tidy format: one row per (timestamp, variable) pair, tagged with
    which Platform-group time coordinate it came from (see
    _PLATFORM_TIME_VARS). A single wide table isn't possible here since
    GPS fixes, motion readings, and the secondary position feed are
    independent streams sampled at different rates -- forcing them into
    one row per timestamp would mean inventing an alignment between them.

    Environment values are usually static (or coarsely sampled) per file,
    so they're broadcast as constant columns across every row rather than
    time-aligned to any particular sensor stream.
    """
    platform = ed["Platform"]

    rows = []
    for time_coord, var_names in _PLATFORM_TIME_VARS.items():
        if time_coord not in platform.coords:
            continue
        timestamps = platform[time_coord].values
        for var in var_names:
            if var not in platform:
                continue
            values = platform[var].values
            if values.shape[0] != timestamps.shape[0]:
                continue  # not actually indexed by this time coordinate; skip
            for ts, val in zip(timestamps, values):
                rows.append({"timestamp": ts, "time_source": time_coord, "variable": var, "value": val})

    df = pd.DataFrame(rows, columns=["timestamp", "time_source", "variable", "value"])

    # Environment group: usually static or coarsely sampled per file.
    # Attach as constant columns rather than trying to time-align.
    env = ed["Environment"]
    for var in ("temperature", "salinity", "sound_speed_indicative", "sound_absorption"):
        if var in env:
            values = env[var].values
            df[f"env_{var}"] = values.flat[0] if values.size else None

    df["source_raw_file"] = raw_filename
    df["sonar_model"] = ed.sonar_model

    return df


def process_ek80_echosounder_raw_file(raw_file_path: str, output_folder_name: str, sonar_model: str = "EK80") -> str:
    """
    Convert a single EK80 .raw file to netCDF and extract easy parameters
    to a matching CSV. Both outputs are written to output_folder_name,
    named after the raw file (e.g. D20240101-T120000.nc / .csv).

    Returns the path to the per-file CSV.
    """
    os.makedirs(output_folder_name, exist_ok=True)
    raw_filename = Path(raw_file_path).stem

    # Some raw files bundle a wideband ADCP (e.g. a CP300) as extra channels
    # that echopype's EK80 parser cannot represent -- ep.open_raw() otherwise
    # raises a KeyError building the Platform group. Those channels are
    # extracted separately by input_tools_ek80_adcp.py.
    with exclude_ek80_adcp_channels():
        ed = ep.open_raw(raw_file_path, sonar_model=sonar_model)
    ed.to_netcdf(save_path=output_folder_name)

    df = _extract_easy_parameters(ed, raw_filename)
    csv_path = os.path.join(output_folder_name, f"{raw_filename}.csv")
    df.to_csv(csv_path, index=False)

    return csv_path


# ---------------------------------------------------------------------------
# Leg-level staleness handling + combination
# ---------------------------------------------------------------------------

def _latest_mtime(folder, exts):
    if not folder or not os.path.isdir(folder):
        return None
    mtimes = [
        os.path.getmtime(os.path.join(folder, f))
        for f in os.listdir(folder)
        if f.lower().endswith(exts)
    ]
    return max(mtimes) if mtimes else None


def _stale_raw_files(input_folder_name, output_folder_name, exts):
    """Raw files with no matching per-file CSV yet, or newer than their CSV."""
    stale = []
    for f in os.listdir(input_folder_name):
        if not f.lower().endswith(exts):
            continue
        raw_path = os.path.join(input_folder_name, f)
        csv_path = os.path.join(output_folder_name, f"{Path(f).stem}.csv")
        if not os.path.exists(csv_path) or os.path.getmtime(raw_path) > os.path.getmtime(csv_path):
            stale.append(f)
    return stale


def ensure_ek80_echosounder_combined_csv(
    input_folder_name: str,
    output_folder_name: str,
    exp_folder_name: str,
    output_file: str,
    sonar_model: str = "EK80",
) -> str:
    """
    Ensure the leg-level combined EK80 CSV exists and reflects the latest
    raw inputs. Same reuse/staleness philosophy as ensure_combined_csv:

      1) Combined file exists AND no raw .raw file is newer -> reuse it.
      2) Else convert only the .raw files that are new/changed since their
         last per-file CSV was built (netCDF + CSV, one pair per raw file).
      3) Combine all per-file CSVs in the output folder into one leg-level
         combined CSV.
    """
    combined_path = os.path.join(exp_folder_name, output_file)
    raw_mtime = _latest_mtime(input_folder_name, RELEVANT_INPUT_EXTS_EK80_ECHOSOUNDER)

    if os.path.exists(combined_path):
        if raw_mtime is None or os.path.getmtime(combined_path) >= raw_mtime:
            return combined_path

    if not input_folder_name or not os.path.isdir(input_folder_name):
        raise FileNotFoundError(f"EK80 input folder not found: {input_folder_name}")

    stale_files = _stale_raw_files(input_folder_name, output_folder_name, RELEVANT_INPUT_EXTS_EK80_ECHOSOUNDER)
    for filename in stale_files:
        raw_path = os.path.join(input_folder_name, filename)
        try:
            process_ek80_echosounder_raw_file(raw_path, output_folder_name, sonar_model=sonar_model)
            print(f"      [OK] Converted {filename}")
        except Exception:
            traceback.print_exc()
            raise

    csv_files = sorted(Path(output_folder_name).glob("*.csv"))
    if not csv_files:
        raise FileNotFoundError(f"No EK80 per-file CSVs found to combine in {output_folder_name}")

    combined_df = pd.concat((pd.read_csv(f) for f in csv_files), ignore_index=True)
    os.makedirs(exp_folder_name, exist_ok=True)
    combined_df.to_csv(combined_path, index=False)

    return combined_path