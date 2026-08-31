import csv, json
from pathlib import Path
import pandas as pd
import os, shutil
import re



def get_time_from_filename(filename, nrows, freq="1s", utc=True):
    """
    Create a DatetimeIndex from a timestamp embedded in a filename.

    Parameters
    ----------
    filename : str or Path
        File path or filename containing a timestamp of the form YYYYMMDDHHMM.
    nrows : int
        Number of timestamps to generate.
    freq : str, default="1s"
        Sampling frequency (e.g. "1s", "100ms", "10Hz" if converted).
    utc : bool, default=True
        Return timezone-aware UTC timestamps.

    Returns
    -------
    pandas.DatetimeIndex

    Raises
    ------
    ValueError
        If no YYYYMMDDHHMM timestamp is found in the filename.
    """

    basename = os.path.splitext(os.path.basename(filename))[0]

    # Find first 12-digit timestamp
    match = re.search(r"(\d{12})", basename)
    if match is None:
        raise ValueError(
            f"No YYYYMMDDHHMM timestamp found in filename '{basename}'."
        )

    start = pd.to_datetime(
        match.group(1),
        format="%Y%m%d%H%M",
        utc=utc,
    )

    return pd.date_range(
        start=start,
        periods=nrows,
        freq=freq,
    )



def jsonlines_file_to_csv(json_path: Path, csv_path: Path) -> None:
    rows = []
    headers = set()

    with json_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            # tolerate missing "{"
            if not line.startswith("{"):
                line = "{" + line
            if not line.endswith("}"):
                line = line + "}"

            obj = json.loads(line)
            rows.append(obj)
            headers.update(obj.keys())

    headers = sorted(headers)
    if "timestamp" in headers:
        headers.remove("timestamp")
        headers = ["timestamp"] + headers

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as out:
        w = csv.DictWriter(out, fieldnames=headers)
        w.writeheader()
        w.writerows(rows)


def convert_jsons_to_csvs(input_folder_name: str | Path,
                            filename: str,
                            output_folder_name: str | Path) -> Path:
    """
    Reads ONE json file named `filename` from `input_folder_name`
    and saves it as a CSV into `output_folder_name`.
    Returns the created CSV path.
    """
    in_dir = Path(input_folder_name)
    out_dir = Path(output_folder_name)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = in_dir / filename                 # read from input folder
    csv_path = out_dir / (Path(filename).stem + ".csv")  # write to output folder

    jsonlines_file_to_csv(json_path, csv_path)
    return csv_path


def copy_csv_files(input_folder_name, output_folder_name, filenames=None):
    input_folder_name = os.path.abspath(input_folder_name)
    output_folder_name = os.path.abspath(output_folder_name)

    if input_folder_name == output_folder_name:
        raise ValueError("Input and output folders must be different.")

    os.makedirs(output_folder_name, exist_ok=True)

    if filenames is None:
        csv_files = sorted([
            f for f in os.listdir(input_folder_name)
            if f.lower().endswith(".csv")
            and os.path.isfile(os.path.join(input_folder_name, f))
        ])
    else:
        csv_files = sorted(filenames)

    def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
        if "time" in df.columns:
            df = df.rename(columns={"time": "Timestamp"})
        return df

    def write_no_quotes(df, out_path):
        df.to_csv(
            out_path,
            index=False,
            quoting=csv.QUOTE_MINIMAL,
            escapechar="\\",
        )

    for file in csv_files:
        df = pd.read_csv(os.path.join(input_folder_name, file))
        df = normalize_df(df)
        write_no_quotes(df, os.path.join(output_folder_name, file))




def convert_cnv_to_csv(input_folder_name: str | Path,
                       filename: str,
                       output_folder_name: str | Path) -> Path:
    """
    Reads ONE CNV file named `filename` from `input_folder_name`
    and saves it as a CSV into `output_folder_name`.

    - Column names are extracted automatically from '# name X = ...'
    - Metadata from '*' and '**' lines are added as new columns
    """
    in_dir = Path(input_folder_name)
    out_dir = Path(output_folder_name)
    out_dir.mkdir(parents=True, exist_ok=True)

    cnv_path = in_dir / filename
    csv_path = out_dir / (cnv_path.stem + ".csv")

    column_names = []
    metadata = {}
    data_start_idx = None

    with open(cnv_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    for i, line in enumerate(lines):
        line = line.strip()

        # -------- column headers --------
        if line.startswith("# name"):
            # example:
            # # name 0 = prdM: Pressure, Strain Gauge [db]
            match = re.search(r"# name \d+ = ([^:]+)", line)
            if match:
                column_names.append(match.group(1))

        # -------- metadata --------
        elif line.startswith("*"):
            # remove leading * or **
            clean = line.lstrip("*").strip()

            if "=" in clean:
                key, value = clean.split("=", 1)
            elif ":" in clean:
                key, value = clean.split(":", 1)
            else:
                continue

            key = key.strip().replace(" ", "_")
            value = value.strip()
            metadata[key] = value

        # -------- detect data start --------
        elif line and not line.startswith("#"):
            data_start_idx = i
            break

    if data_start_idx is None:
        raise ValueError("No data section found in CNV file.")

    # -------- read numeric data --------
    df = pd.read_csv(
        cnv_path,
        skiprows=data_start_idx,
        sep=r"\s+",
        header=None,
    )

    # apply extracted column names
    df.columns = column_names

    # -------- attach metadata as columns --------
    for key, value in metadata.items():
        df[key] = value

    df.to_csv(csv_path, index=False)
    return csv_path



