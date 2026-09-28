"""
Format audit: which column layouts each instrument's per-file CSVs come in,
across every leg of a cruise, and what each layout means for the rename step.

Raw exports change format mid-cruise (logger reconfigured, a manual whole-leg
export next to the automatic 15-min files, ...). Every per-file CSV in
`{PROCESSED_ROOT}/{cruise}/LEG{leg}/{EXPERIMENT}/{instrument}/` is what
`from_csvs_to_csv` concatenates, so this reads only the header, first and
last line of each of them (nothing is written except the report) and groups
files by their exact set of column names (a "layout").

Sheets in the report workbook:
- summary    one row per instrument: layouts, files, and problem counts
- layouts    one row per instrument layout: files, legs, time span, raw file
             types, time source column + sample value, which VARIABLES it
             provides / misses, and the columns the rename whitelist drops
- conflicts  canonical names reached from more than one raw column name
             across layouts. After concatenation both raw columns exist, so
             keep_and_rename produces duplicate columns and one variant's
             values are lost (the leg-21 HDT heading bug)
- overlaps   per leg, pairs of layouts whose files cover the same time
             (duplicate records from two exports)
- files      one row per per-file CSV

    python -m DataPipeline.ingest.format_audit --cruise 2025_2026_OOE2
    python -m DataPipeline.ingest.format_audit --cruise 2026_SaS --legs 3 4 --only-instruments HDT VTG
"""
import argparse
import csv
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from DataPipeline.ingest.fileformatconversion import get_time_from_filename
from DataPipeline.ingest.input_tools import RELEVANT_INPUT_EXTS, give_me_full_folder_name
from DataPipeline.ingest.preprocessing import TIME_ALIAS, to_utc
from DataPipeline.settings import CRUISE, PROCESSED_ROOT, RAW_ROOT
from DataPipeline.vocabulary import INSTRUMENTS, PREFERRED_TIME_COLUMN, RENAME_COLUMNS, VARIABLES

# Derived products or non-CSV folders: nothing raw to audit
SKIP_INSTRUMENTS = {"GPS-MERGED-SOURCES", "EK80-RAW", "EK80_echos_ncdf"}
N_WORKERS = 32
TAIL_BYTES = 16384


def _legs(cruise):
    root = Path(PROCESSED_ROOT) / cruise
    legs = [int(m.group(1)) for p in root.iterdir() if (m := re.fullmatch(r"LEG(\d+)", p.name)) and p.is_dir()]
    return sorted(legs)


def _raw_folder(cruise, leg, experiment, instrument):
    """Raw input folder, mirroring input_folders_processer (None if absent)."""
    leg_root = give_me_full_folder_name(f"{RAW_ROOT}/{cruise}/LEG{leg}", str(leg))
    if leg_root is None:
        return None
    sub = f"{experiment}/SEAPATH/{instrument}" if experiment == "NAVIGATION" else f"{experiment}/{instrument}"
    folder = Path(leg_root) / sub
    return folder if folder.is_dir() else None


def _read_head_tail(path):
    """(header, first data row, last data row) of a CSV, reading only its ends."""
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        first = next(reader, None)
        if first is None:
            return header, None, None
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - TAIL_BYTES))
        lines = [ln for ln in f.read().splitlines() if ln.strip()]
    last = next(csv.reader([lines[-1]])) if lines else first
    if len(last) != len(header):   # tail cut mid-row or a stray line: fall back to the first row
        last = first
    return header, first, last


def _scan_file(job):
    leg, experiment, instrument, path, raw_ext = job
    rec = dict(leg=leg, experiment=experiment, instrument=instrument, file=path.name, raw_ext=raw_ext,
               header=None, time_col=None, first_raw=None, last_raw=None, error=None)
    try:
        header, first, last = _read_head_tail(path)
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
        return rec
    if header is None:
        rec["error"] = "empty file"
        return rec
    rec["header"] = tuple(header)
    rec["n_cols"] = len(header)
    rec["empty"] = first is None

    preferred = PREFERRED_TIME_COLUMN.get(instrument)
    time_col = preferred if preferred in header else next((c for c in TIME_ALIAS if c in header), None)
    rec["time_col"] = time_col or "(filename)"
    if time_col and first is not None:
        i = header.index(time_col)
        rec["first_raw"] = first[i] if i < len(first) else None
        rec["last_raw"] = last[i] if i < len(last) else None
    elif time_col is None:
        try:
            rec["first_raw"] = rec["last_raw"] = str(get_time_from_filename(path.name, 1)[0])
        except Exception:
            pass
    return rec


