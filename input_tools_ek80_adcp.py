import os
import traceback
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import numpy as np
import xarray as xr
from echopype.convert.utils.ek_raw_io import RawSimradFile


RELEVANT_INPUT_EXTS_EK80_ADCP = (".raw",)

# Kongsberg/Simrad EK80 raw files can bundle a wideband ADCP (e.g. a CP300)
# as extra channels riding on the same RAW3 (backscatter)/FIL1 (filter
# coefficient) datagrams as the real echosounder channels. echopype has no
# support for this: its channel bookkeeping assumes one config-datagram
# entry per data channel, but the ADCP is configured as ONE device
# ("...CP300_ADCP") while its ping data arrives under FOUR per-beam channel
# ids ("...CP300_ADCP#ADCP-00" .. "#ADCP-03"). That mismatch makes
# ep.open_raw() raise a KeyError while building the Platform group.
EK80_ADCP_CHANNEL_HINT = "ADCP"

# The literal substring echopype's own EK80 parser (echopype/convert/parse_base.py)
# already uses to drop a non-conforming channel -- originally written for a
# different Kongsberg ADCP model (EC150) -- from both the config datagram and
# all RAW3/RAW4/FIL datagram handling. Relabeling our channel ids to contain
# this token reuses that existing, tested exclusion path instead of
# reimplementing echopype's internal parsing/dispatch logic.
_EK80_EXCLUSION_TOKEN = "EC150"


def is_ek80_adcp_channel(channel_id) -> bool:
    return isinstance(channel_id, str) and EK80_ADCP_CHANNEL_HINT in channel_id


def _relabel_ek80_adcp_for_exclusion(dgram):
    """Rename ADCP channel ids in-place so echopype's EC150 exclusion path drops them."""
    if isinstance(dgram, list):
        for d in dgram:
            _relabel_ek80_adcp_for_exclusion(d)
        return dgram
    if not isinstance(dgram, dict):
        return dgram

    channel_id = dgram.get("channel_id")
    if is_ek80_adcp_channel(channel_id) and _EK80_EXCLUSION_TOKEN not in channel_id:
        dgram["channel_id"] = f"{_EK80_EXCLUSION_TOKEN}_{channel_id}"

    configuration = dgram.get("configuration")
    if isinstance(configuration, dict):
        for key in list(configuration.keys()):
            if is_ek80_adcp_channel(key) and _EK80_EXCLUSION_TOKEN not in key:
                configuration[f"{_EK80_EXCLUSION_TOKEN}_{key}"] = configuration.pop(key)

    return dgram


class _EK80ADCPExcludingRawFile(RawSimradFile):
    """RawSimradFile that hides ADCP channels from echopype's EK80 parser."""

    def read(self, k):
        return _relabel_ek80_adcp_for_exclusion(super().read(k))


@contextmanager
def exclude_ek80_adcp_channels():
    """
    Make echopype's EK80 parser ignore any ADCP channel embedded in the raw
    file being opened underneath this context, so ep.open_raw() succeeds and
    the resulting EchoData only contains the real echosounder channels.

    Use this around any ep.open_raw(..., sonar_model="EK80") call on files
    that may contain an embedded ADCP (see module docstring for why it's
    otherwise unsupported).
    """
    with patch("echopype.convert.parse_base.RawSimradFile", _EK80ADCPExcludingRawFile):
        yield


# ---------------------------------------------------------------------------
# Raw-datagram-level ADCP extraction (bypasses echopype's EK80 parser, which
# cannot represent this channel type at all)
# ---------------------------------------------------------------------------

# Per-ping ADCP instrument parameters carried in the XML0 "parameter"
# datagram that precedes each beam's RAW3 datagram.
_EK80_ADCP_PARAM_NAMES = (
    "sample_interval",
    "sound_velocity",
    "depth_cell_size",
    "pulse_duration",
    "transmit_power",
    "slope",
    "frequency",
    "maximum_current_speed",
    "maximum_vessel_speed",
    "channel_mode",
    "pulse_form",
)


