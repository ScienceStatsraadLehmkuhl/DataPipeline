from pathlib import Path

from DataPipeline.sensors.main_process_sensors import run_processing
from DataPipeline.ctd.main_process_ctd import run_processing_ctd
from DataPipeline.plotting.main_plot import run_plotting, run_expedition_plotting
from DataPipeline.ctd.main_plot_ctd import run_plotting_ctd
from DataPipeline.settings import *
from DataPipeline.vocabulary import LEGS
from DataPipeline.cli import parse_args
from DataPipeline.products.combine import combine_all_intervals
from DataPipeline.products.gap_analysis import run_gap_analysis
from DataPipeline.acoustics.main_process_acoustics import run_processing_acoustics


if __name__ == "__main__":
    args = parse_args()

    legs_to_run = LEGS if args.leg is None else (args.leg if isinstance(args.leg, (list, tuple)) else [args.leg])

    for leg in legs_to_run:
        if args.mode in ("process", "both"):
            run_processing(
                cruise=args.cruise,
                leg=leg,
                update_flag=args.update,
                only_experiments=args.only_experiments,
                only_instruments=args.only_instruments,
                only_variables=args.only_variables,
            )
            run_processing_ctd(
                cruise=args.cruise,
                leg=leg,
                update_flag=args.update,
                only_experiments=args.only_experiments,
                only_instruments=args.only_instruments,
            )

        if args.mode in ("plot", "both"):
            run_plotting(
                cruise=args.cruise,
                leg=leg,
                plot_types_list=args.plot_types,
                only_experiments=args.only_experiments,
                only_instruments=args.only_instruments,
                only_variables=args.only_variables,
            )
            run_plotting_ctd(
                cruise=args.cruise,
                leg=leg,
                only_experiments=args.only_experiments,
                only_instruments=args.only_instruments,
            )

    if args.run_acoustics and args.mode in ("process", "both"):
        run_processing_acoustics(
            cruise=args.cruise,
            leg=args.leg,
            only_acoustics=args.only_acoustics,
        )

    if args.run_combine and args.mode in ("process", "both"):
        combine_all_intervals(
            cruise=args.cruise,
            only_experiments=args.only_experiments,
            only_instruments=args.only_instruments,
        )

    # Gap analysis runs BEFORE the expedition plots: the data-coverage figure
    # is drawn from the workbook it writes.
    if args.run_gap_analysis and args.mode in ("process", "both"):
        run_gap_analysis(
            cruise=args.cruise,
            leg=args.leg,
            cache_dir=Path.home() / ".cache" / "gap_analysis" / args.cruise,
        )

    if args.mode in ("plot", "both"):
        run_expedition_plotting(
            cruise=args.cruise,
            only_experiments=args.only_experiments,
            only_instruments=args.only_instruments,
            only_variables=args.only_variables,
         )