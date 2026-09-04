import os

# Harmless, generally-recommended default when HDF5 files may live on a
# network mount: disables HDF5's POSIX byte-range locking, which some
# network filesystems don't support. Note this alone does NOT fix the
# gvfs/SMB reopen problem handled below in process_ek80_echosounder_raw_file
# -- that needed writing locally and copying the finished file over. Must
# be set before netCDF4/xarray/echopype touch the HDF5 library.
os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")

import glob
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

import pandas as pd
import echopype as ep

# input_tools_ek80_adcp is imported bare (not as DataPipeline.input_tools_ek80_adcp)
# so this module can be run standalone from inside DataPipeline/. Ensure this
# file's own directory is on sys.path so the same bare import also resolves
# when this module is instead imported as DataPipeline.input_tools_ek80_echosounder
# (e.g. from DataPipeline.main_process_sensors) -- same fix as DataPipeline/gap_analysis.py.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from input_tools_ek80_adcp import exclude_ek80_adcp_channels


RELEVANT_INPUT_EXTS_EK80_ECHOSOUNDER = (".raw",)


def _copy_to_network_share_with_retry(
    src_path: str, dst_path: str, dst_folder_name: str, attempts: int = 5, delay_seconds: float = 1.0
) -> None:
    """
    Copy a finished local file to the network share, retrying on ENOENT.

    The gvfs/FUSE SMB mounts this pipeline writes to have shown repeated
    metadata quirks (no symlinks, no chmod, unreliable HDF5 file reopen);
    this adds a "just-created directory isn't visible yet to a subsequent
    open()" quirk to that list -- os.makedirs() reports success, but the
    following copyfile() can still raise FileNotFoundError. Re-asserting
    the directory and retrying rides out a short-lived version of that lag.

    If retries don't help, the cause has (in practice) been GVFS's own
    daemon caching a stale negative lookup for that exact destination path
    -- confirmed by: the directory demonstrably exists (stat/listdir both
    succeed), a different filename in the same directory writes fine, and
    even deleting and recreating the directory does not clear it for the
    original filename. That is a client-side cache problem in gvfsd, not
    something retrying from this process can fix; unmounting and
    remounting the share (`gio mount -u` on it, then access it again to
    trigger auto-remount) has resolved it in practice.
    """
    last_error = None
    for attempt in range(1, attempts + 1):
        os.makedirs(dst_folder_name, exist_ok=True)
        try:
            shutil.copyfile(src_path, dst_path)
            return
        except FileNotFoundError as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(delay_seconds)
    raise FileNotFoundError(
        f"Could not create {dst_path!r} after {attempts} attempts, even though its parent "
        "directory exists. This matches a known GVFS/SMB client-side cache issue rather than "
        "a code bug: try unmounting and remounting the network share (e.g. `gio mount -u` on "
        "the share, then access it again to trigger auto-remount) and rerun."
    ) from last_error

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

def _thin_to_1hz(block_df: pd.DataFrame, time_col: str = "timestamp") -> pd.DataFrame:
    """Keep at most one row per second (first in time), dropping the rest.

    Some Platform time coordinates log at tens-to-hundreds of Hz -- observed:
    the secondary MRU position feed (time3) logging ~6.3M rows across just
    49 files for one gap-fill pass. Nothing downstream uses sub-second
    resolution (GGA's own rate is ~1Hz, and georeferencing's merge_asof
    tolerance is 1s), so thinning here -- before the per-file CSV is even
    written -- keeps conversion, the per-file CSV, and the eventual
    full-leg combine step all proportionate to what's actually usable.
    """
    ts = pd.Series(block_df[time_col]).sort_values()
    keep_idx = ts.index[~pd.to_datetime(ts).dt.floor("1s").duplicated(keep="first")]
    return block_df.loc[keep_idx].sort_index()


