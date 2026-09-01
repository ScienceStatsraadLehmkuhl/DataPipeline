from pathlib import Path

from OneOceanExpedition_DataPipeline.main_process_sensors import run_processing
from OneOceanExpedition_DataPipeline.main_plot import run_plotting, run_expedition_plotting
from OneOceanExpedition_DataPipeline.main_globals import *
from OneOceanExpedition_DataPipeline.globals import LEGS
from OneOceanExpedition_DataPipeline.cli import parse_args
from OneOceanExpedition_DataPipeline.combine_dataset_new import combine_all_intervals
from OneOceanExpedition_DataPipeline.gap_analysis import run_gap_analysis


if __name__ == "__main__":
    args = parse_args()

    legs_to_run = LEGS if args.leg is None else [args.leg]

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

        if args.mode in ("plot", "both"):
            run_plotting(
                cruise=args.cruise,
                leg=leg,
                plot_types_list=args.plot_types,
                only_experiments=args.only_experiments,
                only_instruments=args.only_instruments,
                only_variables=args.only_variables,
            )

    if args.run_combine and args.mode in ("process", "both"):
        combine_all_intervals(
            cruise=args.cruise,
            only_experiments=args.only_experiments,
            only_instruments=args.only_instruments,
        )

    if args.mode in ("plot", "both"):
        run_expedition_plotting(
            cruise=args.cruise,
            only_experiments=args.only_experiments,
            only_instruments=args.only_instruments,
            only_variables=args.only_variables,
        )

    if args.run_gap_analysis and args.mode in ("process", "both"):
        run_gap_analysis(
            cruise=args.cruise,
            cache_dir=Path.home() / ".cache" / "gap_analysis" / args.cruise,
        )