def _extract_ek80_adcp_ping_data(raw_file_path: str):
    """
    Walk a raw file's datagrams directly and collect the ADCP beam channels'
    raw complex samples plus their per-ping instrument parameters.

    Returns None if the file has no ADCP channel.
    """
    beam_channel_ids = []
    ping_time = defaultdict(list)
    complex_samples = defaultdict(list)
    param_fields = defaultdict(lambda: defaultdict(list))
    device_config = {}
    current_params = {}

    with RawSimradFile(raw_file_path, "r") as fid:
        config_datagram = fid.read(1)
        for ch_id, ch_config in config_datagram.get("configuration", {}).items():
            if is_ek80_adcp_channel(ch_id):
                device_config[ch_id] = ch_config

        while True:
            try:
                dg = fid.read(1)
            except Exception:
                break  # SimradEOF (or any other read failure at end of stream)

            dg_type = dg.get("type", "")

            if dg_type.startswith("XML") and dg.get("subtype") == "parameter":
                param = dg["parameter"]
                if is_ek80_adcp_channel(param.get("channel_id")):
                    current_params[param["channel_id"]] = param
                continue

            if not dg_type.startswith("RAW3"):
                continue

            channel_id = dg.get("channel_id", "")
            if not is_ek80_adcp_channel(channel_id):
                continue

            complex_arr = dg.get("complex")
            if complex_arr is None:
                continue  # unexpected: ADCP RAW3 datagram without complex samples

            if channel_id not in beam_channel_ids:
                beam_channel_ids.append(channel_id)

            ping_time[channel_id].append(dg["timestamp"].replace(tzinfo=None))
            complex_samples[channel_id].append(complex_arr[:, 0])

            params = current_params.get(channel_id, {})
            for name in _EK80_ADCP_PARAM_NAMES:
                param_fields[channel_id][name].append(params.get(name))

    if not beam_channel_ids:
        return None

    return {
        "beam_channel_ids": sorted(beam_channel_ids),
        "ping_time": ping_time,
        "complex_samples": complex_samples,
        "param_fields": param_fields,
        "device_config": device_config,
    }


def _pad_to_length(arr: np.ndarray, length: int) -> np.ndarray:
    """Pad a 1-D complex sample array with NaN out to `length` range samples."""
    if arr.shape[0] == length:
        return arr
    padded = np.full(length, np.nan, dtype=arr.dtype)
    padded[: arr.shape[0]] = arr
    return padded


def _build_ek80_adcp_dataset(extracted: dict, raw_filename: str, sonar_model: str) -> xr.Dataset:
    beam_channel_ids = extracted["beam_channel_ids"]

    # The CP300's per-ping range-sample count can shift by a sample or two
    # within a file (e.g. auto range-gating) -- pad every ping out to the
    # longest range seen anywhere in the file rather than assuming a fixed grid.
    n_range = max(
        sample.shape[0]
        for ch in beam_channel_ids
        for sample in extracted["complex_samples"][ch]
    )

    ping_times_by_beam = [extracted["ping_time"][ch] for ch in beam_channel_ids]
    n_pings_values = {len(t) for t in ping_times_by_beam}
    if len(n_pings_values) != 1:
        raise ValueError(
            f"{raw_filename}: ADCP beams have differing ping counts "
            f"{[len(t) for t in ping_times_by_beam]}; beams are expected to ping in lock-step."
        )

    ping_time = np.array(ping_times_by_beam[0], dtype="datetime64[ns]")
    for beam_idx, times in enumerate(ping_times_by_beam[1:], start=1):
        if not np.array_equal(np.array(times, dtype="datetime64[ns]"), ping_time):
            raise ValueError(
                f"{raw_filename}: ping timestamps differ between ADCP beam 0 and beam {beam_idx}."
            )

    complex_data = np.stack(
        [
            np.stack([_pad_to_length(sample, n_range) for sample in extracted["complex_samples"][ch]])
            for ch in beam_channel_ids
        ]
    )  # (beam, ping_time, range_sample), complex64; NaN-padded where a ping was shorter

    data_vars = {
        "backscatter_r": (
            ["beam", "ping_time", "range_sample"],
            complex_data.real.astype("float32"),
            {"long_name": "Real part of raw ADCP backscatter (uncalibrated)"},
        ),
        "backscatter_i": (
            ["beam", "ping_time", "range_sample"],
            complex_data.imag.astype("float32"),
            {"long_name": "Imaginary part of raw ADCP backscatter (uncalibrated)"},
        ),
    }

    for name in _EK80_ADCP_PARAM_NAMES:
        values = np.array([extracted["param_fields"][ch][name] for ch in beam_channel_ids])
        try:
            values = values.astype("float64")
        except (TypeError, ValueError):
            values = values.astype(str)
        data_vars[name] = (["beam", "ping_time"], values)

    beam_coord = [ch.split("#")[-1] if "#" in ch else ch for ch in beam_channel_ids]

    ds = xr.Dataset(
        data_vars,
        coords={
            "beam": beam_coord,
            "ping_time": ping_time,
            "range_sample": np.arange(n_range),
        },
    )
    ds["range_sample"].attrs["long_name"] = "Range sample index (raw, uncalibrated)"

    device_config = next(iter(extracted["device_config"].values()), {})
    ds.attrs.update(
        {
            "instrument": "ADCP",
            "sonar_model": sonar_model,
            "source_raw_file": raw_filename,
            "adcp_transceiver_name": device_config.get("transceiver_name", ""),
            "adcp_serial_number": str(device_config.get("serial_number", "")),
            "adcp_ip_address": device_config.get("ip_address", ""),
            "beam_channel_ids": ", ".join(beam_channel_ids),
            "comment": (
                "Raw per-beam complex backscatter samples from a wideband ADCP "
                "embedded in an EK80 raw file (e.g. Simrad CP300). No calibration "
                "or current-vector processing has been applied -- these are the "
                "unprocessed quadrature samples per range bin per ping."
            ),
        }
    )
    return ds


