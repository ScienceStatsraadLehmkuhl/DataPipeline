"""Central control panel for the data pipeline.

Every setting a user might want to change between runs lives here instead of
being buried in individual scripts. Edit the values below, then run
`main.py` (or the relevant script) — nothing else needs to change.

Sections:
    - Cruise/leg selection      -- which cruise and leg(s) to process
    - Run mode                  -- process vs. plot, and what to include
    - Acoustics                 -- the separate acoustics pipeline
    - Combined dataset          -- resampling intervals for combine.py
    - Plotting                  -- default plot types
    - Cleaning / gap-filling    -- thresholds used by cleaning and gap-fill steps
"""


# ============================================================
# Cruise / leg selection
# ============================================================

## Cruise: name of the cruise folder under processed_data
#CRUISE = "2026_SaS"
CRUISE = "2025_2026_OOE2"

## Leg(s) to run
#LEG = "1"          # run only one leg
#LEG = ["17", "18"]   # run a specific list of legs
LEG = None          # run all legs


# ============================================================
# Run mode: what the pipeline should do, and on what subset
# ============================================================

## Processing? Plotting? Both?
MODE = "process"  # "process", "plot", or "both"

## Filters on what gets run (None = no filter, i.e. run everything)
ONLY_EXPERIMENTS = None   # e.g. ["OCEANOGRAPHY", "METEOROLOGY"]; navigation is always included
ONLY_INSTRUMENTS = None   # e.g. ["Ferrybox_CTD"]
ONLY_VARIABLES = None     # e.g. ["O2_Temperature"]


# ============================================================
# Acoustics (its own pipeline, run separately from "process sensors" above, see acoustics/main_process_acoustics.py)
# ============================================================

RUN_ACOUSTICS = True       # whether to run the acoustics pipeline at all
ONLY_ACOUSTICS = None       # e.g. ["EK80_echos_csv"]; None = run every implemented acoustic source
                            # hydrophone wav anomaly detection: "Hydrophone_{serial}_anomalies"

## Hydrophone wav anomaly detection (acoustics/hydrophone_anomalies.py, ported
## from OtherProjects/HydrophoneAnomalies PANNsClustering.ipynb). Each wav is
## analysed once; changing these only affects files analysed afterwards --
## delete a hydrophone's per_file_logs/ folder to re-run a leg with new values.
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


# ============================================================
# Combined dataset (used by combine.py)
# ============================================================

## Resampling intervals to generate for the combined dataset
INTERVALS = ["1D", "1h", "5min"]    # add "3min", "1min", "cleaned" as needed


# ============================================================
# Plotting
# ============================================================

## Per-variable:  "time", "time_pts", "distribution" (one figure per variable per leg)
## Per-instrument (once per leg, each only applies to its own instrument):
##   "ferrybox_colour_pannel"  Ferrybox_CTD 6-panel overview
##   "wind_rose"               GMX560 corrected wind
##   "cleaning_diagnostics"    Ferrybox_CTD raw vs cleaned (spikes, removed windows)
##   "gps_sources"             GPS-MERGED-SOURCES track/timeline/coverage by source
## The expedition-wide figures (time series, data coverage, GPS source summary)
## are always drawn by the plot step and don't depend on this list.
DEFAULT_PLOT_TYPES = ["time", "time_pts", "ferrybox_colour_pannel", "wind_rose", "cleaning_diagnostics", "gps_sources"]


# ============================================================
# Cleaning / gap-filling thresholds
# ============================================================

## SooGuard cleaning: data removed after each pressure-removal event
PRESSURE_REMOVAL_BUFFER_MINUTES = 15   # minutes of data removed after each pressure-removal event

## GPS gap-fill: EK80/Ferrybox fill in for GGA when it has gaps
GGA_GAP_FILL_THRESHOLD_MINUTES = 0.06   # GGA gaps larger than this trigger EK80/Ferrybox gap-fill #0.06 = 4 sec
BRIDGE_GAP_FILL_THRESHOLD_MINUTES = 10   # stretches still larger than this after EK80/Ferrybox trigger the Bridge nav log (last resort)

## Gap analysis (gap_analysis.py)
GAP_THRESHOLD_MINUTES = 1   # only report gaps larger than this threshold
# Per-instrument overrides of GAP_THRESHOLD_MINUTES. GPS-MERGED-SOURCES is
# partly filled from the Bridge nav log (one fix every 6 min), so its 6-min
# steps would otherwise all count as gaps.
GAP_THRESHOLD_MINUTES_BY_INSTRUMENT = {"GPS-MERGED-SOURCES": 15}

## Ferrybox spike removal (hampel_spike_clean in cleaning_Ferrybox.py):
## a Hampel filter that blanks single/few-point spikes by comparing each
## point to a rolling median computed from its local time neighborhood.
HAMPEL_WINDOW_MINUTES = 10   # width of the centered time window (in minutes) used to compute
                              # the local rolling median/MAD that each point is judged against
HAMPEL_N_SIGMAS = 3.0        # a point is blanked if it's more than this many scaled-MADs
                              # (MAD-based equivalent of "standard deviations") from that window's median
