"""
Hydrophone wav (LUW*.wav) anomaly detection, one source per serial
(Hydrophone_{serial}_anomalies). The detector itself (PANNs embeddings +
DBSCAN + ping/label filtering + temporal grouping) is in
hydrophone_anomalies.py; this module is the per-leg loop around it.

Raw files: each leg's ACOUSTIC/HYDROPHONES folder (LUW{serial}_{yyyymmdd}_{HHMMSS}.wav,
~5-min recordings per hydrophone from mid LEG4 on). Early legs (1-4) also have
an internal-memory export folder (SJW{serial}_... names); both are scanned and a
recording present in both is taken from HYDROPHONES. The filename timestamp is
taken as the recording's UTC start time.

Per leg and hydrophone, under processed_data/{cruise}/LEG{leg}/ACOUSTIC/:
    Hydrophone_{serial}_anomalies/
        per_file_logs/{wav stem}.csv   one row per candidate chunk (KEPT/DISCARDED + reason);
                                       header-only when a file had none. Also the
                                       "already processed" marker: a wav is only
                                       (re)analysed when its log is missing or older.
        standard_plots/, high_quality_plots/    zoomed event spectrograms
        audio_clips/, high_quality_clips/       event wav clips with context
    {cruise}_LEG{leg}_ACOUSTIC_Hydrophone_{serial}_anomalies_log.csv
        every per-file log concatenated
    {cruise}_LEG{leg}_ACOUSTIC_Hydrophone_{serial}_anomalies_events.csv
        one row per event (start/end UTC, labels, loudness, clip/plot paths
        relative to the anomalies folder), geotagged against the leg's
        GPS-MERGED-SOURCES table
The two leg tables are rebuilt only when a file was (re)analysed or they're missing.

These are event tables, not time series, so they're deliberately not in
vocabulary.INSTRUMENTS: combine / gap analysis / plotting don't pick them up.
"""
import os
import re
import traceback

import pandas as pd

from DataPipeline.ingest.input_tools import input_folders_processer, update_csv
from DataPipeline.ingest.preprocessing import format_time, to_utc
from DataPipeline.sensors.data_processing_sensors import add_gps_coordinates_from_df
from DataPipeline.sensors.gps_gap_fill import load_existing_gps
from DataPipeline.settings import CRUISE, HYDROPHONE_ANOMALY_CONFIG, LEG
from DataPipeline.vocabulary import HYDROPHONE_SERIALS, LEGS

EXPERIMENT = "ACOUSTIC"
ANOMALY_SUFFIX = "_anomalies"
# Scanned in this order; the first folder holding a recording wins
WAV_RAW_SUBFOLDERS = ["HYDROPHONES", "Hydrophones_InternalMemoryExport_NotAllFiles"]
WAV_NAME_RE = re.compile(
    r"^(?P<prefix>LUW|SJW)(?P<serial>\d+)_(?P<date>\d{8})_(?P<clock>\d{6})\.wav$", re.IGNORECASE
)
PER_FILE_LOG_SUBFOLDER = "per_file_logs"
# Columns of the candidate log (one row per DBSCAN noise chunk, see hydrophone_anomalies.analyse_file)
LOG_COLUMNS = [
    "time", "source_file", "serial", "chunk_offset_s", "status", "reason",
    "top_prediction", "confidence", "top_3", "loudness_ratio",
    "event_id", "high_quality", "clip_path", "plot_path",
]


def anomaly_instrument(serial):
    return f"Hydrophone_{serial}{ANOMALY_SUFFIX}"


def list_wavs(cruise, leg):
    """{serial: [(wav path, start time string yyyymmddHHMMSS)]} for one leg, sorted by start time."""
    found = {}
    for subfolder in WAV_RAW_SUBFOLDERS:
        folder = input_folders_processer(leg, EXPERIMENT, subfolder, cruise=cruise)[0]
        if not os.path.isdir(folder):
            continue
        with os.scandir(folder) as entries:
            for entry in entries:
                m = WAV_NAME_RE.match(entry.name)
                if not m or not entry.is_file():
                    continue
                key = (m.group("serial"), m.group("date") + m.group("clock"))
                found.setdefault(key, entry.path)
    by_serial = {}
    for (serial, start), path in sorted(found.items()):
        by_serial.setdefault(serial, []).append((path, start))
    return by_serial


def _per_file_log_path(log_folder, wav_path):
    return os.path.join(log_folder, os.path.splitext(os.path.basename(wav_path))[0] + ".csv")


def _is_stale(wav_path, log_path):
    return not os.path.exists(log_path) or os.path.getmtime(wav_path) > os.path.getmtime(log_path)


def _events_from_log(log_df):
    """One row per event from the KEPT rows of the candidate log."""
    kept = log_df[log_df["status"] == "KEPT"]
    if kept.empty:
        return pd.DataFrame(columns=[
            "time", "end_time", "duration_s", "event_id", "source_file", "serial", "n_anomalies",
            "high_quality", "max_loudness_ratio", "top_prediction", "labels", "clip_path", "plot_path",
        ])

    def summarise(g):
        best = g.loc[g["confidence"].idxmax()]
        # unique main labels, each with its highest confidence in the event
        labels = g.groupby("top_prediction")["confidence"].max().sort_values(ascending=False)
        return pd.Series({
            "time": g["time"].min(),
            "end_time": g["time"].max() + pd.Timedelta(seconds=HYDROPHONE_ANOMALY_CONFIG["chunk_seconds"]),
            "source_file": g["source_file"].iloc[0],
            "serial": g["serial"].iloc[0],
            "n_anomalies": len(g),
            "high_quality": bool(g["high_quality"].astype(str).str.lower().eq("true").any()),
            "max_loudness_ratio": g["loudness_ratio"].max(),
            "top_prediction": best["top_prediction"],
            "labels": "; ".join(f"{label}:{conf:.2f}" for label, conf in labels.items()),
            "clip_path": g["clip_path"].iloc[0],
            "plot_path": g["plot_path"].iloc[0],
        })

    events = kept.groupby("event_id", sort=False).apply(summarise, include_groups=False).reset_index()
    events["duration_s"] = (events["end_time"] - events["time"]).dt.total_seconds()
    events = events.sort_values("time", kind="mergesort")
    first = ["time", "end_time", "duration_s", "event_id"]
    return events[first + [c for c in events.columns if c not in first]]


