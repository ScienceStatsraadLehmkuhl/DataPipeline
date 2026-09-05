##This is for all the parameters that can be selected from the Main!



#What to select:
    ##Cruise
#CRUISE = "2026_SaS"
CRUISE = "2025_2026_OOE2"

    ## leg
LEG = "15"    # To run only one leg
#LEG = ["14", "15", "16"]
#LEG = None   # To run all legs

    #Processing? Plotting? both?
MODE = "process"  # "process", "plot", or "both"

    ##What to run: 
ONLY_EXPERIMENTS = None   # e.g. ["OCEANOGRAPHY", "METEOROLOGY"] ##Always pick navigation + other things
ONLY_INSTRUMENTS =None   # e.g. ["Ferrybox_CTD"]
ONLY_VARIABLES = None     # e.g. ["O2_Temperature"]

DEFAULT_PLOT_TYPES = ["time", "time_pts", "ferrybox_colour_pannel"]

    ## Cleaning for SooGuard
PRESSURE_REMOVAL_BUFFER_MINUTES = 15   # minutes of data removed after each pressure-removal event

    ## GPS gap-fill (EK80/Ferrybox fill in for GGA when it has gaps)
GGA_GAP_FILL_THRESHOLD_MINUTES = 5   # GGA gaps larger than this trigger EK80/Ferrybox gap-fill