def process_ek80_adcp_raw_file(raw_file_path: str, output_folder_name: str, sonar_model: str = "EK80") -> str | None:
    """
    Extract a single raw file's ADCP channels (if any) into their own netCDF,
    written to output_folder_name as "<raw_filename>_ADCP.nc".

    Returns the netCDF path, or None if the file has no ADCP channel.
    """
    os.makedirs(output_folder_name, exist_ok=True)
    raw_filename = Path(raw_file_path).stem

    extracted = _extract_ek80_adcp_ping_data(raw_file_path)
    if extracted is None:
        return None

    ds = _build_ek80_adcp_dataset(extracted, raw_filename, sonar_model)
    nc_path = os.path.join(output_folder_name, f"{raw_filename}_ADCP.nc")
    ds.to_netcdf(nc_path)
    return nc_path


# ---------------------------------------------------------------------------
# Leg-level staleness handling
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
    """Raw files with no matching ADCP netCDF yet, or newer than their netCDF."""
    stale = []
    for f in os.listdir(input_folder_name):
        if not f.lower().endswith(exts):
            continue
        raw_path = os.path.join(input_folder_name, f)
        nc_path = os.path.join(output_folder_name, f"{Path(f).stem}_ADCP.nc")
        if not os.path.exists(nc_path) or os.path.getmtime(raw_path) > os.path.getmtime(nc_path):
            stale.append(f)
    return stale


def ensure_ek80_adcp_netcdfs(input_folder_name: str, output_folder_name: str, sonar_model: str = "EK80") -> list:
    """
    Ensure every .raw file's ADCP channels have an up-to-date, standalone
    netCDF extracted -- independent of the main EK80 Sv processing, which
    cannot represent these channels (see exclude_ek80_adcp_channels()).

    Same staleness philosophy as ensure_ek80_echosounder_combined_csv: only raw files
    that are new, or newer than their existing ADCP netCDF, are reparsed.
    Files with no ADCP channel are skipped and produce no netCDF.
    """
    if not input_folder_name or not os.path.isdir(input_folder_name):
        raise FileNotFoundError(f"EK80 input folder not found: {input_folder_name}")

    os.makedirs(output_folder_name, exist_ok=True)
    stale_files = _stale_raw_files(input_folder_name, output_folder_name, RELEVANT_INPUT_EXTS_EK80_ADCP)

    written = []
    for filename in stale_files:
        raw_path = os.path.join(input_folder_name, filename)
        try:
            nc_path = process_ek80_adcp_raw_file(raw_path, output_folder_name, sonar_model=sonar_model)
        except Exception:
            traceback.print_exc()
            raise
        if nc_path:
            written.append(nc_path)
            print(f"      [OK] ADCP channels extracted: {filename} -> {os.path.basename(nc_path)}")
        else:
            print(f"      [SKIP] No ADCP channel found in {filename}")

    return written
