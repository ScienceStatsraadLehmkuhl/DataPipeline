import os

# HDF5 tries to take POSIX byte-range locks on every file it opens. The
# gvfs/FUSE SMB mounts this pipeline writes to don't support that reliably,
# which surfaces as intermittent "OSError: [Errno -101] NetCDF: HDF error"
# part-way through a run. Must be set before netCDF4/xarray/echopype touch
# the HDF5 library.
os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")

import traceback
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import numpy as np
import xarray as xr
from echopype.convert.utils.ek_raw_io import RawSimradFile


RELEVANT_INPUT_EXTS_EK80_ADCP = (".raw",)

# Subfolder of the leg's ACOUSTIC processed folder holding the ADCP outputs.
EK80_ADCP_OUTPUT_SUBFOLDER = "EK80_adcp_ncdf"

# Empty marker written next to the ADCP netCDFs for a raw file that has no
# ADCP channel, so that file isn't re-read on every run just to find nothing.
_NO_ADCP_MARKER_SUFFIX = "_ADCP.none"

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


@contextmanager
def exclude_ek80_adcp_channels(adcp_collector=None):
    """
    Make echopype's EK80 parser ignore any ADCP channel embedded in the raw
    file being opened underneath this context, so ep.open_raw() succeeds and
    the resulting EchoData only contains the real echosounder channels.

    Use this around any ep.open_raw(..., sonar_model="EK80") call on files
    that may contain an embedded ADCP (see module docstring for why it's
    otherwise unsupported).

    If an EK80ADCPCollector is given, every datagram echopype reads is also
    fed to it (before the ADCP channel ids are relabeled), so the ADCP data
    comes out of the same single pass over the raw file instead of a second
    read with _extract_ek80_adcp_ping_data().
    """

    class _EK80ADCPExcludingRawFile(RawSimradFile):
        """RawSimradFile that hides ADCP channels from echopype's EK80 parser."""

        def read(self, k):
            dgram = super().read(k)
            if adcp_collector is not None:
                for d in dgram if isinstance(dgram, list) else [dgram]:
                    adcp_collector.add(d)
            return _relabel_ek80_adcp_for_exclusion(dgram)

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


class EK80ADCPCollector:
    """
    Collect the ADCP beam channels' raw complex samples plus their per-ping
    instrument parameters from a stream of datagrams, in file order.

    Fed either by the standalone walk in _extract_ek80_adcp_ping_data() or,
    to avoid reading the raw file twice, by echopype's own parse through
    exclude_ek80_adcp_channels(adcp_collector=...).
    """

    def __init__(self):
        self.beam_channel_ids = []
        self.ping_time = defaultdict(list)
        self.complex_samples = defaultdict(list)
        self.param_fields = defaultdict(lambda: defaultdict(list))
        self.device_config = {}
        self._current_params = {}

    def add(self, dg):
        if not isinstance(dg, dict):
            return

        # The configuration datagram (first in the file)
        if "configuration" in dg:
            for ch_id, ch_config in dg["configuration"].items():
                if is_ek80_adcp_channel(ch_id):
                    self.device_config[ch_id] = ch_config
            return

        dg_type = dg.get("type", "")

        if dg_type.startswith("XML") and dg.get("subtype") == "parameter":
            param = dg["parameter"]
            if is_ek80_adcp_channel(param.get("channel_id")):
                self._current_params[param["channel_id"]] = dict(param)
            return

        if not dg_type.startswith("RAW3"):
            return

        channel_id = dg.get("channel_id", "")
        if not is_ek80_adcp_channel(channel_id):
            return

        complex_arr = dg.get("complex")
        if complex_arr is None:
            return  # unexpected: ADCP RAW3 datagram without complex samples

        if channel_id not in self.beam_channel_ids:
            self.beam_channel_ids.append(channel_id)

        self.ping_time[channel_id].append(dg["timestamp"].replace(tzinfo=None))
        self.complex_samples[channel_id].append(complex_arr[:, 0])

        params = self._current_params.get(channel_id, {})
        for name in _EK80_ADCP_PARAM_NAMES:
            self.param_fields[channel_id][name].append(params.get(name))

    def result(self):
        """The collected data, or None if the file had no ADCP channel."""
        if not self.beam_channel_ids:
            return None

        return {
            "beam_channel_ids": sorted(self.beam_channel_ids),
            "ping_time": self.ping_time,
            "complex_samples": self.complex_samples,
            "param_fields": self.param_fields,
            "device_config": self.device_config,
        }


