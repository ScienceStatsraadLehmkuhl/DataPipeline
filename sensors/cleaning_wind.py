"""
Wind-specific processing rules, applied via `cleaning()` in cleaning.py.

- true_wind_correction: the Gill_2310037-WC76 ship anemometer only measures
  the apparent (relative) wind: direction the wind blows FROM, clockwise from
  the bow, and speed in m/s. This adds the true wind by removing the ship's
  motion (Smith et al. 1999):

      apparent direction in earth frame = relative direction + true heading (HDT)
      true wind vector = apparent wind vector + ship velocity (VTG COG/SOG)

  Heading and course/speed over ground come from the leg's already-processed
  NAVIGATION files (NAVIGATION runs before METEOROLOGY in run_processing), and
  are matched to each Gill record with a nearest merge_asof. Where either is
  missing within the tolerance, true wind is left empty.

  Where VTG is missing (e.g. 2025_2026_OOE2 legs 23-29 have no VTG export),
  the ship velocity is derived from the leg's GPS-MERGED-SOURCES positions
  instead (ship_velocity_from_positions): the displacement between the
  positions GPS_VELOCITY_HALF_WINDOW before and after each record, both from
  the same position source. Column `ship_velocity_source` says which one was
  used per row ("VTG" / "GPS").

  Likewise, where HDT is missing (2025_2026_OOE2 legs 1-13 have no HDT
  export), the heading comes from NAVIGATION/SXN23, which carries the same
  Seapath heading (identical to HDT within 0.02 deg on leg 21). Column
  `heading_source` says which one was used ("HDT" / "SXN23").

  Validated at anchor (2025_2026_OOE2 leg 21, 2025-12-08..14): Gill relative
  direction + heading matches the GMX560's corrected direction within ~6 deg,
  so the Gill needs no mounting-offset rotation.
"""
from pathlib import Path

import numpy as np
import pandas as pd

from DataPipeline.ingest.preprocessing import to_utc
from DataPipeline.settings import PROCESSED_ROOT

TRUE_WIND_INSTRUMENT = ("METEOROLOGY", "Gill_2310037-WC76")
REL_DIR_COL = "wind_angle_deg"
REL_SPEED_COL = "wind_speed_m_s"
TRUE_DIR_COL = "true_wind_direction_deg"
TRUE_SPEED_COL = "true_wind_speed_m_s"

HEADING_COL = "heading_deg_true"            # NAVIGATION/HDT
COG_COL = "course_over_ground_deg_true"     # NAVIGATION/VTG
SOG_COL = "speed_over_ground_kt"            # NAVIGATION/VTG

VELOCITY_SOURCE_COL = "ship_velocity_source"
HEADING_SOURCE_COL = "heading_source"
SXN23_HEADING_COL = "heading"               # NAVIGATION/SXN23 (same Seapath heading as HDT)

NAV_MATCH_TOLERANCE = pd.Timedelta("2s")
KNOTS_TO_M_S = 1852.0 / 3600.0

# GPS-derived ship velocity: centred difference over 2 x half window. Long
# enough that ~1 m position noise is ~0.02 m/s of speed noise, short enough
# to follow manoeuvres reasonably (a turn shortens the chord, so speed reads
# a little low while turning).
GPS_VELOCITY_HALF_WINDOW = pd.Timedelta("30s")
EARTH_RADIUS_M = 6371000.0


