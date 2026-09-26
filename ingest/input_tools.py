import os
import pandas as pd
from pathlib import Path
from DataPipeline.ingest.fromzipxmltojson import convert_zips_to_csvs
from DataPipeline.ingest.fileformatconversion import  convert_jsons_to_csvs, copy_csv_files, convert_cnv_to_csv
from DataPipeline.ingest.preprocessing import from_csvs_to_csv, format_time


def input_folders_processer(leg, experiment, instrument, cruise):
    """
    Provide the full path of the input and output as a function of the instrument, experiment, leg
    """
    input_folder_root = f"/run/user/1000/gvfs/smb-share:server=sl-nas.local,share=geomatics/{cruise}/LEG{leg}"
    input_folder_root = give_me_full_folder_name(input_folder_root, leg)

    # Exception: NAVIGATION has {experiment}/SEAPATH/{instrument}
    if experiment == "NAVIGATION":
        input_folder_name = f"{input_folder_root}/{experiment}/SEAPATH/{instrument}"
    else:
        input_folder_name = f"{input_folder_root}/{experiment}/{instrument}"

    output_folder_name = f"/run/user/1000/gvfs/smb-share:server=sl-nas.local,share=processed_data/{cruise}/LEG{leg}/{experiment}/{instrument}"
    exp_folder_name = f"/run/user/1000/gvfs/smb-share:server=sl-nas.local,share=processed_data/{cruise}/LEG{leg}/{experiment}"
    fig_png_folder_name = f"/run/user/1000/gvfs/smb-share:server=sl-nas.local,share=processed_data/{cruise}/LEG{leg}/FIGURES/PNG"
    fig_pdf_folder_name = f"/run/user/1000/gvfs/smb-share:server=sl-nas.local,share=processed_data/{cruise}/LEG{leg}/FIGURES/PDF"


    base_name = f"{cruise}_LEG{leg}_{experiment}_{instrument}"
    output_file = str(Path(exp_folder_name) / f"{base_name}.csv")
    cleaned_output_file = str(Path(exp_folder_name) / f"{base_name}_cleaned.csv")

    return (
        input_folder_name,
        output_folder_name,
        exp_folder_name,
        fig_png_folder_name,
        fig_pdf_folder_name,
        cleaned_output_file,
        output_file,
        base_name,
    )


def give_me_full_folder_name(parent_path, leg):
    """
    Give me the full name of a directory given a part of it
    
    parent_path
    leg

    return
    ------
    full_path: str | None
        The complete name of the folder, or None if it can't be found --
        including when the cruise folder itself is missing (raw data removed
        or the geomatics share not mounted), so callers fall back to what's
        already in processed_data instead of crashing.
    """
    parent = Path(parent_path).parent
    if not parent.is_dir():
        return None

    for fname in os.listdir(parent):
        if "LEG" + leg + "_" in fname:
            return str(Path.joinpath(parent, fname))
    return None


def update_csv(df, file_path):
    """
    Write a new CSV file with the contents of a pandas DataFrame.

    Parameters:
        df (pd.DataFrame): The DataFrame to write.
        file_path (str): Path to the CSV file to overwrite.

    The `time` column is always written in the canonical format
    (preprocessing.TIME_FORMAT), whatever form it has in `df`.
    """
    if "time" in df.columns:
        df = df.assign(time=format_time(df["time"]))
    df.to_csv(file_path, index=False)


RELEVANT_INPUT_EXTS = (".zip", ".json", ".csv", ".cnv")


def _has_relevant_inputs(folder: str | None) -> bool:
    if not folder or not os.path.isdir(folder):
        return False
    return any(
        name.lower().endswith(RELEVANT_INPUT_EXTS)
        for name in os.listdir(folder)
        if os.path.isfile(os.path.join(folder, name))
    )


def _has_output_csvs(folder: str) -> bool:
    if not os.path.isdir(folder):
        return False
    return any(
        name.lower().endswith(".csv")
        for name in os.listdir(folder)
        if os.path.isfile(os.path.join(folder, name))
    )


def _stale_raw_files(input_folder_name, output_folder_name, extensions):
    """
    Return the list of raw filenames whose corresponding output CSV
    (same stem, .csv extension) is missing or older than the raw file.
    """
    stale = []
    for filename in os.listdir(input_folder_name):
        filepath = os.path.join(input_folder_name, filename)
        if not os.path.isfile(filepath) or not filename.lower().endswith(extensions):
            continue

        stem, _ = os.path.splitext(filename)
        output_path = os.path.join(output_folder_name, stem + ".csv")

        if not os.path.exists(output_path):
            stale.append(filename)
        elif os.path.getmtime(filepath) > os.path.getmtime(output_path):
            stale.append(filename)

    return stale


