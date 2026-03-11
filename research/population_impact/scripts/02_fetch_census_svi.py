#!/usr/bin/env python3
"""Step 2: Fetch Census county population and CDC SVI data.

Downloads:
1. Census county population estimates (latest ACS 5-year)
2. CDC Social Vulnerability Index (SVI) by county

Output: census_svi_by_county.csv

Usage:
    python 02_fetch_census_svi.py [--output-dir ./data]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path

import requests

# Census API: ACS 5-year estimates (2022 latest available)
# B01001: Total population; B01001_020-025 + B01001_044-049: 65+ population
CENSUS_API_BASE = "https://api.census.gov/data/2022/acs/acs5"
CENSUS_VARS = [
    "NAME",                    # County name
    "B01001_001E",             # Total population
    "B01001_020E", "B01001_021E", "B01001_022E", "B01001_023E", "B01001_024E", "B01001_025E",  # Male 65+
    "B01001_044E", "B01001_045E", "B01001_046E", "B01001_047E", "B01001_048E", "B01001_049E",  # Female 65+
    "B19013_001E",             # Median household income
    "B25001_001E",             # Total housing units
    "B25002_003E",             # Vacant housing units
]

# CDC SVI download URL (2022 edition, county level)
SVI_URL = "https://data.cdc.gov/api/views/4d8n-kk8a/rows.csv?accessType=DOWNLOAD"
# Fallback: use SVI API endpoint
SVI_API_URL = "https://data.cdc.gov/resource/4d8n-kk8a.json"


def log(msg: str) -> None:
    print(f"[census_svi] {msg}", flush=True)


def fetch_census_population(output_dir: Path) -> Path:
    """Fetch county-level population from Census API."""
    log("Fetching Census ACS 5-year population data...")

    var_str = ",".join(CENSUS_VARS)
    url = f"{CENSUS_API_BASE}?get={var_str}&for=county:*&in=state:*"

    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log(f"Census API failed: {e}. Trying alternative approach...")
        return _fetch_census_fallback(output_dir)

    if not data or len(data) < 2:
        raise RuntimeError("Census API returned empty data")

    headers = data[0]
    rows = []
    for row in data[1:]:
        record = dict(zip(headers, row))
        state_fips = record.get("state", "")
        county_fips = record.get("county", "")
        county_fips5 = f"{state_fips}{county_fips}"

        total_pop = _safe_int(record.get("B01001_001E", 0))

        # Sum 65+ population (male + female age groups)
        elderly_cols = [
            "B01001_020E", "B01001_021E", "B01001_022E",
            "B01001_023E", "B01001_024E", "B01001_025E",
            "B01001_044E", "B01001_045E", "B01001_046E",
            "B01001_047E", "B01001_048E", "B01001_049E",
        ]
        pop_65plus = sum(_safe_int(record.get(c, 0)) for c in elderly_cols)

        rows.append({
            "county_fips5": county_fips5,
            "county_name_census": record.get("NAME", ""),
            "total_population": total_pop,
            "pop_65plus": pop_65plus,
            "pct_elderly": round(pop_65plus / total_pop, 4) if total_pop > 0 else 0,
            "median_household_income": _safe_int(record.get("B19013_001E", 0)),
            "total_housing_units": _safe_int(record.get("B25001_001E", 0)),
            "vacant_housing_units": _safe_int(record.get("B25002_003E", 0)),
        })

    output_path = output_dir / "census_county_population.csv"
    _write_csv(rows, output_path)
    log(f"Census data: {len(rows)} counties → {output_path}")
    return output_path


def _fetch_census_fallback(output_dir: Path) -> Path:
    """Fallback: use simpler Census API call."""
    url = f"{CENSUS_API_BASE}?get=NAME,B01001_001E&for=county:*&in=state:*"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    headers = data[0]
    rows = []
    for row in data[1:]:
        record = dict(zip(headers, row))
        county_fips5 = f"{record.get('state', '')}{record.get('county', '')}"
        rows.append({
            "county_fips5": county_fips5,
            "county_name_census": record.get("NAME", ""),
            "total_population": _safe_int(record.get("B01001_001E", 0)),
            "pop_65plus": 0,
            "pct_elderly": 0,
            "median_household_income": 0,
            "total_housing_units": 0,
            "vacant_housing_units": 0,
        })
    output_path = output_dir / "census_county_population.csv"
    _write_csv(rows, output_path)
    log(f"Census fallback: {len(rows)} counties → {output_path}")
    return output_path


def fetch_svi_data(output_dir: Path) -> Path:
    """Fetch CDC SVI data by county."""
    log("Fetching CDC SVI data...")

    # Try direct CSV download first
    try:
        resp = requests.get(SVI_URL, timeout=120, stream=True)
        resp.raise_for_status()
        content = resp.text
        reader = csv.DictReader(io.StringIO(content))
        all_rows = list(reader)
        log(f"  Downloaded {len(all_rows)} SVI records")
    except Exception as e:
        log(f"SVI CSV download failed: {e}. Trying API...")
        return _fetch_svi_api(output_dir)

    rows = []
    for record in all_rows:
        fips = record.get("FIPS", record.get("fips", ""))
        if not fips or len(str(fips).replace(".", "").replace("0", "")) == 0:
            continue

        fips = str(fips).zfill(5)
        if len(fips) != 5:
            continue

        svi_score = _safe_float(record.get("RPL_THEMES", record.get("rpl_themes", "")))
        if svi_score < 0:
            svi_score = None  # -999 means missing

        rows.append({
            "county_fips5": fips,
            "svi_overall": svi_score,
            "svi_socioeconomic": _safe_float(record.get("RPL_THEME1", record.get("rpl_theme1", ""))),
            "svi_household_disability": _safe_float(record.get("RPL_THEME2", record.get("rpl_theme2", ""))),
            "svi_minority_language": _safe_float(record.get("RPL_THEME3", record.get("rpl_theme3", ""))),
            "svi_housing_transport": _safe_float(record.get("RPL_THEME4", record.get("rpl_theme4", ""))),
            "county_name_svi": record.get("COUNTY", record.get("county", "")),
            "state_name_svi": record.get("STATE", record.get("state", "")),
        })

    output_path = output_dir / "cdc_svi_by_county.csv"
    _write_csv(rows, output_path)
    log(f"SVI data: {len(rows)} counties → {output_path}")
    return output_path


def _fetch_svi_api(output_dir: Path) -> Path:
    """Fallback: use SODA API for SVI."""
    log("  Using SODA API fallback for SVI...")
    offset = 0
    limit = 5000
    all_rows = []
    while True:
        url = f"{SVI_API_URL}?$limit={limit}&$offset={offset}"
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        all_rows.extend(batch)
        offset += limit
        if len(batch) < limit:
            break

    rows = []
    for record in all_rows:
        fips = str(record.get("fips", "")).zfill(5)
        if len(fips) != 5:
            continue
        svi = _safe_float(record.get("rpl_themes", ""))
        rows.append({
            "county_fips5": fips,
            "svi_overall": svi if svi >= 0 else None,
            "svi_socioeconomic": _safe_float(record.get("rpl_theme1", "")),
            "svi_household_disability": _safe_float(record.get("rpl_theme2", "")),
            "svi_minority_language": _safe_float(record.get("rpl_theme3", "")),
            "svi_housing_transport": _safe_float(record.get("rpl_theme4", "")),
            "county_name_svi": record.get("county", ""),
            "state_name_svi": record.get("state", ""),
        })

    output_path = output_dir / "cdc_svi_by_county.csv"
    _write_csv(rows, output_path)
    log(f"SVI API fallback: {len(rows)} counties → {output_path}")
    return output_path


def _safe_int(val) -> int:
    try:
        return int(float(str(val).strip()))
    except (ValueError, TypeError):
        return 0


def _safe_float(val) -> float:
    try:
        return float(str(val).strip())
    except (ValueError, TypeError):
        return 0.0


def _write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Fetch Census + SVI data")
    parser.add_argument("--output-dir", default="data", help="Output directory")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    census_path = fetch_census_population(output_dir)
    svi_path = fetch_svi_data(output_dir)

    log(f"Done. Census: {census_path}, SVI: {svi_path}")


if __name__ == "__main__":
    main()