def _load_nav(cruise, leg, instrument, cols, time_col="time"):
    """
    The leg's processed NAVIGATION/{instrument} table (geotagged cleaned file,
    else the plain cleaned one), reduced to time + `cols`, rows with any of
    `cols` missing dropped, sorted by time.
    """
    folder = Path(PROCESSED_ROOT) / cruise / f"LEG{leg}" / "NAVIGATION"
    base = f"{cruise}_LEG{leg}_NAVIGATION_{instrument}"
    for name in (f"{base}_geotag_cleaned.csv", f"{base}_cleaned.csv"):
        path = folder / name
        if path.exists():
            break
    else:
        raise FileNotFoundError(f"No processed NAVIGATION/{instrument} file for {cruise} LEG{leg} in {folder}")

    nav = pd.read_csv(path, usecols=[time_col, *cols], low_memory=False)
    nav[time_col] = to_utc(nav[time_col])
    for c in cols:
        nav[c] = pd.to_numeric(nav[c], errors="coerce")
    return nav.dropna(subset=[time_col, *cols]).sort_values(time_col)


def _load_hdt(cruise, leg, time_col="time"):
    """The leg's HDT heading table, or None when there is none (no export for that leg)."""
    try:
        return _load_nav(cruise, leg, "HDT", [HEADING_COL], time_col=time_col)
    except (FileNotFoundError, ValueError) as exc:
        print(f"      [TRUE WIND] no usable HDT for {cruise} LEG{leg} ({exc}); using SXN23 heading")
        return None


def _load_sxn23_heading(cruise, leg, time_col="time"):
    """The leg's SXN23 heading table (column SXN23_HEADING_COL), or None."""
    try:
        return _load_nav(cruise, leg, "SXN23", [SXN23_HEADING_COL], time_col=time_col)
    except (FileNotFoundError, ValueError) as exc:
        print(f"      [WARN] true wind: no usable SXN23 heading for {cruise} LEG{leg} ({exc})")
        return None


def _load_vtg(cruise, leg, time_col="time"):
    """The leg's VTG table, or None when there is none (no export for that leg)."""
    try:
        return _load_nav(cruise, leg, "VTG", [COG_COL, SOG_COL], time_col=time_col)
    except (FileNotFoundError, ValueError) as exc:   # ValueError: file lacks the columns
        print(f"      [TRUE WIND] no usable VTG for {cruise} LEG{leg} ({exc}); using GPS-derived ship velocity")
        return None


def _load_positions(cruise, leg, time_col="time"):
    """The leg's GPS-MERGED-SOURCES position table (time, lat, lon, source), or None."""
    path = (Path(PROCESSED_ROOT) / cruise / f"LEG{leg}" / "NAVIGATION"
            / f"{cruise}_LEG{leg}_NAVIGATION_GPS-MERGED-SOURCES.csv")
    if not path.exists():
        print(f"      [WARN] true wind: {path.name} not found, no GPS-derived ship velocity")
        return None
    gps = pd.read_csv(path, usecols=[time_col, "latitude_deg", "longitude_deg", "source"], low_memory=False)
    gps[time_col] = to_utc(gps[time_col])
    for c in ("latitude_deg", "longitude_deg"):
        gps[c] = pd.to_numeric(gps[c], errors="coerce")
    return gps.dropna().sort_values(time_col)


