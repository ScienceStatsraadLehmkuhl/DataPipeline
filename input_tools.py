import os
import pandas as pd
from pathlib import Path
from DataPipeline.fromzipxmltojson import convert_zips_to_csvs
from DataPipeline.fileformatconversion import  convert_jsons_to_csvs, copy_csv_files, convert_cnv_to_csv
from DataPipeline.preprocessing import from_csvs_to_csv


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
    full_path: str
        The complete name of the folder 
    """
    parent = Path(parent_path).parent

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
        
    """
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


def _latest_mtime(folder, exts=None):
    """Most recent mtime among files in `folder` (optionally filtered by
    extension). Returns None if the folder doesn't exist or is empty."""
    if not folder or not os.path.isdir(folder):
        return None
    mtimes = [
        os.path.getmtime(os.path.join(folder, name))
        for name in os.listdir(folder)
        if os.path.isfile(os.path.join(folder, name))
        and (exts is None or name.lower().endswith(exts))
    ]
    return max(mtimes) if mtimes else None



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
):
    """
    Ensure the combined CSV exists at exp_folder_name/output_file and
    reflects the latest raw inputs.

    Rules:
      1) Combined file exists AND no raw input is newer -> reuse it.
      2) Else output_folder has CSVs AND none of them are older than the
         raw inputs -> (re)combine those.
      3) Else input_folder_name missing/has no relevant files -> raise.
      4) Else rebuild per-file CSVs from input_folder_name, then combine.

    A raw file newer than an existing artifact means new data arrived
    since that artifact was built -> treated as stale -> rebuilt.
    """
    combined_path = os.path.join(exp_folder_name, output_file)
    raw_mtime = _latest_mtime(input_folder_name, RELEVANT_INPUT_EXTS)

    # 1) Reuse combined file only if at least as fresh as raw inputs.
    if os.path.exists(combined_path):
        if raw_mtime is None or os.path.getmtime(combined_path) >= raw_mtime:
            return combined_path
        # else: new raw data landed since this was built -> fall through

    # 2) Reuse per-file CSVs only if at least as fresh as raw inputs.
    if _has_output_csvs(output_folder_name):
        output_mtime = _latest_mtime(output_folder_name, (".csv",))
        if raw_mtime is None or (output_mtime is not None and output_mtime >= raw_mtime):
            os.makedirs(exp_folder_name, exist_ok=True)
            from_csvs_to_csv(output_folder_name, combined_path)
            return combined_path

        # 2b) Some raw files are newer -> reprocess only those, by filename pairing.
        if _has_relevant_inputs(input_folder_name):
            stale_files = _stale_raw_files(
                input_folder_name, output_folder_name, RELEVANT_INPUT_EXTS
            )
            if stale_files:
                _process_raw_files(input_folder_name, output_folder_name, stale_files)
            os.makedirs(exp_folder_name, exist_ok=True)
            from_csvs_to_csv(output_folder_name, combined_path)
            return combined_path
        # else: fall through to rule 3/4

    # 3) No relevant raw inputs to (re)build from.
    if not _has_relevant_inputs(input_folder_name):
        raise FileNotFoundError(
            f"         1. Combined file not found or stale\n"
            f"         2. No fresh CSVs found in output folder to combine\n"
            f"         3. Raw input folder missing or has no relevant files"
        )

    # 4) Rebuild everything from raw.
    os.makedirs(output_folder_name, exist_ok=True)
    os.makedirs(exp_folder_name, exist_ok=True)


    csvs_processed = False
    for filename in os.listdir(input_folder_name):
        filepath = os.path.join(input_folder_name, filename)
        if not os.path.isfile(filepath):
            continue

        lower = filename.lower()
        if lower.endswith(".zip"):
            convert_zips_to_csvs(filepath, output_folder_name)
        elif lower.endswith(".json"):
            convert_jsons_to_csvs(input_folder_name, filepath, output_folder_name)
        elif lower.endswith(".csv") and not csvs_processed:
            copy_csv_files(input_folder_name, output_folder_name)
            csvs_processed = True
        elif lower.endswith(".cnv"):
            convert_cnv_to_csv(input_folder_name, filename, output_folder_name)

    from_csvs_to_csv(output_folder_name, combined_path)
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
) -> pd.DataFrame:
    combined_path = ensure_combined_csv(
        input_folder_name=input_folder_name,
        output_folder_name=output_folder_name,
        exp_folder_name=exp_folder_name,
        output_file=output_file,
    )
    return load_combined_csv(combined_path)