##This is for all the parameters that can be selected from the Main!



#What to select:
    ##Cruise
CRUISE = "2026_SaS"
#CRUISE = "2025_2026_OOE2"

    ## leg
LEG = "16"    # To run only one leg
#LEG = None   # To run all legs

    #Processing? Plotting? both?
MODE = "both"  # "process", "plot", or "both"

    ##What to run: 
ONLY_EXPERIMENTS = None   # e.g. ["OCEANOGRAPHY", "METEOROLOGY"] ##Always pick navigation + other things
ONLY_INSTRUMENTS =None   # e.g. ["Ferrybox_CTD"]
ONLY_VARIABLES = None     # e.g. ["O2_Temperature"]

DEFAULT_PLOT_TYPES = ["time", "time_pts", "ferrybox_colour_pannel"]

    ## Cleaning for SooGuard
PRESSURE_REMOVAL_BUFFER_MINUTES = 15   # minutes of data removed after each pressure-removal event