def _process_raw_files(input_folder_name, output_folder_name, filenames):
    csvs_to_copy = []
    for filename in filenames:
        filepath = os.path.join(input_folder_name, filename)
        lower = filename.lower()
        if lower.endswith(".zip"):
            convert_zips_to_csvs(filepath, output_folder_name)
        elif lower.endswith(".json"):
            convert_jsons_to_csvs(input_folder_name, filepath, output_folder_name)
        elif lower.endswith(".csv"):
            csvs_to_copy.append(filename)
        elif lower.endswith(".cnv"):
            convert_cnv_to_csv(input_folder_name, filename, output_folder_name)

    if csvs_to_copy:
        copy_csv_files(input_folder_name, output_folder_name, filenames=csvs_to_copy)





def ensure_combined_csv(
    input_folder_name: str | None,
    output_folder_name: str,
    exp_folder_name: str,
    output_file: str,
    preferred_time_col: str | None = None,
    force_rebuild: bool = False,
):
    """
    Ensure the combined CSV exists at exp_folder_name/output_file and
    reflects the latest raw inputs.

    `force_rebuild` recombines the per-file CSVs even when nothing is stale
    (used to migrate a combined file whose `time` column isn't canonical).

    Staleness is decided per raw file via _stale_raw_files (does this raw
    file have a matching output CSV, and is that CSV at least as new as the
    raw file?), not by comparing a single "latest mtime in the folder"
    number against the combined file's mtime. Raw files landing on the
    geomatics share can carry an old, original-recording mtime even when
    they are genuinely new arrivals, so a single-number comparison can miss
    them entirely -- per-file existence can't be fooled that way.

    Rules:
      1) Combined file exists AND no raw file is missing/stale -> reuse it.
      2) Else reprocess whichever raw files are missing/stale, then
         (re)combine every per-file CSV in output_folder_name.
      3) Else (no relevant raw inputs at all) fall back to whatever's
         already in output_folder_name, or raise if there's nothing to
         build from.
    """
    combined_path = os.path.join(exp_folder_name, output_file)
    has_inputs = _has_relevant_inputs(input_folder_name)
    stale_files = (
        _stale_raw_files(input_folder_name, output_folder_name, RELEVANT_INPUT_EXTS)
        if has_inputs else []
    )

    # 1) Reuse combined file only if every raw file is already represented.
    if os.path.exists(combined_path) and not stale_files and not force_rebuild:
        return combined_path

    if not has_inputs:
        if os.path.exists(combined_path) and not force_rebuild:
            return combined_path
        if not _has_output_csvs(output_folder_name):
            if os.path.exists(combined_path):
                return combined_path  # force_rebuild, but nothing to rebuild from
            raise FileNotFoundError(
                f"         1. Combined file not found\n"
                f"         2. No CSVs found in output folder to combine\n"
                f"         3. Raw input folder missing or has no relevant files"
            )
    else:
        os.makedirs(output_folder_name, exist_ok=True)
        if stale_files:
            _process_raw_files(input_folder_name, output_folder_name, stale_files)

    os.makedirs(exp_folder_name, exist_ok=True)
    from_csvs_to_csv(output_folder_name, combined_path, preferred_time_col=preferred_time_col)
    return combined_path


def load_combined_csv(combined_path: str) -> pd.DataFrame:
    df = pd.read_csv(combined_path, low_memory=False)
    df = df.loc[:, ~df.columns.str.contains("^Unnamed")]  # drop unnamed columns
    return df


def import_and_process_sources(
    input_folder_name: str | None,
    output_folder_name: str,
    exp_folder_name: str,
    output_file: str,
    preferred_time_col: str | None = None,
) -> pd.DataFrame:
    kwargs = dict(
        input_folder_name=input_folder_name,
        output_folder_name=output_folder_name,
        exp_folder_name=exp_folder_name,
        output_file=output_file,
        preferred_time_col=preferred_time_col,
    )
    combined_path = ensure_combined_csv(**kwargs)
    df = load_combined_csv(combined_path)

    # A combined file built before the canonical time format can carry
    # mixed-precision time strings and literal "NaT" rows (lost at build time).
    # It is only rebuilt when a raw file goes stale, so migrate it here, once.
    if not _time_is_canonical(df):
        print(f"      [TIME] {os.path.basename(combined_path)} has a non-canonical 'time' column; rebuilding from per-file CSVs")
        combined_path = ensure_combined_csv(**kwargs, force_rebuild=True)
        df = load_combined_csv(combined_path)
    return df


_CANONICAL_TIME_RE = r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{6}\+00:00"


def _time_is_canonical(df: pd.DataFrame) -> bool:
    """True if df has no `time` column, or every non-empty value is in TIME_FORMAT."""
    if "time" not in df.columns:
        return True
    t = df["time"].dropna().astype(str)
    return bool(t.str.fullmatch(_CANONICAL_TIME_RE).all())