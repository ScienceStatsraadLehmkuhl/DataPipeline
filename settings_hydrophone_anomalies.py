"""Settings for the hydrophone wav anomaly detection.

Used by acoustics/hydrophone_anomalies.py (ported from
OtherProjects/HydrophoneAnomalies PANNsClustering.ipynb) through
acoustics/main_process_hydrophone_wav.py. Whether it runs at all is still
controlled in settings.py (RUN_ACOUSTICS / ONLY_ACOUSTICS, source names
"Hydrophone_{serial}_anomalies").

Each wav is analysed once; changing these only affects files analysed
afterwards -- delete a hydrophone's per_file_logs/ folder to re-run a leg
with new values.
"""


HYDROPHONE_ANOMALY_CONFIG = {
    # Anomaly quality control: events with any chunk this many times louder
    # (RMS) than the whole file go to high_quality_plots/ and high_quality_clips/
    "separate_loud_anomalies": True,
    "loudness_ratio_threshold": 2.5,

    # Audio processing & visualisation
    "chunk_seconds": 3.0,
    "pre_context_seconds": 5.0,    # audio saved before the event group
    "post_context_seconds": 7.0,   # audio saved after the event group
    "plot_context_seconds": 2.0,   # shorter context for the zoomed spectrogram
    "target_sample_rate": 32000,   # PANNs Cnn14 is trained at 32 kHz
    "high_pass_filter_hz": 2000,
    "inference_batch_size": 32,    # chunks per PANNs forward pass

    # Instrument ping filtering (EK80 and ADCP)
    "instrument_frequencies_hz": [38000, 75000, 80000],
    "frequency_tolerance_hz": 250,
    "instrument_peak_ratio": 8.0,        # band peak > this x mean spectral magnitude -> ping
    "instrument_energy_threshold": 0.30, # energy share in the bands above this -> ping

    # Clustering & grouping
    "dbscan_eps": 2.5,
    "dbscan_min_samples": 4,
    "temporal_grouping_seconds": 15.0,
    "min_anomalies_in_group": 2,

    # PANNs label filtering: discard candidates whose top label is one of these
    "validate_predictions": True,
    "avoid_labels": ["Stream", "Water", "Pour", "Frying (food)", "Drip", "Boiling", "Raindrop"],
}
