"""
Raw-file conversion for the icListen HF hydrophones (Ocean Sonics Lucy II).

Each leg's ACOUSTIC/HYDROPHONES folder mixes every hydrophone's files:
    LUF{serial}_{yyyymmdd}_{HHMMSS}.txt   5-min "Spectrum" logs (handled here)
    LUW{serial}_{yyyymmdd}_{HHMMSS}.wav   raw audio (not processed yet)

A spectrum txt is a header block ("Key <tab> value" lines under "File
Details:", "Device Details:", "Setup:") followed by "Data:" and a
tab-separated table: Time (HH:MM:SS only; the date is the header's Start
Date), Comment, Temperature, Humidity, Sequence #, Data Points, then one
column per frequency bin (header = bin frequency in Hz), one row per
averaged spectrum (every 30 s with the current setup).

Every txt becomes one per-file CSV with one row per spectrum:
  - the row's own fields and the file's header setup (serial, firmware,
    dB refs, sample rate, FFT size, bin width, ...), so a row is
    self-describing even when the setup changes between legs;
  - frequency-independent summaries (broadband, fixed band levels, peak
    frequency) that keep the same columns whatever bins a file has;
  - the full spectrum, one spec_{freq}Hz column per bin. Files with
    different bins still combine: the combined CSV holds the union of bins.

Levels are left in the file's own dB units (not converted to dB re 1 uPa);
db_ref_1v / db_ref_1upa are kept per row so that conversion can be done
later. Band/broadband levels are energetic sums over the bins in the band.
"""
import os
import re

import numpy as np
import pandas as pd

from DataPipeline.ingest.input_tools import update_csv
from DataPipeline.ingest.preprocessing import to_utc

HYDROPHONE_RAW_SUBFOLDER = "HYDROPHONES"
TXT_NAME_RE = re.compile(r"^LUF(?P<serial>\d+)_(?P<date>\d{8})_(?P<clock>\d{6})\.txt$", re.IGNORECASE)

# Header keys copied onto every row -> output column name
HEADER_FIELDS = {
    "S/N": "serial",
    "Device": "device",
    "Model": "model",
    "Firmware": "firmware",
    "File Version": "file_version",
    "dB Ref re 1V": "db_ref_1v",
    "dB Ref re 1uPa": "db_ref_1upa",
    "Sample Rate [S/s]": "sample_rate_hz",
    "FFT Size": "fft_size",
    "Bin Width [Hz]": "bin_width_hz",
    "Window Function": "window_function",
    "Overlap [%]": "overlap_pct",
    "Power Calculation": "power_calculation",
    "Accumulations": "accumulations",
}

# Row fields of the data table -> output column name
ROW_FIELDS = {
    "Comment": "comment",
    "Temperature": "temperature",
    "Humidity": "humidity",
    "Sequence #": "sequence",
    "Data Points": "data_points",
}

# Text columns: must not be coerced to numeric by data_process
TEXT_COLUMNS = ["device", "model", "firmware", "window_function", "power_calculation", "comment", "source_file"]

# Fixed bands (Hz, [low, high)) summarised from whatever bins a file has.
# The 0 Hz (DC) bin is left out of every summary.
BANDS = {
    "level_0.5-2kHz": (500.0, 2_000.0),
    "level_2-10kHz": (2_000.0, 10_000.0),
    "level_10-50kHz": (10_000.0, 50_000.0),
    "level_50-256kHz": (50_000.0, np.inf),
}
SPECTRUM_PREFIX = "spec_"


def spectrum_column(freq_hz: float) -> str:
    return f"{SPECTRUM_PREFIX}{freq_hz:g}Hz"


def spectrum_frequency(column: str) -> float:
    return float(column[len(SPECTRUM_PREFIX):-len("Hz")])


def _energetic_sum(levels_db: np.ndarray) -> np.ndarray:
    """10*log10(sum(10^(L/10))) along axis 1; NaN where every bin is NaN."""
    with np.errstate(divide="ignore"):
        power = np.nansum(np.power(10.0, levels_db / 10.0), axis=1)
        out = 10.0 * np.log10(power)
    out[np.all(np.isnan(levels_db), axis=1)] = np.nan
    return out