def _extract_easy_parameters(ed: "ep.echodata.EchoData", raw_filename: str) -> pd.DataFrame:
    """
    Pull the 'easy' parameters out of a converted EchoData object with no
    calibration or computation: GPS position, attitude, and a few static
    environment values.

    One row per (timestamp, time_source) pair, with every variable that
    shares that time coordinate as its own column on that row (see
    _PLATFORM_TIME_VARS) -- e.g. latitude and longitude, both on time1,
    land on the same row. Different time_source blocks are stacked rather
    than merged into a single wide table, since GPS fixes, motion readings,
    and the secondary position feed are independent streams sampled at
    different rates; forcing them onto one shared row per timestamp would
    mean inventing an alignment between them.

    Each time_source block is thinned to at most 1 row/second (see
    _thin_to_1hz) before being stacked -- some Platform streams log far
    denser than that, and nothing downstream uses sub-second resolution.

    Environment values are usually static (or coarsely sampled) per file,
    so they're broadcast as constant columns across every row rather than
    time-aligned to any particular sensor stream.
    """
    platform = ed["Platform"]

    blocks = []
    for time_coord, var_names in _PLATFORM_TIME_VARS.items():
        if time_coord not in platform.coords:
            continue
        timestamps = platform[time_coord].values
        block = {"timestamp": timestamps, "time_source": time_coord}
        for var in var_names:
            if var not in platform:
                continue
            values = platform[var].values
            if values.shape[0] != timestamps.shape[0]:
                continue  # not actually indexed by this time coordinate; skip
            block[var] = values
        if len(block) > 2:  # has at least one variable beyond timestamp/time_source
            blocks.append(_thin_to_1hz(pd.DataFrame(block)))

    df = (
        pd.concat(blocks, ignore_index=True)
        if blocks
        else pd.DataFrame(columns=["timestamp", "time_source"])
    )

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


def process_ek80_echosounder_raw_file(
    raw_file_path: str, nc_folder_name: str, csv_folder_name: str, sonar_model: str = "EK80"
) -> str:
    """
    Convert a single EK80 .raw file to netCDF and extract easy parameters
    to a matching CSV, named after the raw file (e.g. D20240101-T120000.nc
    / .csv) -- the netCDF goes to nc_folder_name, the CSV to csv_folder_name.

    Returns the path to the per-file CSV.
    """
    os.makedirs(nc_folder_name, exist_ok=True)
    os.makedirs(csv_folder_name, exist_ok=True)
    raw_filename = Path(raw_file_path).stem

    # If this raw file was already converted to netCDF and that file is at
    # least as fresh as the raw file, reuse it instead of re-parsing the raw
    # file from scratch: open_raw() (decoding the proprietary Simrad binary
    # format) is the expensive step; open_converted() just reads back
    # already-decoded data, so this only skips redundant work -- it does not
    # affect the netCDF's own content, which always keeps full original
    # resolution (only the CSV extraction below is thinned).
    ed = None
    existing_nc_matches = glob.glob(os.path.join(nc_folder_name, f"{raw_filename}*.nc"))
    if len(existing_nc_matches) == 1 and os.path.getmtime(existing_nc_matches[0]) >= os.path.getmtime(raw_file_path):
        try:
            ed = ep.open_converted(existing_nc_matches[0])
        except Exception as exc:
            print(f"      [WARN] Could not reuse existing netCDF for {raw_filename} ({exc}); reconverting from raw")
            ed = None

    if ed is None:
        # Some raw files bundle a wideband ADCP (e.g. a CP300) as extra channels
        # that echopype's EK80 parser cannot represent -- ep.open_raw() otherwise
        # raises a KeyError building the Platform group. Those channels are
        # extracted separately by input_tools_ek80_adcp.py.
        with exclude_ek80_adcp_channels():
            ed = ep.open_raw(raw_file_path, sonar_model=sonar_model)

        # echopype writes each internal group (Environment, Platform, Sonar,
        # Beam_group*...) as a separate reopen of the same .nc file. The
        # gvfs/FUSE SMB mounts this pipeline writes to don't support reopening
        # a file for append reliably -- it fails partway through with
        # "OSError: [Errno -101] NetCDF: HDF error" (confirmed independent of
        # HDF5's file-locking mode). Build the file on local disk, where
        # reopening works fine, then copy the finished file over -- a plain
        # byte copy has no such problem.
        with tempfile.TemporaryDirectory() as tmp_dir:
            ed.to_netcdf(save_path=tmp_dir)
            local_nc_files = glob.glob(os.path.join(tmp_dir, "*.nc"))
            if len(local_nc_files) != 1:
                raise RuntimeError(
                    f"Expected exactly one netCDF written for {raw_filename}, got {local_nc_files}"
                )
            nc_path = os.path.join(nc_folder_name, os.path.basename(local_nc_files[0]))
            _copy_to_network_share_with_retry(local_nc_files[0], nc_path, nc_folder_name)

    df = _extract_easy_parameters(ed, raw_filename)
    csv_path = os.path.join(csv_folder_name, f"{raw_filename}.csv")
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


