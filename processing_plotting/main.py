from main_process_sensors import run_processing
from main_plot import run_plotting
from main_globals import *
from globals import LEGS
from cli import parse_args
from combine_dataset_new import combine_all_intervals


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