from pathlib import Path
import os
import pandas as pd
import numpy as np
from DataPipeline.fromzipxmltojson import read_csv
import csv
from DataPipeline.fileformatconversion import get_time_from_filename


TIME_ALIAS = ["System Date and Time","timestamp", "Timestamp", "NMEA_UTC_(Time)"]


def add_canonical_time(df, *, utc=True, dayfirst=False):
    """
    Find a timestamp column using TIME_ALIAS, preserve any existing 'time'
    column by renaming it, and always add a canonical 'time' column.
    """
    df = df.copy()

    # 1. Find timestamp source FIRST
    time_col = next((col for col in TIME_ALIAS if col in df.columns), None)
    if time_col is None:
        print("Warning: No timestamp column found. Skipping canonical time.")
        return df

    # 2. If the source column is already named 'time', preserve it
    if time_col == "time":
        new_name = "timestamp"
        if new_name in df.columns:
            i = 2
            while f"{new_name}_{i}" in df.columns:
                i += 1
            new_name = f"{new_name}_{i}"

        df = df.rename(columns={"time": new_name})
        time_col = new_name  # update source name

    s = df[time_col]

    # 3. Parse timestamps
    if pd.api.types.is_numeric_dtype(s):
        vals = pd.to_numeric(s.dropna(), errors="coerce")
        med = vals.abs().median() if len(vals) else 0
        unit = "ms" if med > 1e11 else "s"
        dt = pd.to_datetime(s, unit=unit, utc=utc, errors="coerce")
    else:
        dt = pd.to_datetime(s, utc=utc, errors="coerce", dayfirst=dayfirst)

    # 4. Always add canonical time
    df["time"] = dt

    return df


def ensure_time(df, filename):
    """
    Ensure the DataFrame has a canonical 'time' column.
    If no timestamp column exists, create one from the filename first.
    """
    if not any(col in df.columns for col in TIME_ALIAS):
        df = df.copy()
        df["timestamp"] = get_time_from_filename(filename, len(df))

    return add_canonical_time(df)


def from_csvs_to_csv(output_folder_name, output_file):
    """
    Combine multiple CSVs into one, adding canonical time where possible.
    """
    data_rows, keywords = [], []

    for filecsv in os.listdir(output_folder_name):
        if not filecsv.endswith(".csv"):
            continue

        # Read CSV as list of dicts
        data_row, file_keywords = read_csv(os.path.join(output_folder_name, filecsv))
        if not data_row:
            continue

        # Convert to DataFrame
        df = pd.DataFrame(data_row)

        # Add canonical time if possible
        try:
            df = ensure_time(df, os.path.join(output_folder_name, filecsv))

            # Sync keywords with whatever columns ensure_time/add_canonical_time produced
            for col in df.columns:
                if col not in file_keywords:
                    file_keywords.append(col)

        except ValueError as e:
            print(f"Warning: {e}")

        # Update global keywords
        for k in file_keywords:
            if k not in keywords:
                keywords.append(k)

        # Append rows
        data_rows += df.to_dict(orient="records")

    # Ensure output folder exists
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    # Write combined CSV
    with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=keywords)
        writer.writeheader()
        writer.writerows(data_rows)

    print(f"      Successfully wrote {len(data_rows)} records")







# def from_csvs_to_csv(output_folder_name, output_file):
#     """
#     Combine multiple CSVs into one, adding canonical time where possible.
#     """
#     data_rows, keywords = [], []

#     for filecsv in os.listdir(output_folder_name):
#         if not filecsv.endswith(".csv"):
#             continue

#         # Read CSV as list of dicts
#         data_row, file_keywords = read_csv(os.path.join(output_folder_name, filecsv))
#         if not data_row:
#             continue

#         # Convert to DataFrame
#         df = pd.DataFrame(data_row)

#         # Add canonical time if possible
#         try:
#             df = add_canonical_time(df)
#             # Ensure 'time' is in the keywords for CSV writing
#             if "time" not in file_keywords:
#                 file_keywords.append("time")
#         except KeyError:
#             pass

#         # Update global keywords
#         for k in file_keywords:
#             if k not in keywords:
#                 keywords.append(k)

#         # Append rows
#         data_rows += df.to_dict(orient="records")

#     # Ensure output folder exists
#     os.makedirs(os.path.dirname(output_file), exist_ok=True)

#     # Write combined CSV
#     with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
#         writer = csv.DictWriter(csvfile, fieldnames=keywords)
#         writer.writeheader()
#         writer.writerows(data_rows)

#     print(f"      Successfully wrote {len(data_rows)} records")