def _stale_raw_files(input_folder_name, csv_folder_name, exts):
    """Raw files with no matching per-file CSV yet, or newer than their CSV."""
    stale = []
    for f in os.listdir(input_folder_name):
        if not f.lower().endswith(exts):
            continue
        raw_path = os.path.join(input_folder_name, f)
        csv_path = os.path.join(csv_folder_name, f"{Path(f).stem}.csv")
        if not os.path.exists(csv_path) or os.path.getmtime(raw_path) > os.path.getmtime(csv_path):
            stale.append(f)
    return stale


def ensure_ek80_echosounder_combined_csv(
    input_folder_name: str,
    nc_folder_name: str,
    csv_folder_name: str,
    exp_folder_name: str,
    output_file: str,
    sonar_model: str = "EK80",
) -> str:
    """
    Ensure the leg-level combined EK80 CSV exists and reflects the latest
    raw inputs. Same reuse/staleness philosophy as ensure_combined_csv:

      1) Combined file exists AND no raw .raw file is newer -> reuse it.
      2) Else convert only the .raw files that are new/changed since their
         last per-file CSV was built (netCDF to nc_folder_name, CSV to
         csv_folder_name -- one pair per raw file).
      3) Combine all per-file CSVs in csv_folder_name into one leg-level
         combined CSV.
    """
    combined_path = os.path.join(exp_folder_name, output_file)
    raw_mtime = _latest_mtime(input_folder_name, RELEVANT_INPUT_EXTS_EK80_ECHOSOUNDER)

    if os.path.exists(combined_path):
        if raw_mtime is None or os.path.getmtime(combined_path) >= raw_mtime:
            return combined_path

    if not input_folder_name or not os.path.isdir(input_folder_name):
        raise FileNotFoundError(f"EK80 input folder not found: {input_folder_name}")

    stale_files = _stale_raw_files(input_folder_name, csv_folder_name, RELEVANT_INPUT_EXTS_EK80_ECHOSOUNDER)
    for filename in stale_files:
        raw_path = os.path.join(input_folder_name, filename)
        try:
            process_ek80_echosounder_raw_file(raw_path, nc_folder_name, csv_folder_name, sonar_model=sonar_model)
            print(f"      [OK] Converted {filename}")
        except Exception:
            traceback.print_exc()
            raise

    csv_files = sorted(Path(csv_folder_name).glob("*.csv"))
    if not csv_files:
        raise FileNotFoundError(f"No EK80 per-file CSVs found to combine in {csv_folder_name}")

    combined_df = pd.concat((pd.read_csv(f) for f in csv_files), ignore_index=True)
    os.makedirs(exp_folder_name, exist_ok=True)
    combined_df.to_csv(combined_path, index=False)

    return combined_path