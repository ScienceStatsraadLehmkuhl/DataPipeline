"""
Ferrybox_CTD-specific cleaning rules, applied via `cleaning()`.

All rules below are gated to experiment == "OCEANOGRAPHY" and
instrument == "Ferrybox_CTD" and are no-ops otherwise:

- pressure_removal_clean: drops rows where the Ferrybox intake pressure
  falls below a leg-specific threshold (from the SooGuard sheet
  "Pressure_removal"), plus a following buffer window, to remove data
  collected while the ship's underway water intake was compromised
  (e.g. shallow water, docking).
- trilux_zero_clean: blanks out non-positive Trilux sensor readings
  (chlorophyll, phycoerythrin, turbidity), which are sensor artifacts
  rather than valid measurements.
- hampel_spike_clean: blanks individual/few-point spikes (values way out
  of range relative to their local time neighborhood) in every other
  numeric sensor column, using a rolling median/MAD (Hampel identifier).
"""
import pandas as pd
import numpy as np
from DataPipeline.manual_data_read import load_leg_windows, load_pressure_removal_rules
from DataPipeline.main_globals import (
    PRESSURE_REMOVAL_BUFFER_MINUTES,
    HAMPEL_WINDOW_MINUTES,
    HAMPEL_N_SIGMAS,
)

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

    # Rows with an unparseable timestamp can't be placed in the buffer
    # timeline (no valid ordering), so drop them the same way unparseable
    # pressure values are dropped above.
    out = out.dropna(subset=[time_col])

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

    # Early legs have no Trilux data at all: clean whichever columns exist.
    cols = [c for c in cols if c in out.columns]

    for col in cols:
        vals = pd.to_numeric(out[col], errors="coerce")
        out[col] = vals.where(vals > 0)

    return out


def hampel_spike_clean(
    df: pd.DataFrame,
    *,
    experiment: str,
    instrument: str,
    time_col: str = "time",
    window_minutes: float = HAMPEL_WINDOW_MINUTES,
    n_sigmas: float = HAMPEL_N_SIGMAS,
    exclude_patterns: tuple[str, ...] = ("lat", "lon", "pressure"),
) -> pd.DataFrame:
    """
    Blank out (set to NaN) individual datapoints that are extreme outliers
    relative to their local time neighborhood, independently per column.
    Rows are kept; only the offending cell is removed.

    Uses a Hampel identifier: for each numeric column (excluding `time_col`
    and any column whose name contains "lat", "lon", or "pressure"), each
    point is compared to the median of a centered `window_minutes`-wide time
    window around it. A point is blanked if its distance from that local
    median exceeds `n_sigmas` times the window's MAD (median absolute
    deviation, scaled by 1.4826 to be comparable to a standard deviation).
    This is more robust than a global z-score/threshold because it adapts to
    local drift/trends and isn't itself skewed by the outliers it's meant to
    catch (unlike mean/std).

    Runs ONLY when experiment == "OCEANOGRAPHY" and instrument == "Ferrybox_CTD".
    """
    out = df.copy()

    # Gate: only run for this experiment/instrument combination
    if experiment != "OCEANOGRAPHY" or instrument != "Ferrybox_CTD":
        return out

    if time_col not in out.columns:
        raise KeyError(f"'{time_col}' not in df columns: {list(out.columns)}")

    # Target columns: numeric, not the time column, and not lat/lon/pressure
    cols = [
        c for c in out.columns
        if c != time_col
        and pd.api.types.is_numeric_dtype(out[c])
        and not any(p.lower() in c.lower() for p in exclude_patterns)
    ]
    if not cols:
        return out

    time_vals = pd.to_datetime(out[time_col], errors="coerce")
    # Rows with an unparseable timestamp can't be placed in a time window,
    # so they're left out of the rolling computation (and left unflagged)
    # rather than dropped, since this step only ever blanks cells.
    order = time_vals[time_vals.notna()].sort_values().index
    window = f"{window_minutes}min"

    for col in cols:
        vals = pd.to_numeric(out[col], errors="coerce")
        tmp = pd.DataFrame({time_col: time_vals.loc[order], "_val": vals.loc[order]})

        # min_periods=3: with fewer neighbors the local MAD isn't a
        # meaningful robust estimate, so those points are left unflagged.
        rolling = tmp.rolling(window, on=time_col, center=True, min_periods=3)
        local_median = rolling["_val"].median()
        abs_dev = (tmp["_val"] - local_median).abs()
        tmp["_abs_dev"] = abs_dev
        local_mad = tmp.rolling(window, on=time_col, center=True, min_periods=3)["_abs_dev"].median()

        threshold = n_sigmas * 1.4826 * local_mad
        is_outlier = abs_dev > threshold

        out.loc[is_outlier[is_outlier].index, col] = np.nan

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
    """
    Apply Ferrybox_CTD cleaning rules to `df`, in order: pressure-removal
    (only if `leg` is given), then Trilux zero-cleaning, then Hampel
    spike removal.
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

    return out