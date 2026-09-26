"""
Acoustic anomaly detection in one hydrophone wav recording (LUW*.wav),
ported from OtherProjects/HydrophoneAnomalies/Hydrophones_Old/PANNsClustering.ipynb.

Per file:
  1. load -> mono -> resample to target_sample_rate -> high-pass filter;
  2. cut into chunk_seconds chunks; PANNs Cnn14 (AudioSet tagger) gives each
     chunk an embedding and a clipwise label probability vector;
  3. DBSCAN on the embeddings: chunks left as noise (-1) are the candidate
     anomalies, i.e. "unlike the rest of this recording" (clustering is per
     file, as in the notebook);
  4. a candidate is discarded if it looks like an EK80/ADCP ping (energy
     ratio or dominant peak at the aliased instrument frequencies) or if its
     top PANNs label is in avoid_labels;
  5. surviving candidates closer than temporal_grouping_seconds form an
     event; events with fewer than min_anomalies_in_group candidates are
     discarded as isolated;
  6. each event gets a zoomed mel spectrogram (PNG) and a wav clip with
     context, filed as high-quality when any of its chunks has
     loudness_ratio (chunk RMS / file RMS) >= loudness_ratio_threshold.

analyse_file() returns one log row per candidate (KEPT or DISCARDED, with
the reason), so the caller can write per-file and per-leg tables.

torch / torchaudio / librosa / panns_inference / sklearn are only imported
here, so the rest of the pipeline still imports without them.
"""
import gc
import os

import librosa
import librosa.display
import matplotlib

matplotlib.use("Agg")  # non-interactive backend: no figure windows, no memory leak
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import soundfile as sf
import torch
import torchaudio
from scipy.signal import butter, sosfilt
from sklearn.cluster import DBSCAN

# Output subfolders under each hydrophone's anomalies folder
AUDIO_CLIPS_SUBFOLDER = "audio_clips"
HQ_CLIPS_SUBFOLDER = "high_quality_clips"
STD_PLOTS_SUBFOLDER = "standard_plots"
HQ_PLOTS_SUBFOLDER = "high_quality_plots"
OUTPUT_SUBFOLDERS = [AUDIO_CLIPS_SUBFOLDER, HQ_CLIPS_SUBFOLDER, STD_PLOTS_SUBFOLDER, HQ_PLOTS_SUBFOLDER]


# --- Helper functions ---

def high_pass_filter(data, cutoff, fs, order=5):
    """Applies a high-pass filter to the data."""
    normal_cutoff = cutoff / (0.5 * fs)
    if normal_cutoff >= 1.0:
        return data
    sos = butter(order, normal_cutoff, btype="high", analog=False, output="sos")
    return sosfilt(sos, data)


def calculate_aliased_frequency(original_freq, sample_rate):
    """Calculates the aliased frequency after downsampling."""
    nyquist = sample_rate / 2
    while original_freq > nyquist:
        original_freq = abs(original_freq - sample_rate)
    return original_freq


def is_instrument_ping_energy(chunk, sr, unwanted_freqs, tolerance_hz, energy_threshold):
    """Checks if a chunk's energy is concentrated in unwanted frequency bands."""
    if np.sum(np.abs(chunk)) == 0:
        return False
    spectrum = np.abs(np.fft.rfft(chunk))
    freq_axis = np.fft.rfftfreq(len(chunk), 1 / sr)
    total_energy = np.sum(spectrum ** 2)
    if total_energy == 0:
        return False
    ping_energy = 0
    for freq_hz in unwanted_freqs:
        band_mask = (freq_axis >= freq_hz - tolerance_hz) & (freq_axis <= freq_hz + tolerance_hz)
        ping_energy += np.sum(spectrum[band_mask] ** 2)
    return ping_energy / total_energy > energy_threshold


def is_instrument_ping_peak(chunk, sr, aliased_freqs, freq_tolerance, peak_ratio_threshold):
    """
    Detects an instrument ping by looking for a dominant spectral peak in the
    instrument frequency bands: a peak more than peak_ratio_threshold times the
    spectrum's mean magnitude is characteristic of the narrow-band pings of
    echosounders and ADCPs.
    """
    if np.sum(np.abs(chunk)) == 0:
        return False
    spectrum = np.abs(np.fft.rfft(chunk))
    freq_axis = np.fft.rfftfreq(len(chunk), 1 / sr)
    avg_magnitude = np.mean(spectrum)
    if avg_magnitude == 0:
        return False
    for freq_hz in aliased_freqs:
        band_mask = (freq_axis >= freq_hz - freq_tolerance) & (freq_axis <= freq_hz + freq_tolerance)
        if np.any(band_mask) and np.max(spectrum[band_mask]) > avg_magnitude * peak_ratio_threshold:
            return True
    return False


