"""Central control panel for the data pipeline.

Every setting a user might want to change between runs lives here instead of
being buried in individual scripts. Edit the values below, then run
`main.py` (or the relevant script) — nothing else needs to change.

Sections:
    - Cruise/leg selection      -- which cruise and leg(s) to process
    - Run mode                  -- process vs. plot, and what to include
    - Acoustics                 -- the separate acoustics pipeline
    - Combined dataset          -- resampling intervals for combine_dataset_new.py
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
#LEG = "9"          # run only one leg
#LEG = ["9", "10", "11", "12", "14", "27", "28"]   # run a specific list of legs
LEG = None          # run all legs


# ============================================================
# Run mode: what the pipeline should do, and on what subset
# ============================================================

## Processing? Plotting? Both?
MODE = "plot"  # "process", "plot", or "both"

## Filters on what gets run (None = no filter, i.e. run everything)
ONLY_EXPERIMENTS = None   # e.g. ["OCEANOGRAPHY", "METEOROLOGY"]; navigation is always included
ONLY_INSTRUMENTS = None   # e.g. ["Ferrybox_CTD"]
ONLY_VARIABLES = None     # e.g. ["O2_Temperature"]


# ============================================================
# Acoustics (its own pipeline, run separately from "process sensors" above see main_processing_acoustics.py)
# ============================================================

RUN_ACOUSTICS = False       # whether to run the acoustics pipeline at all
ONLY_ACOUSTICS = None       # e.g. ["EK80_echos_csv"]; None = run every implemented acoustic source


# ============================================================
# Combined dataset (used by combine_dataset_new.py)
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

## Gap analysis (gap_analysis.py)
GAP_THRESHOLD_MINUTES = 1   # only report gaps larger than this threshold

## Ferrybox spike removal (hampel_spike_clean in cleaning_Ferrybox.py):
## a Hampel filter that blanks single/few-point spikes by comparing each
## point to a rolling median computed from its local time neighborhood.
HAMPEL_WINDOW_MINUTES = 10   # width of the centered time window (in minutes) used to compute
                              # the local rolling median/MAD that each point is judged against
HAMPEL_N_SIGMAS = 3.0        # a point is blanked if it's more than this many scaled-MADs
                              # (MAD-based equivalent of "standard deviations") from that window's median