def _extract_ek80_adcp_ping_data(raw_file_path: str):
    """
    Walk a raw file's datagrams directly and collect the ADCP beam channels'
    raw complex samples plus their per-ping instrument parameters.

    Returns None if the file has no ADCP channel.
    """
    collector = EK80ADCPCollector()

    with RawSimradFile(raw_file_path, "r") as fid:
        collector.add(fid.read(1))  # configuration datagram; an unreadable file raises here
        while True:
            try:
                dg = fid.read(1)
            except Exception:
                break  # SimradEOF (or any other read failure at end of stream)
            collector.add(dg)

    return collector.result()


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


def write_ek80_adcp_output(
    extracted: dict | None, raw_file_path: str, output_folder_name: str, sonar_model: str = "EK80"
) -> str | None:
    """
    Write one raw file's extracted ADCP data (from EK80ADCPCollector.result())
    to output_folder_name as "<raw_filename>_ADCP.nc", or, if the file has no
    ADCP channel, an empty "<raw_filename>_ADCP.none" marker so later runs
    don't re-read it.

    Returns the netCDF path, or None if the file has no ADCP channel.
    """
    os.makedirs(output_folder_name, exist_ok=True)
    raw_filename = Path(raw_file_path).stem
    marker_path = os.path.join(output_folder_name, f"{raw_filename}{_NO_ADCP_MARKER_SUFFIX}")

    if extracted is None:
        open(marker_path, "w").close()
        print(f"      [SKIP] No ADCP channel found in {os.path.basename(raw_file_path)}")
        return None

    ds = _build_ek80_adcp_dataset(extracted, raw_filename, sonar_model)
    nc_path = os.path.join(output_folder_name, f"{raw_filename}_ADCP.nc")
    ds.to_netcdf(nc_path)
    if os.path.exists(marker_path):
        os.remove(marker_path)  # the raw file changed and now has ADCP data
    print(f"      [OK] ADCP channels extracted: {os.path.basename(raw_file_path)} -> {os.path.basename(nc_path)}")
    return nc_path


def process_ek80_adcp_raw_file(raw_file_path: str, output_folder_name: str, sonar_model: str = "EK80") -> str | None:
    """
    Extract a single raw file's ADCP channels (if any) into their own netCDF,
    with its own read of the raw file. See write_ek80_adcp_output().
    """
    extracted = _extract_ek80_adcp_ping_data(raw_file_path)
    return write_ek80_adcp_output(extracted, raw_file_path, output_folder_name, sonar_model=sonar_model)


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


def stale_ek80_adcp_raw_files(input_folder_name, output_folder_name):
    """
    Raw files with neither an ADCP netCDF nor a no-ADCP marker yet, or newer
    than the one they have.
    """
    existing = set(os.listdir(output_folder_name)) if os.path.isdir(output_folder_name) else set()
    stale = []
    for f in os.listdir(input_folder_name):
        if not f.lower().endswith(RELEVANT_INPUT_EXTS_EK80_ADCP):
            continue
        stem = Path(f).stem
        outputs = [
            os.path.join(output_folder_name, name)
            for name in (f"{stem}_ADCP.nc", f"{stem}{_NO_ADCP_MARKER_SUFFIX}")
            if name in existing
        ]
        raw_mtime = os.path.getmtime(os.path.join(input_folder_name, f))
        if not any(os.path.getmtime(p) >= raw_mtime for p in outputs):
            stale.append(f)
    return stale


def ensure_ek80_adcp_netcdfs(input_folder_name: str, output_folder_name: str, sonar_model: str = "EK80") -> list:
    """
    Ensure every .raw file's ADCP channels have an up-to-date, standalone
    netCDF extracted -- the ADCP-only path. When the echosounder is processed
    too, ensure_ek80_echosounder_combined_csv does this in the same read of
    each raw file instead, and this finds nothing left to do.

    Same staleness philosophy as ensure_ek80_echosounder_combined_csv: only raw files
    that are new, or newer than their existing ADCP netCDF / no-ADCP marker,
    are reparsed. Files with no ADCP channel get a marker and no netCDF.
    """
    if not input_folder_name or not os.path.isdir(input_folder_name):
        # Raw data removed: the per-file netCDFs already written are the
        # final output, so there is nothing left to do.
        print(f"      [SKIP] EK80 input folder not found: {input_folder_name}; keeping existing ADCP outputs")
        return []

    os.makedirs(output_folder_name, exist_ok=True)
    stale_files = stale_ek80_adcp_raw_files(input_folder_name, output_folder_name)

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

    return written