def preprocess_audio(file_path, target_sr, high_pass_cutoff):
    """Loads, resamples and high-pass filters a single audio file (mono)."""
    try:
        waveform, original_sr = torchaudio.load(file_path)
        if waveform.shape[0] > 1:
            waveform = torch.mean(waveform, dim=0, keepdim=True)
    except Exception as exc:
        print(f"         torchaudio failed ({exc}); falling back to librosa")
        waveform_np, original_sr = librosa.load(file_path, sr=None, mono=True)
        waveform = torch.from_numpy(waveform_np).unsqueeze(0)
    waveform = torchaudio.transforms.Resample(orig_freq=original_sr, new_freq=target_sr)(waveform)
    return high_pass_filter(waveform.numpy().flatten(), high_pass_cutoff, target_sr), target_sr


def load_panns_model():
    """PANNs Cnn14 AudioTagging model (GPU when available) and its class labels."""
    from panns_inference import AudioTagging

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"      [HYDROPHONE WAV] Loading PANNs (Cnn14) model on {device}...")
    model = AudioTagging(checkpoint_path=None, device=device)
    return model, model.labels


def _event_is_high_quality(group, cfg):
    return cfg["separate_loud_anomalies"] and any(
        a["loudness_ratio"] >= cfg["loudness_ratio_threshold"] for a in group
    )


def create_zoomed_event_plot(waveform, sr, file_name, file_start, group, plot_path, cfg):
    """Mel spectrogram zoomed on one event group, with plot_context_seconds either side."""
    group_start = group[0]["offset"]
    group_end = group[-1]["offset"] + cfg["chunk_seconds"]
    start_sample = max(0, int((group_start - cfg["plot_context_seconds"]) * sr))
    end_sample = min(len(waveform), int((group_end + cfg["plot_context_seconds"]) * sr))
    zoomed_clip = waveform[start_sample:end_sample]

    fig, ax = plt.subplots(figsize=(18, 6))
    n_fft, hop_length = 2048, 512
    S = librosa.feature.melspectrogram(y=zoomed_clip, sr=sr, n_mels=128, n_fft=n_fft, hop_length=hop_length)
    log_S = librosa.power_to_db(S, ref=np.max)
    librosa.display.specshow(log_S, sr=sr, x_axis="time", y_axis="mel", ax=ax, hop_length=hop_length, cmap="magma")
    fig.colorbar(ax.collections[0], ax=ax, format="%+2.0f dB", label="Intensity (dB)")

    event_utc = file_start + pd.Timedelta(seconds=group_start)
    ax.set_title(
        f"Zoomed event in {file_name} ({event_utc:%Y-%m-%d %H:%M:%S} UTC)\n"
        f"Time in file: {group_start:.1f}s - {group_end:.1f}s"
    )

    predictions_text = "--- Predictions for event ---\n"
    seen = set()
    for item in group:
        label, prob = item["top_3"][0]
        if label not in seen:
            predictions_text += f"- {label}: {prob:.2f}\n"
            seen.add(label)
    fig.text(0.02, 0.02, predictions_text, fontsize=9, wrap=True, verticalalignment="bottom")
    plt.tight_layout(rect=[0.0, 0.1, 1, 0.95])
    plt.savefig(plot_path, dpi=200)
    plt.close(fig)


# --- Analysis ---

