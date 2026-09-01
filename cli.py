import argparse
from OneOceanExpedition_DataPipeline.main_globals import (
    CRUISE,
    DEFAULT_PLOT_TYPES,
    LEG,
    MODE,
    ONLY_EXPERIMENTS,
    ONLY_INSTRUMENTS,
    ONLY_VARIABLES,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run the OOE-2 workflow: process, plot, or both."
    )
    parser.add_argument(
        "mode",
        nargs="?",
        choices=["process", "plot", "both"],
        default=MODE,
        help="Mode to run: process, plot, or both. Defaults to value set in Settings.",
    )
    parser.add_argument("--cruise", default=CRUISE)
    parser.add_argument("--leg", default=LEG, help="Single leg to run. Omit / use None in settings to run all legs.")
    parser.add_argument("--plot-types", nargs="+", default=DEFAULT_PLOT_TYPES)
    parser.add_argument("--no-update", dest="update", action="store_false", help="Skip data update and use existing processed files.")
    parser.add_argument("--no-combine", dest="run_combine", action="store_false", default=True, help="Skip the dataset-combining step when running the main workflow.")
    parser.add_argument("--no-gap-analysis", dest="run_gap_analysis", action="store_false", default=True, help="Skip the gap analysis step when running the main workflow.")
    parser.add_argument("--only-experiments", nargs="+", default=ONLY_EXPERIMENTS, help="Filter to specific experiment names.")
    parser.add_argument("--only-instruments", nargs="+", default=ONLY_INSTRUMENTS, help="Filter to specific instrument names.")
    parser.add_argument("--only-variables", nargs="+", default=ONLY_VARIABLES, help="Filter to specific variable names.")
    return parser.parse_args()