def parse_spectrum_txt(path: str) -> pd.DataFrame:
    """Parse one LUF*.txt spectrum log into one row per spectrum (see module docstring)."""
    header = {}
    table_lines = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if table_lines is not None:
                table_lines.append(line.rstrip("\r\n"))
            elif line.strip() == "Data:":
                table_lines = []
            elif "\t" in line:
                key, value = line.split("\t", 1)
                header[key.strip()] = value.strip()

    if not table_lines:
        raise ValueError("no 'Data:' table")

    columns = table_lines[0].split("\t")
    rows = [line.split("\t") for line in table_lines[1:] if line.strip()]
    if not rows:
        raise ValueError("'Data:' table has no rows")
    # Rows end with a trailing tab; a truncated last row is padded with NaN
    rows = [r[:len(columns)] + [""] * (len(columns) - len(r)) for r in rows]
    raw = pd.DataFrame(rows, columns=columns)
    raw = raw.mask(raw == "")
    raw = raw.loc[:, [c.strip() != "" for c in raw.columns]]

    # Everything after the known row fields is a frequency bin
    bin_cols = [c for c in raw.columns if c not in ("Time", *ROW_FIELDS)]
    freqs = np.array([float(c) for c in bin_cols])
    spectrum = raw[bin_cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)

    out = pd.DataFrame(index=raw.index)

    # Time of day + header Start Date; a file running past midnight wraps the
    # clock back, so every wrap adds a day.
    start_date = pd.Timestamp(header["Start Date"])
    clock = pd.to_timedelta(raw["Time"].str.strip(), errors="coerce")
    day_offset = (clock.diff() < pd.Timedelta(0)).cumsum()
    out["time"] = to_utc((start_date + clock + pd.to_timedelta(day_offset, unit="D")).astype(str))

    for key, col in HEADER_FIELDS.items():
        out[col] = header.get(key)
    for key, col in ROW_FIELDS.items():
        out[col] = raw[key] if key in raw.columns else np.nan

    positive = freqs > 0
    out["n_bins"] = len(freqs)
    out["f_min_hz"] = freqs.min() if len(freqs) else np.nan
    out["f_max_hz"] = freqs.max() if len(freqs) else np.nan
    out["level_broadband"] = _energetic_sum(spectrum[:, positive])
    for name, (low, high) in BANDS.items():
        in_band = (freqs >= low) & (freqs < high)
        out[name] = _energetic_sum(spectrum[:, in_band]) if in_band.any() else np.nan
    pos_spectrum = np.where(np.isnan(spectrum[:, positive]), -np.inf, spectrum[:, positive])
    out["peak_freq_hz"] = np.where(
        np.all(np.isnan(spectrum[:, positive]), axis=1), np.nan, freqs[positive][pos_spectrum.argmax(axis=1)]
    ) if positive.any() else np.nan

    out["source_file"] = os.path.basename(path)

    spec_df = pd.DataFrame(spectrum, columns=[spectrum_column(f) for f in freqs], index=raw.index)
    return pd.concat([out, spec_df], axis=1)


def list_spectrum_txts(input_folder: str) -> dict[str, list[str]]:
    """{serial: [LUF txt filenames]} for one leg's HYDROPHONES folder (one directory listing)."""
    by_serial: dict[str, list[str]] = {}
    with os.scandir(input_folder) as entries:
        for entry in entries:
            m = TXT_NAME_RE.match(entry.name)
            if m and entry.is_file():
                by_serial.setdefault(m.group("serial"), []).append(entry.name)
    return by_serial


def _order_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Summary/metadata columns first, then spectrum bins in increasing frequency."""
    spec = sorted((c for c in df.columns if c.startswith(SPECTRUM_PREFIX)), key=spectrum_frequency)
    other = [c for c in df.columns if not c.startswith(SPECTRUM_PREFIX)]
    return df[other + spec]


def ensure_hydrophone_combined_csv(
    input_folder: str,
    txt_names: list[str],
    csv_folder: str,
    combined_path: str,
) -> str | None:
    """
    Convert each stale txt (per-file CSV missing or older than the txt, the
    same per-file staleness rule as input_tools.ensure_combined_csv) into
    csv_folder, then (re)build combined_path from every per-file CSV there.
    The combined CSV is reused as-is when nothing was stale.

    txt_names may be empty (raw data removed from the raw share): the
    combined CSV is then reused, or rebuilt from the per-file CSVs.

    Returns combined_path, or None if there is nothing to combine.
    """
    os.makedirs(csv_folder, exist_ok=True)

    stale = []
    for name in txt_names:
        csv_path = os.path.join(csv_folder, os.path.splitext(name)[0] + ".csv")
        if not os.path.exists(csv_path) or os.path.getmtime(os.path.join(input_folder, name)) > os.path.getmtime(csv_path):
            stale.append(name)

    if os.path.exists(combined_path) and not stale:
        return combined_path

    n_failed = 0
    for i, name in enumerate(sorted(stale), 1):
        try:
            df = parse_spectrum_txt(os.path.join(input_folder, name))
        except Exception as exc:
            n_failed += 1
            print(f"      [WARN] Skipping {name}: {exc}")
            continue
        update_csv(df, os.path.join(csv_folder, os.path.splitext(name)[0] + ".csv"))
        if i % 500 == 0:
            print(f"      converted {i}/{len(stale)} txt files")
    print(f"      converted {len(stale) - n_failed}/{len(stale)} stale txt file(s)")

    frames = []
    for name in sorted(os.listdir(csv_folder)):
        if not name.lower().endswith(".csv"):
            continue
        df = pd.read_csv(os.path.join(csv_folder, name), low_memory=False)
        if df.empty:
            continue
        df["time"] = to_utc(df["time"])  # parse per frame, before concatenating
        frames.append(df)

    if not frames:
        return None

    combined = _order_columns(pd.concat(frames, ignore_index=True))
    combined = combined.sort_values("time", kind="mergesort", na_position="last")
    update_csv(combined, combined_path)
    return combined_path