def _jobs(cruise, legs, only_instruments):
    for leg in legs:
        for experiment, instruments in INSTRUMENTS.items():
            for instrument in instruments:
                if instrument in SKIP_INSTRUMENTS:
                    continue
                if only_instruments and instrument not in only_instruments:
                    continue
                folder = Path(PROCESSED_ROOT) / cruise / f"LEG{leg}" / experiment / instrument
                if not folder.is_dir():
                    continue
                raw = _raw_folder(cruise, leg, experiment, instrument)
                raw_ext = {}
                if raw is not None:
                    # One stem can have several files (Seabird .cnv/.ros/.xmlcon):
                    # report the type the pipeline actually converts.
                    for name in os.listdir(raw):
                        stem, ext = os.path.splitext(name)
                        ext = ext.lower()
                        if stem not in raw_ext or (ext in RELEVANT_INPUT_EXTS and raw_ext[stem] not in RELEVANT_INPUT_EXTS):
                            raw_ext[stem] = ext
                for name in sorted(os.listdir(folder)):
                    if name.lower().endswith(".csv"):
                        stem = os.path.splitext(name)[0]
                        yield leg, experiment, instrument, folder / name, raw_ext.get(stem, "(no raw match)")


def _parse_times(files):
    """first/last time columns, parsed per (instrument, time_col) group so to_utc sees one format at a time."""
    for dst in ("first_time", "last_time"):
        files[dst] = pd.Series(pd.NaT, index=files.index, dtype="datetime64[ns, UTC]")
    for _, idx in files.groupby(["instrument", "layout", "time_col"], dropna=False).groups.items():
        for src, dst in (("first_raw", "first_time"), ("last_raw", "last_time")):
            vals = files.loc[idx, src]
            ok = vals.notna()
            if ok.any():
                try:
                    files.loc[vals[ok].index, dst] = to_utc(vals[ok].astype(str)).to_numpy()
                except Exception:
                    pass
    return files


def _union(intervals):
    out = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _intersection(u1, u2):
    i = j = 0
    out = []
    while i < len(u1) and j < len(u2):
        a, b = max(u1[i][0], u2[j][0]), min(u1[i][1], u2[j][1])
        if a < b:
            out.append((a, b))
        if u1[i][1] < u2[j][1]:
            i += 1
        else:
            j += 1
    return out


