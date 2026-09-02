# DataPipeline

Python tools for processing, cleaning, combining, and plotting sensor data
from One Ocean Expedition and other voyages aboard Statsraad Lehmkuhl.

The pipeline processes data by leg, creates standardized and resampled CSV
files, generates PDF and PNG figures, combines data across legs, and reports
time gaps in the processed data.

## Requirements

- Python 3.10 or newer
- `pandas`
- `numpy`
- `matplotlib`
- `openpyxl`
- `pyarrow` (optional; used to speed up some CSV reads)

Install the main dependencies in the active environment with:

```bash
pip install pandas numpy matplotlib openpyxl
```

## Data locations

The default paths are configured in `input_tools.py` and
`main_globals.py`. The pipeline currently expects the raw and processed data
shares to be mounted at:

```text
/run/user/1000/gvfs/smb-share:server=sl-nas.local,share=geomatics/{cruise}/
/run/user/1000/gvfs/smb-share:server=sl-nas.local,share=processed_data/{cruise}/
```

Raw data is organized by leg and instrument. A cruise also needs a
`data_entries/` directory containing the logsheet workbooks used to determine
leg windows and pressure-removal rules.

```text
{cruise}/
├── LEG{leg}_*/
│   ├── NAVIGATION/
│   ├── OCEANOGRAPHY/
│   ├── ACOUSTIC/
│   ├── METEOROLOGY/
│   └── RADIATION/
└── data_entries/
		├── Logsheet_LegNumber_StartDate-Time.xlsx
		└── LogSheet_SooGuard.xlsx
```

If your files are stored elsewhere, update the path configuration before
running the pipeline.

## Running the pipeline

Run commands from the directory containing the `DataPipeline/` package:

```bash
cd /home/operator0/data_processing
```

Run processing and plotting for the configured cruise and leg:

```bash
python -m DataPipeline.main both
```

Run only one part of the workflow:

```bash
python -m DataPipeline.main process --cruise 2026_SaS --leg 16
python -m DataPipeline.main plot --cruise 2026_SaS --leg 16
```

Useful options include:

```text
--cruise NAME                 Cruise folder name
--leg LEG                     Process one leg; otherwise use configured legs
--plot-types TYPE [TYPE ...]  time, time_pts, distribution, or ferrybox_colour_pannel
--no-update                   Skip regeneration of processed files
--no-combine                  Skip cruise-wide file combination
--no-gap-analysis             Skip gap-analysis workbooks
--only-experiments NAME ...   Restrict processing to selected experiments
--only-instruments NAME ...   Restrict processing to selected instruments
--only-variables NAME ...     Restrict processing to selected variables
```

## Standalone commands

Combine already processed leg files at several time intervals:

```bash
python -m DataPipeline.combine_dataset_new \
	--cruise 2026_SaS \
	--intervals 1min 3min 5min 1h 1D
```

Create expedition-length plots from existing combined 5-minute files:

```bash
python -m DataPipeline.plot_expedition_report --cruise 2026_SaS
```

Run gap analysis:

```bash
python -m DataPipeline.gap_analysis --cruise 2026_SaS
```

## Generated files

Per-leg processed data is written below the processed cruise directory:

```text
LEG{leg}/{experiment}/
├── *_cleaned.csv
├── *_1min.csv
├── *_3min.csv
├── *_5min.csv
└── *_geotag*.csv
```

Per-leg figures are saved in both formats:

```text
LEG{leg}/FIGURES/PDF/
LEG{leg}/FIGURES/PNG/
```

Cruise-wide files and expedition figures are saved below:

```text
combined_files/
├── 1min/
├── 3min/
├── 5min/
├── 1h/
├── 1D/
├── FIGURES/PDF/
└── FIGURES/PNG/
```

Gap analysis creates Excel workbooks with `gaps` and `statistics` sheets:

```text
LEG{leg}/gap_analysis_{cruise}_LEG{leg}.xlsx
combined_files/gap_analysis_{cruise}.xlsx
```

## Configuration

Edit `main_globals.py` to set the default cruise, leg, execution mode, and
optional filters. `globals.py` contains the configured legs, experiments,
instruments, variables, column mappings, and plot labels.

```python
CRUISE = "2026_SaS"
LEG = "16"
MODE = "both"
ONLY_EXPERIMENTS = None
ONLY_INSTRUMENTS = None
ONLY_VARIABLES = None
```

The normal workflow is:

1. Discover and import raw files.
2. Standardize columns and timestamps.
3. Clean and resample sensor data.
4. Generate per-leg figures.
5. Combine files across legs.
6. Generate expedition-length figures.
7. Write gap-analysis reports.

## Project modules

- `main.py`: workflow dispatcher and command-line entry point
- `cli.py`: command-line argument parsing
- `main_process_sensors.py`: per-leg processing
- `preprocessing.py`: timestamp normalization and resampling
- `cleaning.py`: sensor-specific cleaning rules
- `combine_dataset_new.py`: cruise-wide data combination
- `main_plot.py`: per-leg and expedition plotting orchestration
- `plot_expedition_report.py`: expedition-length plots
- `gap_analysis.py`: gap reports and statistics
- `globals.py`: experiment and instrument configuration
- `main_globals.py`: runtime defaults and filters

## Notes

- Expedition plotting requires combined 5-minute files to exist first.
- The pipeline assumes the SMB shares are mounted at the configured paths.
- `--no-combine` and `--no-gap-analysis` are useful when rerunning only part
	of a workflow.
- Generated files can be large, so check the configured output directory
	before starting a full cruise run.