def analyse_file(audio_file_path, file_start, serial, panns_model, class_labels, cfg, output_folder):
    """
    Run the detection on one wav file; write the events' spectrograms and clips
    under output_folder. Returns one log row per candidate
    (main_process_hydrophone_wav.LOG_COLUMNS);
    an empty list means no candidates (or a file too short to analyse).
    file_start: the recording's start time (UTC Timestamp), used for the
    absolute time of each candidate.
    """
    file_name = os.path.basename(audio_file_path)
    stem = os.path.splitext(file_name)[0]
    try:
        waveform, sr = preprocess_audio(audio_file_path, cfg["target_sample_rate"], cfg["high_pass_filter_hz"])
        file_rms_avg = np.sqrt(np.mean(waveform ** 2)) + 1e-9

        chunk_samples = int(cfg["chunk_seconds"] * sr)
        num_chunks = len(waveform) // chunk_samples
        if num_chunks == 0:
            print("         file too short to analyse")
            return []
        chunks = waveform[: num_chunks * chunk_samples].reshape(num_chunks, chunk_samples)

        # Batched inference: same outputs as one chunk at a time, much faster on GPU
        clipwise, embeddings = [], []
        for b in range(0, num_chunks, cfg["inference_batch_size"]):
            clip_out, emb = panns_model.inference(chunks[b:b + cfg["inference_batch_size"]])
            clipwise.append(clip_out)
            embeddings.append(emb)
        clipwise = np.concatenate(clipwise)
        feature_matrix = np.concatenate(embeddings).reshape(num_chunks, -1)

        db = DBSCAN(eps=cfg["dbscan_eps"], min_samples=cfg["dbscan_min_samples"]).fit(feature_matrix)
        anomaly_indices = np.where(db.labels_ == -1)[0]
        if len(anomaly_indices) == 0:
            return []

        aliased_freqs = [calculate_aliased_frequency(f, sr) for f in cfg["instrument_frequencies_hz"]]
        rows, validated = [], []

        def row(offset, status, reason, top_3, loudness_ratio, **extra):
            return {
                "time": file_start + pd.Timedelta(seconds=offset),
                "source_file": file_name,
                "serial": serial,
                "chunk_offset_s": offset,
                "status": status,
                "reason": reason,
                "top_prediction": top_3[0][0],
                "confidence": round(float(top_3[0][1]), 3),
                "top_3": "; ".join(f"{label}:{prob:.2f}" for label, prob in top_3),
                "loudness_ratio": round(float(loudness_ratio), 3),
                **extra,
            }

        for i in anomaly_indices:
            offset = i * cfg["chunk_seconds"]
            chunk = chunks[i]
            prediction = clipwise[i]
            top_3 = [(class_labels[k], prediction[k]) for k in np.argsort(prediction)[::-1][:3]]
            loudness_ratio = np.sqrt(np.mean(chunk ** 2)) / file_rms_avg

            if is_instrument_ping_energy(chunk, sr, aliased_freqs, cfg["frequency_tolerance_hz"], cfg["instrument_energy_threshold"]) \
                    or is_instrument_ping_peak(chunk, sr, aliased_freqs, cfg["frequency_tolerance_hz"], cfg["instrument_peak_ratio"]):
                rows.append(row(offset, "DISCARDED", "Instrument Ping", top_3, loudness_ratio))
                continue
            if cfg["validate_predictions"] and top_3[0][0] in cfg["avoid_labels"]:
                rows.append(row(offset, "DISCARDED", "Failed Validation", top_3, loudness_ratio))
                continue
            validated.append({"offset": offset, "top_3": top_3, "loudness_ratio": loudness_ratio})

        # Group validated candidates in time; small groups are isolated anomalies
        event_groups = []
        if validated:
            current = [validated[0]]
            for anom in validated[1:]:
                if anom["offset"] - current[-1]["offset"] <= cfg["temporal_grouping_seconds"]:
                    current.append(anom)
                else:
                    event_groups.append(current)
                    current = [anom]
            event_groups.append(current)
            event_groups = [g for g in event_groups if len(g) >= cfg["min_anomalies_in_group"]]

        grouped = set()
        for group in event_groups:
            hq = _event_is_high_quality(group, cfg)
            group_start = group[0]["offset"]
            event_id = f"{stem}_{group_start:.0f}s"

            plot_rel = os.path.join(HQ_PLOTS_SUBFOLDER if hq else STD_PLOTS_SUBFOLDER, f"{event_id}_zoomed.png")
            create_zoomed_event_plot(waveform, sr, file_name, file_start, group, os.path.join(output_folder, plot_rel), cfg)

            clip_rel = os.path.join(HQ_CLIPS_SUBFOLDER if hq else AUDIO_CLIPS_SUBFOLDER, f"{event_id}_clip.wav")
            start_sample = max(0, int((group_start - cfg["pre_context_seconds"]) * sr))
            end_sample = min(len(waveform), int((group[-1]["offset"] + cfg["chunk_seconds"] + cfg["post_context_seconds"]) * sr))
            sf.write(os.path.join(output_folder, clip_rel), waveform[start_sample:end_sample].astype(np.float32), sr, subtype="FLOAT")

            for anom in group:
                grouped.add(anom["offset"])
                rows.append(row(anom["offset"], "KEPT", "Part of Event Group", anom["top_3"], anom["loudness_ratio"],
                                event_id=event_id, high_quality=hq, clip_path=clip_rel, plot_path=plot_rel))

        for anom in validated:
            if anom["offset"] not in grouped:
                rows.append(row(anom["offset"], "DISCARDED", "Isolated Anomaly", anom["top_3"], anom["loudness_ratio"]))

        if event_groups:
            n_hq = sum(_event_is_high_quality(g, cfg) for g in event_groups)
            print(f"         {len(event_groups)} event(s), {n_hq} high-quality")
        return sorted(rows, key=lambda r: r["chunk_offset_s"])
    finally:
        gc.collect()
