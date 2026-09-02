import pandas as pd
import numpy as np
from DataPipeline.manual_data_read import load_leg_windows, load_pressure_removal_rules
from DataPipeline.main_globals import PRESSURE_REMOVAL_BUFFER_MINUTES

load_leg_windows.cache_clear()


def pressure_removal_clean(
    df: pd.DataFrame,
    *,
    leg: int,
    experiment: str,
    instrument: str,
    sooguard_path: str,
    sooguard_sheet: str = "Pressure_removal",
    pressure_col: str = "ts_pressure",
    time_col: str = "time",
    buffer_minutes: float = PRESSURE_REMOVAL_BUFFER_MINUTES,
    strict: bool = True,   # if False: if no rule found for leg, return df unchanged
) -> pd.DataFrame:
    """
    Remove rows where `pressure_col` is below the leg-specific threshold from the
    SooGuard sheet "Pressure_removal", plus the following `buffer_minutes` of data
    after each such removed row.

    Runs ONLY when experiment == "OCEANOGRAPHY" and instrument == "Ferrybox_CTD".
    """
    out = df.copy()

    # Gate: only run for this experiment/instrument combination
    if experiment != "OCEANOGRAPHY" or instrument != "Ferrybox_CTD":
        return out

    if pressure_col not in out.columns:
        raise KeyError(f"'{pressure_col}' not in df columns: {list(out.columns)}")
    if time_col not in out.columns:
        raise KeyError(f"'{time_col}' not in df columns: {list(out.columns)}")

    # Load thresholds and pick the one for this leg
    rules = load_pressure_removal_rules(sooguard_path, sheet_name=sooguard_sheet)
    row = rules.loc[rules["leg"].eq(int(leg))]
    if row.empty:
        if strict:
            raise KeyError(f"No pressure removal rule found for leg={leg} in {sooguard_path} ({sooguard_sheet})")
        return out
    if len(row) > 1:
        raise ValueError(f"Multiple pressure removal rules found for leg={leg}; expected exactly 1.")

    threshold = float(row["pressure_under"].iloc[0])

    # Canonicalize pressure values and drop unparseable values (can’t compare)
    out[pressure_col] = pd.to_numeric(out[pressure_col], errors="coerce")
    out = out.dropna(subset=[pressure_col])

    if out[time_col].isna().any():
        raise ValueError(f"'{time_col}' contains NaT values; cannot compute {buffer_minutes}-minute buffer.")

    # Defensive: callers are expected to pass data pre-sorted by time_col,
    # but the buffer computation below requires it.
    out = out.sort_values(time_col, kind="mergesort")

    # Remove below-threshold rows AND the following `buffer_minutes` of data.
    # Forward-filling the last below-threshold timestamp merges overlapping/
    # cascading buffer windows into one continuous exclusion region.
    buffer_delta = pd.Timedelta(minutes=buffer_minutes)
    below_threshold = out[pressure_col] < threshold
    last_bad_time = out[time_col].where(below_threshold).ffill()
    elapsed = out[time_col] - last_bad_time

    drop_mask = below_threshold | (last_bad_time.notna() & (elapsed <= buffer_delta))
    out = out.loc[~drop_mask].reset_index(drop=True)

    return out






def trilux_zero_clean(
    df: pd.DataFrame,
    *,
    experiment: str,
    instrument: str,
    cols: tuple[str, ...] = ("trilux_chlorophyll", "trilux_phycoerythrin", "trilux_turbidity"),
) -> pd.DataFrame:
    """
    Blank out (set to NaN) values <= 0 in `cols`, independently per column.
    Rows are kept; only the offending cell is removed.

    Runs ONLY when experiment == "OCEANOGRAPHY" and instrument == "Ferrybox_CTD".
    """
    out = df.copy()

    # Gate: only run for this experiment/instrument combination
    if experiment != "OCEANOGRAPHY" or instrument != "Ferrybox_CTD":
        return out

    missing = [c for c in cols if c not in out.columns]
    if missing:
        raise KeyError(f"{missing} not in df columns: {list(out.columns)}")

    for col in cols:
        vals = pd.to_numeric(out[col], errors="coerce")
        out[col] = vals.where(vals > 0)

    return out


def cleaning(
    df: pd.DataFrame,
    *,
    time_col: str = "time",
    leg: int | None = None,
    legs_path: str | None = None,
    experiment: str | None = None,
    instrument: str | None = None,
    sooguard_path: str | None = None,
) -> pd.DataFrame:
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

    return out