def _rebuild_leg_tables(log_folder, log_path, events_path, gga_df):
    frames = []
    for name in sorted(os.listdir(log_folder)):
        if not name.lower().endswith(".csv"):
            continue
        df = pd.read_csv(os.path.join(log_folder, name))
        if df.empty:
            continue
        df["time"] = to_utc(df["time"])  # parse per frame, before concatenating
        frames.append(df)

    if frames:
        log_df = pd.concat(frames, ignore_index=True).sort_values("time", kind="mergesort")
    else:
        log_df = pd.DataFrame(columns=LOG_COLUMNS)
    update_csv(log_df, log_path)

    events = _events_from_log(log_df)
    if not events.empty:
        if gga_df is not None:
            events = add_gps_coordinates_from_df(events, gga_df)
        events["end_time"] = format_time(events["end_time"])
    update_csv(events, events_path)
    return len(events)


def run_processing_hydrophone_wav(cruise, leg=None, serials=None, cfg=None):
    """Anomaly detection on every hydrophone's wav files for the given leg(s) (None = all legs)."""
    if cruise is None:
        raise ValueError("run_processing_hydrophone_wav requires cruise to be provided.")

    legs = LEGS if leg is None else (leg if isinstance(leg, (list, tuple)) else [leg])
    serials = HYDROPHONE_SERIALS if serials is None else serials
    cfg = HYDROPHONE_ANOMALY_CONFIG if cfg is None else cfg
    model = class_labels = None  # loaded on the first file that needs analysing

    for current_leg in legs:
        wavs_by_serial = list_wavs(cruise, current_leg)
        header_printed = False

        for serial in serials:
            instrument = anomaly_instrument(serial)
            (
                _input_folder_name,
                output_folder,
                exp_folder_name,
                _fig_png_folder_name,
                _fig_pdf_folder_name,
                _cleaned_output_file,
                _output_file,
                base_name,
            ) = input_folders_processer(current_leg, EXPERIMENT, instrument, cruise=cruise)
            log_folder = os.path.join(output_folder, PER_FILE_LOG_SUBFOLDER)
            leg_log_path = os.path.join(exp_folder_name, f"{base_name}_log.csv")
            events_path = os.path.join(exp_folder_name, f"{base_name}_events.csv")

            wavs = wavs_by_serial.get(serial, [])
            if not wavs and not os.path.isdir(log_folder):
                continue

            if not header_printed:
                print(f"\n{'=' * 80}")
                print(f"                 HYDROPHONE WAV ANOMALIES: {cruise} - LEG {current_leg}")
                print(f"{'=' * 80}")
                header_printed = True

            stale = [(p, s) for p, s in wavs if _is_stale(p, _per_file_log_path(log_folder, p))]
            print(f"   PROCESSING LEG {current_leg}: {instrument} "
                  f"({len(wavs)} wav files, {len(stale)} to analyse)")

            try:
                if stale:
                    if model is None:
                        from DataPipeline.acoustics.hydrophone_anomalies import load_panns_model
                        model, class_labels = load_panns_model()
                    from DataPipeline.acoustics.hydrophone_anomalies import OUTPUT_SUBFOLDERS, analyse_file
                    for sub in [PER_FILE_LOG_SUBFOLDER, *OUTPUT_SUBFOLDERS]:
                        os.makedirs(os.path.join(output_folder, sub), exist_ok=True)

                    n_failed = 0
                    for i, (wav_path, start) in enumerate(stale, 1):
                        print(f"      [{i}/{len(stale)}] {os.path.basename(wav_path)}")
                        try:
                            file_start = to_utc(pd.Series([start])).iloc[0]
                            rows = analyse_file(wav_path, file_start, serial, model, class_labels, cfg, output_folder)
                        except Exception as exc:
                            # no per-file log written, so the file is retried next run
                            n_failed += 1
                            print(f"      [WARN] Failed analysing {os.path.basename(wav_path)}: {exc}")
                            continue
                        update_csv(pd.DataFrame(rows, columns=LOG_COLUMNS), _per_file_log_path(log_folder, wav_path))
                    print(f"      analysed {len(stale) - n_failed}/{len(stale)} wav file(s)")

                if (stale or not os.path.exists(events_path)) and os.path.isdir(log_folder):
                    gga_df, _gps_path = load_existing_gps(cruise, current_leg)
                    n_events = _rebuild_leg_tables(log_folder, leg_log_path, events_path, gga_df)
                    print(f"      [OK] LEG {current_leg}: {instrument}: {n_events} event(s) -> {os.path.basename(events_path)}")
                else:
                    print(f"      [OK] LEG {current_leg}: {instrument}: nothing new")
            except Exception as exc:
                print(f"      [ERROR] Failed processing LEG {current_leg}: {instrument}\n{exc}")
                traceback.print_exc()


if __name__ == "__main__":
    run_processing_hydrophone_wav(cruise=CRUISE, leg=LEG)
