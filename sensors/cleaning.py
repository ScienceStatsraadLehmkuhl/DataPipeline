"""
Entry point for per-instrument cleaning rules: `cleaning()` runs every rule
set in turn, and each rule is gated to its own experiment/instrument (a no-op
for anything else).

- cleaning_Ferrybox.py: OCEANOGRAPHY / Ferrybox_CTD (pressure removal, Trilux
  zeros, Hampel spikes)
- cleaning_wind.py: METEOROLOGY / Gill_2310037-WC76 (true wind from the
  apparent wind, heading and ship velocity)
"""
import pandas as pd

from DataPipeline.sensors.cleaning_Ferrybox import (
    pressure_removal_clean,
    trilux_zero_clean,
    hampel_spike_clean,
)
from DataPipeline.sensors.cleaning_wind import true_wind_correction


def cleaning(
    df: pd.DataFrame,
    *,
    time_col: str = "time",
    leg: int | None = None,
    legs_path: str | None = None,
    experiment: str | None = None,
    instrument: str | None = None,
    sooguard_path: str | None = None,
    cruise: str | None = None,
) -> pd.DataFrame:
    """
    Apply the cleaning rules to `df`, in order: Ferrybox_CTD pressure-removal
    (only if `leg` is given), Trilux zero-cleaning and Hampel spike removal,
    then the Gill true-wind correction (needs `cruise` and `leg`).
    """
    out = df.copy()


    if leg is not None:
        out = pressure_removal_clean(
            out,
            leg=int(leg),
            experiment=experiment or "",
            instrument=instrument or "",
            sooguard_path=sooguard_path,
            pressure_col="ts_pressure",
            time_col=time_col,
        )

    out = trilux_zero_clean(
        out,
        experiment=experiment or "",
        instrument=instrument or "",
    )

    out = hampel_spike_clean(
        out,
        experiment=experiment or "",
        instrument=instrument or "",
        time_col=time_col,
    )

    out = true_wind_correction(
        out,
        experiment=experiment or "",
        instrument=instrument or "",
        cruise=cruise,
        leg=leg,
        time_col=time_col,
    )

    return out