def audit(cruise, legs=None, only_instruments=None):
    legs = legs or _legs(cruise)
    jobs = list(_jobs(cruise, legs, only_instruments))
    print(f"[AUDIT] {cruise}: scanning {len(jobs)} per-file CSV(s) in {len(legs)} leg(s)")
    with ThreadPoolExecutor(N_WORKERS) as pool:
        recs = list(pool.map(_scan_file, jobs))

    files = pd.DataFrame(recs)
    errors = files[files["error"].notna()]
    files = files[files["error"].isna()].copy()

    # Layout id per instrument: L1, L2, ... in order of first appearance (by leg, then filename)
    layout_ids = {}
    for (inst, header) in files[["instrument", "header"]].itertuples(index=False):
        key = (inst, frozenset(header))
        if key not in layout_ids:
            layout_ids[key] = f"L{sum(1 for k in layout_ids if k[0] == inst) + 1}"
    files["layout"] = [layout_ids[(i, frozenset(h))] for i, h in zip(files["instrument"], files["header"])]
    files = _parse_times(files)

    layouts, conflicts, overlaps, summary = [], [], [], []
    for (experiment, instrument), g in files.groupby(["experiment", "instrument"], sort=False):
        rename_map = RENAME_COLUMNS.get(experiment, {}).get(instrument)
        variables = VARIABLES.get(experiment, {}).get(instrument, [])
        # Only variables that can come straight from a raw column (not derived ones like true wind)
        raw_vars = [v for v in variables if rename_map is None or v in rename_map or v in rename_map.values()]

        canonical_sources = defaultdict(set)   # canonical name -> raw column names mapping to it
        canonical_layouts = defaultdict(set)
        inst_layouts = []
        for layout, lg in g.groupby("layout", sort=False):
            header = lg["header"].iloc[0]
            provided = set(header)
            if rename_map is not None:
                for c in header:
                    if c in rename_map:
                        canonical_sources[rename_map[c]].add(c)
                        canonical_layouts[rename_map[c]].add(layout)
                provided |= {rename_map[c] for c in header if c in rename_map}
                dropped = [c for c in header if c not in rename_map]
            else:
                dropped = []
            missing = [v for v in raw_vars if v not in provided]
            sample = lg.dropna(subset=["first_raw"])
            inst_layouts.append(layout)
            layouts.append(dict(
                experiment=experiment, instrument=instrument, layout=layout,
                n_files=len(lg), n_empty_files=int(lg["empty"].sum()),
                legs=", ".join(str(x) for x in sorted(lg["leg"].unique())),
                first_time=lg["first_time"].min(), last_time=lg["last_time"].max(),
                raw_types=", ".join(f"{k} x{v}" for k, v in lg["raw_ext"].value_counts().items()),
                time_col=", ".join(lg["time_col"].dropna().unique()),
                time_sample=sample["first_raw"].iloc[0] if len(sample) else None,
                n_unparsed_times=int((lg["first_raw"].notna() & lg["first_time"].isna()).sum()),
                example_file=f"LEG{lg['leg'].iloc[0]}/{lg['file'].iloc[0]}",
                missing_variables=", ".join(missing),
                dropped_columns=" | ".join(dropped),
                columns=" | ".join(header),
                no_rename_map=rename_map is None,
            ))

        inst_conflicts = 0
        for canonical, raws in canonical_sources.items():
            if len(raws) > 1 and canonical != "time":
                inst_conflicts += 1
                conflicts.append(dict(
                    experiment=experiment, instrument=instrument, canonical=canonical,
                    raw_columns=" | ".join(sorted(raws)), layouts=", ".join(sorted(canonical_layouts[canonical])),
                ))

        inst_overlaps = 0
        for leg, lg in g.groupby("leg"):
            unions = {}
            for layout, fl in lg.dropna(subset=["first_time", "last_time"]).groupby("layout"):
                unions[layout] = _union(list(zip(fl["first_time"], fl["last_time"])))
            names = sorted(unions)
            for a_i, a in enumerate(names):
                for b in names[a_i + 1:]:
                    inter = _intersection(unions[a], unions[b])
                    if inter:
                        inst_overlaps += 1
                        overlaps.append(dict(
                            experiment=experiment, instrument=instrument, leg=leg, layout_a=a, layout_b=b,
                            overlap_hours=round(sum((y - x).total_seconds() for x, y in inter) / 3600, 2),
                            overlap_start=inter[0][0], overlap_end=inter[-1][1],
                        ))

        inst_rows = [r for r in layouts if r["instrument"] == instrument and r["experiment"] == experiment]
        summary.append(dict(
            experiment=experiment, instrument=instrument, n_files=len(g), n_layouts=len(inst_layouts),
            layouts_missing_variables=sum(1 for r in inst_rows if r["missing_variables"]),
            rename_conflicts=inst_conflicts, leg_overlaps=inst_overlaps,
            unparsed_times=sum(r["n_unparsed_times"] for r in inst_rows),
            no_rename_map=rename_map is None,
        ))

    files_out = files.drop(columns=["header"]).sort_values(["experiment", "instrument", "leg", "file"])
    sheets = {
        "summary": pd.DataFrame(summary),
        "layouts": pd.DataFrame(layouts),
        "conflicts": pd.DataFrame(conflicts, columns=["experiment", "instrument", "canonical", "raw_columns", "layouts"]),
        "overlaps": pd.DataFrame(overlaps, columns=["experiment", "instrument", "leg", "layout_a", "layout_b",
                                                    "overlap_hours", "overlap_start", "overlap_end"]),
        "files": files_out,
    }
    if len(errors):
        sheets["unreadable"] = errors[["leg", "experiment", "instrument", "file", "error"]]
    return sheets


def write_report(sheets, out_path):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path) as xw:
        for name, df in sheets.items():
            df = df.copy()
            for c in df.columns:   # Excel can't store tz-aware datetimes
                if isinstance(df[c].dtype, pd.DatetimeTZDtype):
                    df[c] = df[c].dt.tz_localize(None)
            df.to_excel(xw, sheet_name=name, index=False)
    print(f"[AUDIT] report written to {out_path}")


def print_summary(sheets):
    s = sheets["summary"]
    flagged = s[(s["n_layouts"] > 1) | (s["layouts_missing_variables"] > 0) | (s["rename_conflicts"] > 0)
                | (s["leg_overlaps"] > 0) | (s["unparsed_times"] > 0)]
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(s.to_string(index=False))
        print(f"\n[AUDIT] {len(flagged)} of {len(s)} instrument(s) have more than one layout or a problem")
        if len(sheets["conflicts"]):
            print("\nRename conflicts (values of one raw variant are lost in keep_and_rename):")
            print(sheets["conflicts"].to_string(index=False))


def parse_args():
    p = argparse.ArgumentParser(description="Audit the column layouts of every instrument's per-file CSVs.")
    p.add_argument("--cruise", default=CRUISE)
    p.add_argument("--legs", nargs="+", type=int, default=None, help="Legs to scan (default: every LEG* folder)")
    p.add_argument("--only-instruments", nargs="+", default=None)
    p.add_argument("--out", default=None,
                   help="Report path (default: {PROCESSED_ROOT}/{cruise}/format_audit/{cruise}_format_audit.xlsx)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    sheets = audit(args.cruise, legs=args.legs, only_instruments=args.only_instruments)
    out = args.out or Path(PROCESSED_ROOT) / args.cruise / "format_audit" / f"{args.cruise}_format_audit.xlsx"
    write_report(sheets, out)
    print_summary(sheets)