def ship_velocity_from_positions(times, gps, *, half_window=GPS_VELOCITY_HALF_WINDOW,
                                 tolerance=NAV_MATCH_TOLERANCE, time_col="time"):
    """
    Course (deg true) and speed over ground (knots, as VTG) at each of `times`,
    from the positions nearest to t - half_window and t + half_window (each
    within `tolerance`). Both ends must come from the same position source,
    since GGA, EK80 and Ferrybox fixes come from different antennas and a
    jump between them would read as motion. NaN where that isn't available.
    Returns a DataFrame aligned with `times` (a Series of UTC datetimes).
    """
    q = pd.DataFrame({"_t": pd.to_datetime(times).to_numpy(), "_row": np.arange(len(times))}).dropna(subset=["_t"])
    ends = {}
    for side, shift in (("a", -half_window), ("b", half_window)):
        keys = pd.DataFrame({time_col: q["_t"] + shift, "_row": q["_row"]}).sort_values(time_col)
        pos = gps.rename(columns={"latitude_deg": f"lat_{side}", "longitude_deg": f"lon_{side}",
                                  "source": f"src_{side}"}).assign(**{f"t_{side}": gps[time_col]})
        ends[side] = (pd.merge_asof(keys, pos, on=time_col, direction="nearest", tolerance=tolerance)
                        .set_index("_row").drop(columns=time_col))
    m = ends["a"].join(ends["b"]).reindex(np.arange(len(times)))

    dt = (m["t_b"] - m["t_a"]).dt.total_seconds().to_numpy()
    lat0 = np.deg2rad((m["lat_a"] + m["lat_b"]).to_numpy() / 2.0)
    dlat = np.deg2rad((m["lat_b"] - m["lat_a"]).to_numpy())
    dlon = np.deg2rad(((m["lon_b"] - m["lon_a"] + 180.0) % 360.0 - 180.0).to_numpy())   # across the antimeridian too
    v_east = EARTH_RADIUS_M * np.cos(lat0) * dlon / dt
    v_north = EARTH_RADIUS_M * dlat / dt

    # A usable pair: same source, and the two fixes really about 2 x half_window apart
    ok = (m["src_a"] == m["src_b"]).to_numpy() & (dt >= half_window.total_seconds())
    sog_kt = np.where(ok, np.hypot(v_east, v_north) / KNOTS_TO_M_S, np.nan)
    cog = np.where(ok, np.rad2deg(np.arctan2(v_east, v_north)) % 360.0, np.nan)
    return pd.DataFrame({COG_COL: cog, SOG_COL: sog_kt})


def compute_true_wind(rel_dir_deg, rel_speed, heading_deg, cog_deg, sog_m_s):
    """
    True wind (direction FROM, degrees clockwise from north; speed in the
    units of `rel_speed`) from apparent wind relative to the bow, true
    heading, and the ship's course/speed over ground (speed in the same
    units as `rel_speed`). Accepts scalars or arrays.
    """
    app_dir = np.deg2rad(np.asarray(rel_dir_deg, dtype=float) + np.asarray(heading_deg, dtype=float))
    rel_speed = np.asarray(rel_speed, dtype=float)
    cog = np.deg2rad(np.asarray(cog_deg, dtype=float))
    sog = np.asarray(sog_m_s, dtype=float)

    # Components of the vector the air moves TOWARD (east, north)
    u = -rel_speed * np.sin(app_dir) + sog * np.sin(cog)
    v = -rel_speed * np.cos(app_dir) + sog * np.cos(cog)

    speed = np.hypot(u, v)
    direction = np.rad2deg(np.arctan2(-u, -v)) % 360.0
    return direction, speed


