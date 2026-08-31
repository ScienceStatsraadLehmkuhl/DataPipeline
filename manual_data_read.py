import pandas as pd
from functools import lru_cache
from pathlib import Path

def get_logsheet_paths(cruise):
    root = Path(f"/run/user/1000/gvfs/smb-share:server=sl-nas.local,share=processed_data/{cruise}/data_entries")

    return (
        root / "Logsheet_LegNumber_StartDate-Time.xlsx",
        root / "LogSheet_SooGuard.xlsx",
    )




def to_canonical_utc_datetime(s: pd.Series, *, utc: bool = True, dayfirst: bool = False) -> pd.Series:
    s = s.copy()

    if pd.api.types.is_numeric_dtype(s):
        vals = pd.to_numeric(s.dropna(), errors="coerce")
        med = vals.abs().median() if len(vals) else 0
        unit = "ms" if med > 1e11 else "s"
        dt = pd.to_datetime(s, unit=unit, utc=utc, errors="coerce")
    else:
        dt = pd.to_datetime(s, utc=utc, errors="coerce", dayfirst=dayfirst)

    return dt



@lru_cache(maxsize=8)
def load_leg_windows(leg_start_end_path: str, *, sheet_name=0) -> pd.DataFrame:
    legs = pd.read_excel(leg_start_end_path, sheet_name=sheet_name)

    legs = legs.rename(columns={
        "Leg Number": "leg",
        "Start time (UTC)": "start",
        "End time (UTC)": "end",
    })


    legs["start"] = to_canonical_utc_datetime(legs["start"])
    legs["end"]   = to_canonical_utc_datetime(legs["end"])

    # Remove incomplete rows
    legs = legs.dropna(subset=["leg", "start", "end"])

    # Validate the remaining rows
    bad = legs[legs["end"] <= legs["start"]]
    if not bad.empty:
        raise ValueError(
            f"Bad leg window rows found:\n{bad[['leg','start','end']]}"
        )

    legs["leg"] = legs["leg"].astype(int)
    return legs[["leg", "start", "end"]].sort_values("leg").reset_index(drop=True)




@lru_cache(maxsize=8)
def load_pressure_removal_rules(sooguard_log_path: str, *, sheet_name="Pressure_removal") -> pd.DataFrame:
    df = pd.read_excel(sooguard_log_path, sheet_name=sheet_name)

    df = df.rename(columns={
        "Leg number": "leg",
        "Remove data when pressure is under": "pressure_under",
    })

    # Coerce types
    df["leg"] = pd.to_numeric(df["leg"], errors="coerce")
    df["pressure_under"] = pd.to_numeric(df["pressure_under"], errors="coerce")

    bad = df[df["leg"].isna() | df["pressure_under"].isna()]
    if not bad.empty:
        raise ValueError(f"Bad pressure removal rows found:\n{bad[['leg','pressure_under']]}")

    df["leg"] = df["leg"].astype(int)

    # Optional: detect duplicates per leg
    dup = df[df.duplicated("leg", keep=False)]
    if not dup.empty:
        raise ValueError(f"Duplicate leg rows found in pressure removal sheet:\n{dup}")

    return df[["leg", "pressure_under"]].sort_values("leg").reset_index(drop=True)