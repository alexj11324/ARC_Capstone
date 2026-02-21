import geopandas as gpd
import pandas as pd
import numpy as np

import requests
import zipfile
import io
import os
from bs4 import BeautifulSoup


def download_potential_flood_zip(storm_id, advisory_num=None, output_dir='./rasters', max_files=3):
    """
    Download and unzip the last N potential flooding/inundation tidalmask zip files.
    Scans available advisories and downloads the most recent ones matching *_tidalmask.zip.
    Only the .tif raster is kept after extraction; all other files are removed.

    Parameters:
    -----------
    storm_id : str
        Storm identifier (e.g., 'AL092017')
    advisory_num : str or int, optional
        If provided, download only that specific advisory.
        If None, auto-discover and download the last `max_files` advisories.
    output_dir : str
        Directory to save extracted .tif files
    max_files : int
        Number of most recent matching files to download (default: 3)

    Returns:
    --------
    list of str
        Paths to the extracted .tif files in output_dir
    """
    BASE_URL = "https://www.nhc.noaa.gov/gis/inundation/forecasts/"
    PATTERN = "_tidalmask.zip"

    # Format storm ID
    storm_id = storm_id.upper()
    storm_id_short = storm_id[:4] + storm_id[6:]

    os.makedirs(output_dir, exist_ok=True)

    # --- Resolve which filenames to download ---
    if advisory_num is not None:
        # Single specific advisory
        adv_str = f"{int(advisory_num):02d}" if isinstance(advisory_num, int) else advisory_num
        filenames = [f"{storm_id_short}_{adv_str}{PATTERN}"]
    else:
        # Auto-discover by scraping the directory listing
        try:
            response = requests.get(BASE_URL)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            # Collect all links matching <storm_id_short>_<adv>_tidalmask.zip
            filenames = sorted([
                a["href"] for a in soup.find_all("a", href=True)
                if a["href"].startswith(storm_id_short) and a["href"].endswith(PATTERN)
            ])

            if not filenames:
                print(f"No files found matching pattern '{storm_id_short}*{PATTERN}' at {BASE_URL}")
                return []

            # Keep only the last N
            filenames = filenames[-max_files:]
            print(f"Found {len(filenames)} matching file(s) to download: {filenames}")

        except requests.exceptions.RequestException as e:
            print(f"Error fetching directory listing: {e}")
            return []

    # --- Download and unzip each file ---
    extracted_dirs = []

    for filename in filenames:
        url = BASE_URL + filename
        zip_path = os.path.join(output_dir, filename)

        # Download
        try:
            response = requests.get(url)
            response.raise_for_status()

            with open(zip_path, "wb") as f:
                f.write(response.content)
            print(f"Downloaded: {filename}")

        except requests.exceptions.RequestException as e:
            print(f"Error downloading {filename}: {e}")
            continue

        # Unzip into a subdirectory named after the file (without .zip)
        extract_dir = os.path.join(output_dir, filename.replace(".zip", ""))
        os.makedirs(extract_dir, exist_ok=True)

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)

            # Remove the zip after extraction
            os.remove(zip_path)

            # Find the .tif file and move it up to output_dir, delete everything else
            tif_files = [f for f in os.listdir(extract_dir) if f.lower().endswith('.tif')]
            if tif_files:
                tif_src = os.path.join(extract_dir, tif_files[0])
                tif_dst = os.path.join(output_dir, tif_files[0])
                os.rename(tif_src, tif_dst)
                print(f"Extracted: {filename} → {tif_dst}")
                extracted_dirs.append(tif_dst)
            else:
                print(f"Warning: No .tif file found in {filename}")

            # Clean up the subdirectory and any remaining files
            for leftover in os.listdir(extract_dir):
                os.remove(os.path.join(extract_dir, leftover))
            os.rmdir(extract_dir)

        except zipfile.BadZipFile as e:
            print(f"Error extracting {filename}: {e}")

    return extracted_dirs


if __name__ == "__main__":
    storms = ['al042024', 'al092024', 'al092022', 'al092021', 'al102023', 'al142024', 'al062018', 'al142018']
    for storm in storms:
        download_potential_flood_zip(storm_id=storm)