def true_wind_correction(
    df: pd.DataFrame,
    *,
    experiment: str,
    instrument: str,
    cruise: str | None,
    leg: int | None,
    time_col: str = "time",
) -> pd.DataFrame:
    """
    Add TRUE_DIR_COL / TRUE_SPEED_COL to the Gill frame. Runs ONLY for
    METEOROLOGY / Gill_2310037-WC76; a no-op otherwise, or when cruise/leg
    aren't known (no navigation data to correct with).
    """
    out = df
    if (experiment, instrument) != TRUE_WIND_INSTRUMENT:
        return out
    if cruise is None or leg is None:
        print("      [WARN] true wind: cruise/leg not given, skipping")
        return out

    hdt = _load_hdt(cruise, leg, time_col=time_col)
    vtg = _load_vtg(cruise, leg, time_col=time_col)

    # merge_asof needs sorted, non-null keys; match by row position so the
    # original row order and index are kept as they are
    out = df.copy()
    out[time_col] = to_utc(out[time_col])
    keys = pd.DataFrame({time_col: out[time_col].to_numpy(), "_row": np.arange(len(out))})
    keys = keys.dropna(subset=[time_col]).sort_values(time_col)
    if hdt is not None:
        matched = pd.merge_asof(keys, hdt, on=time_col, direction="nearest", tolerance=NAV_MATCH_TOLERANCE)
    else:
        matched = keys.assign(**{HEADING_COL: np.nan})
    matched = matched.reset_index(drop=True)
    matched[HEADING_SOURCE_COL] = np.where(matched[HEADING_COL].notna(), "HDT", None).astype(object)

    # Fill rows without HDT from the SXN23 heading (same Seapath unit, same
    # value: on leg 21 SXN23 - HDT is 0.000 deg median, 95% within 0.02 deg).
    # 2025_2026_OOE2 legs 1-13 have no HDT export at all.
    no_heading = matched[HEADING_COL].isna().to_numpy()
    if no_heading.any():
        sxn = _load_sxn23_heading(cruise, leg, time_col=time_col)
        if sxn is not None:
            fill = pd.merge_asof(matched.loc[no_heading, [time_col]], sxn, on=time_col,
                                 direction="nearest", tolerance=NAV_MATCH_TOLERANCE)
            vals = fill[SXN23_HEADING_COL].to_numpy(dtype=float)
            idx = np.flatnonzero(no_heading)
            got = np.isfinite(vals)
            matched.loc[idx[got], HEADING_COL] = vals[got]
            matched.loc[idx[got], HEADING_SOURCE_COL] = "SXN23"

    if vtg is not None:
        matched = pd.merge_asof(matched, vtg, on=time_col, direction="nearest", tolerance=NAV_MATCH_TOLERANCE)
    else:
        matched[COG_COL] = np.nan
        matched[SOG_COL] = np.nan
    matched = matched.set_index("_row").reindex(np.arange(len(out)))

    cog = matched[COG_COL].to_numpy(dtype=float)
    sog_kt = matched[SOG_COL].to_numpy(dtype=float)
    source = np.where(np.isfinite(cog) & np.isfinite(sog_kt), "VTG", None).astype(object)

    # Fill rows without VTG from GPS positions (only where heading exists too,
    # since true wind needs both)
    need = (source == None) & np.isfinite(matched[HEADING_COL].to_numpy(dtype=float))  # noqa: E711
    if need.any():
        gps = _load_positions(cruise, leg, time_col=time_col)
        if gps is not None:
            idx = np.flatnonzero(need)
            vel = ship_velocity_from_positions(out[time_col].iloc[idx].reset_index(drop=True), gps, time_col=time_col)
            got = np.isfinite(vel[SOG_COL].to_numpy())
            cog[idx[got]] = vel[COG_COL].to_numpy()[got]
            sog_kt[idx[got]] = vel[SOG_COL].to_numpy()[got]
            source[idx[got]] = "GPS"

    direction, speed = compute_true_wind(
        out[REL_DIR_COL].to_numpy(),
        out[REL_SPEED_COL].to_numpy(),
        matched[HEADING_COL].to_numpy(),
        cog,
        sog_kt * KNOTS_TO_M_S,
    )
    out[TRUE_DIR_COL] = direction
    out[TRUE_SPEED_COL] = speed
    out[VELOCITY_SOURCE_COL] = np.where(np.isfinite(speed), source, None)
    out[HEADING_SOURCE_COL] = np.where(np.isfinite(speed), matched[HEADING_SOURCE_COL].to_numpy(), None)

    n_vtg = int((out[VELOCITY_SOURCE_COL] == "VTG").sum())
    n_gps = int((out[VELOCITY_SOURCE_COL] == "GPS").sum())
    n_sxn = int((out[HEADING_SOURCE_COL] == "SXN23").sum())
    print(f"      [TRUE WIND] {n_vtg + n_gps}/{len(out)} Gill record(s) corrected "
          f"(heading from HDT: {n_vtg + n_gps - n_sxn}, from SXN23: {n_sxn}; "
          f"ship velocity from VTG: {n_vtg}, from GPS positions: {n_gps})")
    return out
