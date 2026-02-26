#!/usr/bin/env python3
"""End-to-end NSI Parquet -> FAST CSV pipeline from Oracle Object Storage."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq
import rasterio
from rasterio.warp import transform as warp_transform
from rasterio.warp import transform_bounds

try:
    import yaml
except Exception:
    yaml = None


FAST_INPUT_COLUMNS = [
    "FltyId",
    "Occ",
    "Cost",
    "Area",
    "NumStories",
    "FoundationType",
    "FirstFloorHt",
    "ContentCost",
    "Latitude",
    "Longitude",
]

FAST_MAPPING_JSON = {
    "UserDefinedFltyId": "FltyId",
    "OCC": "Occ",
    "Cost": "Cost",
    "Area": "Area",
    "NumStories": "NumStories",
    "FoundationType": "FoundationType",
    "FirstFloorHt": "FirstFloorHt",
    "ContentCost": "ContentCost",
    "BDDF_ID": "",
    "CDDF_ID": "",
    "IDDF_ID": "",
    "InvCost": "",
    "SOID": "",
    "Latitude": "Latitude",
    "Longitude": "Longitude",
}

NSI_COLUMNS = [
    "bid",
    "occtype",
    "val_struct",
    "sqft",
    "num_story",
    "found_type",
    "found_ht",
    "latitude",
    "longitude",
    "val_cont",
    "firmzone",
]

FLC_COASTAL_A = "CoastalA"
FLC_COASTAL_V = "CoastalV"
FLC_RIVERINE = "Riverine"

AUTO_RASTER_PATTERN = re.compile(
    r"(?P<storm>[A-Za-z0-9]+)_(?P<year>\d{4})_adv(?P<adv>\d+).*ResultMaskRaster\.tif$"
)


def log(message: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {message}", flush=True)


def run_command(command: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=check)


def run_oci_json(profile: str, args: list[str]) -> dict[str, Any]:
    command = ["oci", "--profile", profile] + args
    result = run_command(command, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            "OCI command failed: {cmd}\nstdout:\n{out}\nstderr:\n{err}".format(
                cmd=" ".join(command), out=result.stdout, err=result.stderr
            )
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OCI returned non-JSON output: {exc}") from exc


def run_oci_text(profile: str, args: list[str]) -> str:
    command = ["oci", "--profile", profile] + args
    result = run_command(command, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            "OCI command failed: {cmd}\nstdout:\n{out}\nstderr:\n{err}".format(
                cmd=" ".join(command), out=result.stdout, err=result.stderr
            )
        )
    return result.stdout.strip()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(repo_root: Path, config_path: Path | None) -> dict[str, Any]:
    defaults = {
        "batch_size": 65536,
        "download_retries": 3,
        "upload_retries": 3,
        "firmzone_codes": {
            "coastal_v": ["V", "VE"],
            "coastal_a": ["A", "AE", "AH", "AO", "A99", "AREA"],
        },
        "found_type_map": {
            "2": 2,
            "4": 4,
            "5": 5,
            "7": 7,
            "B": 4,
            "BASEMENT": 4,
            "C": 5,
            "CRAWL": 5,
            "CRAWL SPACE": 5,
            "P": 2,
            "PIER": 2,
            "S": 7,
            "SLAB": 7,
            "SLAB ON GRADE": 7,
            "F": 5,
            "I": 5,
            "W": 5,
        },
    }
    if yaml is None:
        return defaults
    candidate = config_path or (repo_root / "configs" / "fast_e2e.yaml")
    if not candidate.exists():
        return defaults
    with candidate.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Config must be a mapping: {candidate}")
    return deep_merge(defaults, loaded)


def get_namespace(profile: str) -> str:
    return run_oci_text(profile, ["os", "ns", "get", "--query", "data", "--raw-output"])


def list_objects(profile: str, namespace: str, bucket: str, prefix: str) -> list[dict[str, Any]]:
    payload = run_oci_json(
        profile,
        [
            "os",
            "object",
            "list",
            "--namespace-name",
            namespace,
            "--bucket-name",
            bucket,
            "--prefix",
            prefix,
            "--all",
        ],
    )
    return payload.get("data", [])


def parse_state_from_object_name(object_name: str) -> str | None:
    match = re.match(r"^nsi/state=([^/]+)/.+\.parquet$", object_name)
    if not match:
        return None
    return match.group(1)


def build_state_object_index(nsi_objects: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in nsi_objects:
        object_name = entry["name"]
        state = parse_state_from_object_name(object_name)
        if state is None:
            continue
        grouped[state].append(entry)
    return dict(grouped)


def filter_states(
    state_index: dict[str, list[dict[str, Any]]], state_scope: str
) -> dict[str, list[dict[str, Any]]]:
    if state_scope.strip().lower() == "all":
        return dict(sorted(state_index.items()))
    requested = [part.strip() for part in state_scope.split(",") if part.strip()]
    filtered: dict[str, list[dict[str, Any]]] = {}
    missing: list[str] = []
    for state in requested:
        if state in state_index:
            filtered[state] = state_index[state]
        else:
            missing.append(state)
    if missing:
        raise ValueError(f"Requested states not found in bucket: {', '.join(missing)}")
    return dict(sorted(filtered.items()))


def choose_raster_object(
    raster_objects: list[dict[str, Any]], raster_name: str
) -> dict[str, Any]:
    tif_objects = [entry for entry in raster_objects if entry["name"].lower().endswith(".tif")]
    if not tif_objects:
        raise RuntimeError("No .tif raster object found under rasters/ prefix.")

    if raster_name != "auto":
        matches = [
            entry
            for entry in tif_objects
            if entry["name"] == raster_name
            or Path(entry["name"]).name == raster_name
            or entry["name"].endswith("/" + raster_name)
        ]
        if not matches:
            raise ValueError(f"Requested raster not found: {raster_name}")
        if len(matches) > 1:
            matches = sorted(matches, key=lambda item: item["name"])
        return matches[-1]

    parsed: list[tuple[int, int, str, dict[str, Any]]] = []
    for entry in tif_objects:
        base = Path(entry["name"]).name
        match = AUTO_RASTER_PATTERN.match(base)
        if not match:
            continue
        parsed.append(
            (
                int(match.group("year")),
                int(match.group("adv")),
                base,
                entry,
            )
        )
    if parsed:
        parsed.sort(key=lambda item: (item[0], item[1], item[2]))
        return parsed[-1][3]

    tif_objects.sort(key=lambda item: (item.get("time-created", ""), item["name"]))
    return tif_objects[-1]


def download_object(
    profile: str,
    namespace: str,
    bucket: str,
    object_name: str,
    local_path: Path,
    expected_size: int | None,
    resume: bool,
    retries: int,
) -> dict[str, Any]:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    if resume and local_path.exists() and expected_size is not None and local_path.stat().st_size == expected_size:
        return {"status": "skipped", "path": str(local_path), "size": local_path.stat().st_size}

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        command = [
            "oci",
            "--profile",
            profile,
            "os",
            "object",
            "get",
            "--namespace-name",
            namespace,
            "--bucket-name",
            bucket,
            "--name",
            object_name,
            "--file",
            str(local_path),
        ]
        result = run_command(command, check=False)
        if result.returncode == 0:
            if expected_size is not None and local_path.stat().st_size != expected_size:
                last_error = RuntimeError(
                    f"Downloaded file size mismatch for {object_name}: "
                    f"expected {expected_size}, got {local_path.stat().st_size}"
                )
            else:
                return {"status": "downloaded", "path": str(local_path), "size": local_path.stat().st_size}
        else:
            last_error = RuntimeError(
                "Download failed for {name} (attempt {attempt}/{total})\nstdout:\n{out}\nstderr:\n{err}".format(
                    name=object_name,
                    attempt=attempt,
                    total=retries,
                    out=result.stdout,
                    err=result.stderr,
                )
            )
        if local_path.exists():
            local_path.unlink()
        time.sleep(min(3 * attempt, 10))
    raise RuntimeError(f"Failed to download {object_name}: {last_error}")


def load_occupancy_set(lookup_csv_path: Path) -> set[str]:
    allowed = set()
    with lookup_csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if "Occupancy" not in reader.fieldnames:
            raise ValueError(f"OccupancyTypes.csv missing Occupancy column: {lookup_csv_path}")
        for row in reader:
            value = (row.get("Occupancy") or "").strip().upper()
            if value:
                allowed.add(value)
    return allowed


def normalize_blank(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "null"}:
        return ""
    return text


def parse_float(value: Any) -> float | None:
    text = normalize_blank(value)
    if text == "":
        return None
    try:
        number = float(text)
    except Exception:
        return None
    if math.isnan(number):
        return None
    return number


def format_number(value: float | int | None) -> str:
    if value is None:
        return ""
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.10f}".rstrip("0").rstrip(".")


def normalize_occ(value: Any, allowed_occupancies: set[str]) -> str | None:
    text = normalize_blank(value).upper()
    if text == "":
        return None
    candidate = text.split("-", 1)[0]
    if candidate in allowed_occupancies:
        return candidate
    if text in allowed_occupancies:
        return text
    return None


def normalize_foundation_type(value: Any, mapping: dict[str, int]) -> int | None:
    text = normalize_blank(value).upper()
    if text == "":
        return None
    if text in mapping:
        return int(mapping[text])
    return None


def normalize_firmzone(value: Any) -> str:
    text = normalize_blank(value).upper()
    if text == "":
        return ""
    token = re.split(r"[^A-Z0-9]+", text)[0]
    return token


def classify_firmzone(zone: str, coastal_a_codes: set[str], coastal_v_codes: set[str]) -> str | None:
    if zone == "":
        return None
    if zone in coastal_v_codes or zone.startswith("VE"):
        return FLC_COASTAL_V
    if (
        zone in coastal_a_codes
        or zone.startswith("A")
        or zone.startswith("AE")
        or zone.startswith("AH")
        or zone.startswith("AO")
    ):
        return FLC_COASTAL_A
    return None


def raster_bbox_wgs84(raster_path: Path) -> dict[str, float]:
    with rasterio.open(raster_path) as src:
        left, bottom, right, top = src.bounds
        crs = src.crs
        if crs is None:
            raise RuntimeError(f"Raster has no CRS: {raster_path}")
        min_lon, min_lat, max_lon, max_lat = transform_bounds(
            crs,
            "EPSG:4326",
            left,
            bottom,
            right,
            top,
            densify_pts=21,
        )
        return {
            "min_lon": min_lon,
            "min_lat": min_lat,
            "max_lon": max_lon,
            "max_lat": max_lat,
        }


def compute_raster_footprint(raster_path: Path) -> dict[str, float] | None:
    """Compute tight bbox of non-nodata pixels (much tighter than full raster bbox)."""
    with rasterio.open(raster_path) as src:
        data = src.read(1)
        nodata = src.nodata
        valid = (data > 0) if nodata is None else ((data != nodata) & (data > 0))
        if not valid.any():
            return None
        rows, cols = np.where(valid)
        min_col, max_col = int(cols.min()), int(cols.max())
        min_row, max_row = int(rows.min()), int(rows.max())
        t = src.transform
        min_x, max_y = t * (min_col, min_row)
        max_x, min_y = t * (max_col + 1, max_row + 1)
        return dict(zip(
            ["min_lon", "min_lat", "max_lon", "max_lat"],
            transform_bounds(src.crs, "EPSG:4326", min_x, min_y, max_x, max_y, densify_pts=21),
        ))


def state_overlaps_footprint(
    parquet_paths: list[Path], footprint: dict[str, float]
) -> bool:
    """Check if any parquet file's lat/lon range overlaps the raster footprint using metadata."""
    for path in parquet_paths:
        pf = pq.ParquetFile(path)
        meta = pf.metadata
        for i in range(meta.num_row_groups):
            rg = meta.row_group(i)
            lat_min = lat_max = lon_min = lon_max = None
            for j in range(rg.num_columns):
                col = rg.column(j)
                if not col.is_stats_set:
                    continue
                if col.path_in_schema == "latitude":
                    lat_min = min(lat_min, col.statistics.min) if lat_min is not None else col.statistics.min
                    lat_max = max(lat_max, col.statistics.max) if lat_max is not None else col.statistics.max
                elif col.path_in_schema == "longitude":
                    lon_min = min(lon_min, col.statistics.min) if lon_min is not None else col.statistics.min
                    lon_max = max(lon_max, col.statistics.max) if lon_max is not None else col.statistics.max
            if all(v is not None for v in [lat_min, lat_max, lon_min, lon_max]):
                if (lat_max >= footprint["min_lat"] and lat_min <= footprint["max_lat"]
                        and lon_max >= footprint["min_lon"] and lon_min <= footprint["max_lon"]):
                    return True
    return False


def in_bbox(lon: float, lat: float, bbox: dict[str, float]) -> bool:
    return (
        bbox["min_lon"] <= lon <= bbox["max_lon"]
        and bbox["min_lat"] <= lat <= bbox["max_lat"]
    )


def clean_state_to_fast_csv(
    state: str,
    state_objects: list[dict[str, Any]],
    local_object_paths: dict[str, Path],
    run_dir: Path,
    mode: str,
    bbox: dict[str, float],
    raster_path: Path | None,
    allowed_occupancies: set[str],
    found_type_map: dict[str, int],
    coastal_a_codes: set[str],
    coastal_v_codes: set[str],
    batch_size: int,
    resume: bool,
) -> dict[str, Any]:
    summary_path = run_dir / "reports" / "state_cleaning" / f"state={state}.json"
    if resume and summary_path.exists():
        previous = load_json(summary_path)
        paths_ok = True
        for path in previous.get("csv_paths", {}).values():
            if not Path(path).exists():
                paths_ok = False
                break
        if paths_ok:
            return previous

    state_csv_paths = {
        FLC_COASTAL_A: run_dir / "input" / "fast_csv" / f"flc={FLC_COASTAL_A}" / f"state={state}.csv",
        FLC_COASTAL_V: run_dir / "input" / "fast_csv" / f"flc={FLC_COASTAL_V}" / f"state={state}.csv",
        FLC_RIVERINE: run_dir / "input" / "fast_csv" / f"flc={FLC_RIVERINE}" / f"state={state}.csv",
    }
    for path in state_csv_paths.values():
        if path.exists():
            path.unlink()

    handles: dict[str, Any] = {}
    writers: dict[str, csv.DictWriter] = {}

    def get_writer(flc: str) -> csv.DictWriter:
        if flc in writers:
            return writers[flc]
        csv_path = state_csv_paths[flc]
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        handle = csv_path.open("w", newline="", encoding="utf-8")
        writer = csv.DictWriter(handle, fieldnames=FAST_INPUT_COLUMNS)
        writer.writeheader()
        handles[flc] = handle
        writers[flc] = writer
        return writer

    # --- Task 2: Load raster for depth sampling ---
    raster_data = raster_inv_tf = raster_nodata = raster_shape = raster_crs_obj = None
    if raster_path:
        with rasterio.open(raster_path) as src:
            raster_data = src.read(1)
            raster_inv_tf = ~src.transform
            raster_nodata = src.nodata
            raster_shape = raster_data.shape
            raster_crs_obj = src.crs

    dropped = Counter()
    found_type_counter = Counter()
    firmzone_counter = Counter()
    zone_class_counter = Counter()
    written_by_flc = Counter()
    seen_bids: set[str] = set()  # Task 3: dedup by bid+lat+lon to avoid duplicate FltyIds
    valid_foundation_types: set[int] = set(found_type_map.values())

    input_rows = 0
    written_rows = 0

    for entry in state_objects:
        object_name = entry["name"]
        parquet_path = local_object_paths[object_name]
        parquet_file = pq.ParquetFile(parquet_path)
        available_columns = set(parquet_file.schema.names)
        missing_columns = [col for col in NSI_COLUMNS if col not in available_columns]
        if missing_columns:
            raise RuntimeError(
                f"Parquet file missing required columns ({', '.join(missing_columns)}): {parquet_path}"
            )
        for batch in parquet_file.iter_batches(batch_size=batch_size, columns=NSI_COLUMNS, use_threads=True):
            data = batch.to_pydict()
            batch_rows = batch.num_rows
            for idx in range(batch_rows):
                input_rows += 1
                bid = normalize_blank(data["bid"][idx])
                occ = normalize_occ(data["occtype"][idx], allowed_occupancies)
                cost = parse_float(data["val_struct"][idx])
                area = parse_float(data["sqft"][idx])
                num_stories = parse_float(data["num_story"][idx])
                foundation_raw = normalize_blank(data["found_type"][idx]).upper()
                foundation = normalize_foundation_type(foundation_raw, found_type_map)
                first_floor_ht = parse_float(data["found_ht"][idx])
                latitude = parse_float(data["latitude"][idx])
                longitude = parse_float(data["longitude"][idx])
                content_cost = parse_float(data["val_cont"][idx])
                firmzone = normalize_firmzone(data["firmzone"][idx])

                found_type_counter[foundation_raw or "<blank>"] += 1
                firmzone_counter[firmzone or "<blank>"] += 1

                if any(
                    value in {None, ""}
                    for value in [bid, occ, cost, area, num_stories, foundation, first_floor_ht, latitude, longitude]
                ):
                    dropped["missing_or_invalid_required"] += 1
                    continue
                if foundation not in valid_foundation_types:
                    dropped["invalid_foundation_type"] += 1
                    continue
                if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                    dropped["invalid_lat_lon"] += 1
                    continue

                # --- Task 2: Raster-aware spatial pre-filter ---
                if raster_data is not None:
                    if raster_crs_obj and raster_crs_obj.to_epsg() != 4326:
                        rx, ry = warp_transform("EPSG:4326", raster_crs_obj, [longitude], [latitude])
                        px, py = rx[0], ry[0]
                    else:
                        px, py = longitude, latitude
                    c_f, r_f = raster_inv_tf * (px, py)
                    r, c = int(r_f), int(c_f)
                    if 0 <= r < raster_shape[0] and 0 <= c < raster_shape[1]:
                        depth = float(raster_data[r, c])
                        if (raster_nodata is not None and depth == float(raster_nodata)) or depth <= 0:
                            dropped["zero_flood_depth"] += 1
                            continue
                    else:
                        dropped["outside_raster"] += 1
                        continue

                # --- Task 3: FltyId deduplication ---
                dedup_key = f"{bid}_{latitude}_{longitude}"
                if dedup_key in seen_bids:
                    dropped["duplicate_bid"] += 1
                    continue
                seen_bids.add(dedup_key)

                zone_class = classify_firmzone(firmzone, coastal_a_codes, coastal_v_codes)
                if zone_class is not None:
                    flc = zone_class
                elif mode == "impact-only":
                    if in_bbox(longitude, latitude, bbox):
                        flc = FLC_COASTAL_A
                    else:
                        dropped["unknown_firmzone_outside_bbox"] += 1
                        continue
                else:
                    flc = FLC_RIVERINE
                zone_class_counter[flc] += 1

                row = {
                    "FltyId": dedup_key,
                    "Occ": occ,
                    "Cost": format_number(cost),
                    "Area": format_number(area),
                    "NumStories": format_number(num_stories),
                    "FoundationType": str(int(foundation)),
                    "FirstFloorHt": format_number(first_floor_ht),
                    "ContentCost": format_number(content_cost if content_cost is not None else 0),
                    "Latitude": format_number(latitude),
                    "Longitude": format_number(longitude),
                }
                writer = get_writer(flc)
                writer.writerow(row)
                written_rows += 1
                written_by_flc[flc] += 1

    for handle in handles.values():
        handle.close()

    csv_paths = {
        flc: str(path)
        for flc, path in state_csv_paths.items()
        if path.exists() and path.stat().st_size > 0
    }

    summary = {
        "state": state,
        "objects": [entry["name"] for entry in state_objects],
        "input_rows": input_rows,
        "written_rows": written_rows,
        "written_by_flc": dict(written_by_flc),
        "dropped": dict(dropped),
        "firmzone_counts": dict(firmzone_counter),
        "assigned_flc_counts": dict(zone_class_counter),
        "found_type_counts": dict(found_type_counter),
        "csv_paths": csv_paths,
    }
    write_json(summary_path, summary)
    return summary


def find_fast_outputs(output_dir: Path) -> tuple[Path | None, Path | None]:
    csv_files = sorted([path for path in output_dir.glob("*.csv") if path.is_file()])
    primary_candidates = [path for path in csv_files if not path.name.endswith("_sorted.csv")]
    if not primary_candidates:
        return None, None
    primary = max(primary_candidates, key=lambda path: path.stat().st_mtime)
    sorted_match = primary.with_name(primary.stem + "_sorted.csv")
    if not sorted_match.exists():
        sorted_candidates = [path for path in csv_files if path.name.endswith("_sorted.csv")]
        sorted_match = max(sorted_candidates, key=lambda path: path.stat().st_mtime) if sorted_candidates else None
    return primary, sorted_match


def count_csv_data_rows(csv_path: Path) -> int:
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        return sum(1 for _ in reader)


def validate_fast_output_pair(
    primary_csv: Path | None,
    sorted_csv: Path | None,
) -> tuple[bool, int, int]:
    if primary_csv is None or sorted_csv is None:
        return False, 0, 0
    if not primary_csv.exists() or not sorted_csv.exists():
        return False, 0, 0
    primary_rows = count_csv_data_rows(primary_csv)
    sorted_rows = count_csv_data_rows(sorted_csv)
    is_valid = primary_rows > 0 and sorted_rows > 0 and primary_rows == sorted_rows
    return is_valid, primary_rows, sorted_rows


def run_fast_job(
    task: dict[str, Any],
    resume: bool,
    fast_python: str,
    run_fast_path: Path,
    fast_project_root: Path,
) -> dict[str, Any]:
    state = task["state"]
    flc = task["flc"]
    input_csv = Path(task["input_csv"])
    output_dir = Path(task["output_dir"])
    raster_path = Path(task["raster_path"])
    output_dir.mkdir(parents=True, exist_ok=True)

    if resume:
        existing_primary, existing_sorted = find_fast_outputs(output_dir)
        outputs_valid, primary_rows, sorted_rows = validate_fast_output_pair(
            existing_primary,
            existing_sorted,
        )
        if outputs_valid:
            return {
                "state": state,
                "flc": flc,
                "input_csv": str(input_csv),
                "output_dir": str(output_dir),
                "success": True,
                "returncode": 0,
                "skipped": True,
                "primary_csv": str(existing_primary),
                "sorted_csv": str(existing_sorted),
                "primary_rows": primary_rows,
                "sorted_rows": sorted_rows,
                "stdout": "",
                "stderr": "",
            }

    for stale_csv in output_dir.glob("*.csv"):
        stale_csv.unlink()

    command = [
        fast_python,
        str(run_fast_path),
        "--inventory",
        str(input_csv),
        "--mapping-json",
        json.dumps(FAST_MAPPING_JSON),
        "--flc",
        flc,
        "--rasters",
        str(raster_path),
        "--output-dir",
        str(output_dir),
        "--project-root",
        str(fast_project_root),
    ]
    result = run_command(command, check=False)
    primary_csv, sorted_csv = find_fast_outputs(output_dir)
    outputs_valid, primary_rows, sorted_rows = validate_fast_output_pair(primary_csv, sorted_csv)
    success = result.returncode == 0 and outputs_valid
    validation_error = ""
    if not outputs_valid:
        validation_error = (
            "Invalid FAST output pair: primary_rows={primary}, sorted_rows={sorted}".format(
                primary=primary_rows,
                sorted=sorted_rows,
            )
        )

    return {
        "state": state,
        "flc": flc,
        "input_csv": str(input_csv),
        "output_dir": str(output_dir),
        "success": bool(success),
        "returncode": result.returncode,
        "skipped": False,
        "primary_csv": str(primary_csv) if primary_csv else None,
        "sorted_csv": str(sorted_csv) if sorted_csv else None,
        "primary_rows": primary_rows,
        "sorted_rows": sorted_rows,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "validation_error": validation_error,
        "command": command,
    }


def merge_predictions(
    run_dir: Path,
    run_id: str,
    raster_name: str,
    state_summaries: dict[str, dict[str, Any]],
    successful_runs: list[dict[str, Any]],
) -> tuple[Path, int]:
    output_path = run_dir / "final" / f"predictions_{Path(raster_name).stem}_{run_id}.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows_written = 0

    metadata_fields = ["state", "flc", "raster_name", "run_id", "source_object"]
    expected_fields: list[str] | None = None
    writer: csv.DictWriter | None = None

    with output_path.open("w", newline="", encoding="utf-8") as out_handle:
        for run in successful_runs:
            primary_csv = Path(run["primary_csv"])
            state = run["state"]
            with primary_csv.open("r", newline="", encoding="utf-8") as in_handle:
                reader = csv.DictReader(in_handle)
                current_fields = reader.fieldnames or []
                if expected_fields is None:
                    expected_fields = current_fields
                    writer = csv.DictWriter(out_handle, fieldnames=expected_fields + metadata_fields)
                    writer.writeheader()
                elif current_fields != expected_fields:
                    raise RuntimeError(
                        "FAST output columns mismatch while merging.\n"
                        f"Expected: {expected_fields}\nGot: {current_fields}\nFile: {primary_csv}"
                    )
                source_object = "|".join(state_summaries[state].get("objects", []))
                for row in reader:
                    row["state"] = state
                    row["flc"] = run["flc"]
                    row["raster_name"] = raster_name
                    row["run_id"] = run_id
                    row["source_object"] = source_object
                    writer.writerow(row)
                    rows_written += 1
    return output_path, rows_written


def upload_results(
    profile: str,
    namespace: str,
    bucket: str,
    run_dir: Path,
    run_id: str,
    retries: int,
) -> dict[str, Any]:
    upload_root = f"results/{run_id}/"
    upload_paths: list[Path] = []
    for subdir in ["final", "reports", "fast_output", "input/fast_csv"]:
        root = run_dir / subdir
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                upload_paths.append(path)

    uploaded = 0
    failed: list[dict[str, str]] = []
    for local_path in upload_paths:
        relative_name = local_path.relative_to(run_dir).as_posix()
        object_name = upload_root + relative_name
        success = False
        last_error = ""
        for attempt in range(1, retries + 1):
            command = [
                "oci",
                "--profile",
                profile,
                "os",
                "object",
                "put",
                "--namespace-name",
                namespace,
                "--bucket-name",
                bucket,
                "--name",
                object_name,
                "--file",
                str(local_path),
                "--force",
            ]
            result = run_command(command, check=False)
            if result.returncode == 0:
                success = True
                uploaded += 1
                break
            last_error = result.stderr.strip() or result.stdout.strip()
            time.sleep(min(3 * attempt, 10))
        if not success:
            failed.append({"file": str(local_path), "object": object_name, "error": last_error})
    return {
        "uploaded_count": uploaded,
        "failed_count": len(failed),
        "failed": failed,
        "object_prefix": upload_root,
    }


def serialize_counter_map(state_map: dict[str, dict[str, Any]], key: str) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for state, payload in state_map.items():
        result[state] = {k: int(v) for k, v in payload.get(key, {}).items()}
    return result


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run full NSI Parquet -> FAST pipeline from Oracle Object Storage.",
    )
    parser.add_argument("--oci-profile", default="DEFAULT")
    parser.add_argument("--bucket", default="arc-capstone-processed-parquet")
    parser.add_argument("--state-scope", default="all")
    parser.add_argument("--raster-name", default="auto")
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--mode", choices=["impact-only", "full-domain"], default="impact-only")
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--upload-results", action="store_true", default=False)
    parser.add_argument("--resume", dest="resume", action="store_true", default=True)
    parser.add_argument("--no-resume", dest="resume", action="store_false")
    parser.add_argument("--fast-python", default=sys.executable)
    parser.add_argument("--config", default=None)
    parser.add_argument("--event", default=None, help="Event name from configs/event_state_map.yaml")
    return parser.parse_args()


def load_event_state_map(repo_root: Path) -> dict[str, Any]:
    """Load hurricane event → affected states mapping from YAML config."""
    path = repo_root / "configs" / "event_state_map.yaml"
    if not path.exists() or yaml is None:
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("events", {})


def main() -> int:
    args = parse_arguments()
    repo_root = Path(__file__).resolve().parents[1]
    fast_project_root = repo_root / "FAST-main"
    run_fast_path = fast_project_root / "Python_env" / "run_fast.py"
    occupancy_lookup_path = fast_project_root / "Lookuptables" / "OccupancyTypes.csv"
    if not run_fast_path.exists():
        raise RuntimeError(f"FAST run entrypoint not found: {run_fast_path}")
    if not occupancy_lookup_path.exists():
        raise RuntimeError(f"Occupancy lookup not found: {occupancy_lookup_path}")

    config_path = Path(args.config) if args.config else None
    config = load_config(repo_root, config_path)

    # --- Task 4: Event-state mapping ---
    if args.event:
        event_map = load_event_state_map(repo_root)
        if args.event not in event_map:
            raise ValueError(f"Unknown event '{args.event}'. Available: {list(event_map.keys())}")
        args.state_scope = ",".join(event_map[args.event]["states"])
        log(f"Event '{args.event}' -> states: {args.state_scope}")

    output_root = Path(args.output_root) if args.output_root else (repo_root / "exports")
    output_root.mkdir(parents=True, exist_ok=True)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = output_root / f"fast_e2e_{run_id}"
    (run_dir / "reports").mkdir(parents=True, exist_ok=True)

    log("Resolving Oracle namespace and listing objects...")
    namespace = get_namespace(args.oci_profile)
    nsi_objects = list_objects(args.oci_profile, namespace, args.bucket, "nsi/")
    raster_objects = list_objects(args.oci_profile, namespace, args.bucket, "rasters/")
    if not nsi_objects:
        raise RuntimeError("No NSI objects found under nsi/ prefix.")

    state_index = build_state_object_index(nsi_objects)
    selected_state_index = filter_states(state_index, args.state_scope)
    selected_raster = choose_raster_object(raster_objects, args.raster_name)
    log(
        "Selected raster: {name} | states: {states}".format(
            name=selected_raster["name"], states=", ".join(selected_state_index.keys())
        )
    )

    manifest = {
        "run_id": run_id,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "parameters": {
            "oci_profile": args.oci_profile,
            "bucket": args.bucket,
            "state_scope": args.state_scope,
            "raster_name": args.raster_name,
            "mode": args.mode,
            "max_workers": args.max_workers,
            "upload_results": args.upload_results,
            "resume": args.resume,
            "fast_python": args.fast_python,
            "config": str(config_path) if config_path else None,
        },
        "namespace": namespace,
        "selected_raster_object": selected_raster["name"],
        "states": list(selected_state_index.keys()),
    }
    write_json(run_dir / "reports" / "run_manifest.json", manifest)

    download_manifest: list[dict[str, Any]] = []
    local_object_paths: dict[str, Path] = {}
    all_target_objects: list[dict[str, Any]] = []
    for state_entries in selected_state_index.values():
        all_target_objects.extend(state_entries)
    all_target_objects.append(selected_raster)

    log(f"Downloading {len(all_target_objects)} object(s) to staging...")
    for entry in all_target_objects:
        object_name = entry["name"]
        local_path = run_dir / "input" / "oracle_objects" / object_name
        result = download_object(
            profile=args.oci_profile,
            namespace=namespace,
            bucket=args.bucket,
            object_name=object_name,
            local_path=local_path,
            expected_size=entry.get("size"),
            resume=args.resume,
            retries=int(config.get("download_retries", 3)),
        )
        result["object_name"] = object_name
        download_manifest.append(result)
        local_object_paths[object_name] = local_path
    write_json(run_dir / "reports" / "download_manifest.json", {"downloads": download_manifest})

    raster_local_path = local_object_paths[selected_raster["name"]]
    bbox = raster_bbox_wgs84(raster_local_path)

    # --- Task 1: Compute tight raster footprint and filter states ---
    footprint = compute_raster_footprint(raster_local_path)
    if footprint is None:
        raise RuntimeError("Raster has no valid (>0) flood depth pixels.")
    log(f"Raster footprint: {footprint}")

    pre_filter_count = len(selected_state_index)
    filtered_state_index: dict[str, list[dict[str, Any]]] = {}
    for state, entries in selected_state_index.items():
        parquet_paths = [local_object_paths[e["name"]] for e in entries]
        if state_overlaps_footprint(parquet_paths, footprint):
            filtered_state_index[state] = entries
        else:
            log(f"Skipping state={state} (no overlap with raster footprint)")
    selected_state_index = filtered_state_index
    log(f"State filter: {pre_filter_count} -> {len(selected_state_index)} states with raster overlap")

    if not selected_state_index:
        raise RuntimeError("No states overlap with the raster footprint.")

    write_json(
        run_dir / "reports" / "raster_bbox.json",
        {"raster_object": selected_raster["name"], "bbox_wgs84": bbox, "footprint": footprint},
    )

    allowed_occupancies = load_occupancy_set(occupancy_lookup_path)
    found_type_map = {str(k).upper(): int(v) for k, v in config["found_type_map"].items()}
    coastal_a_codes = {str(code).upper() for code in config["firmzone_codes"]["coastal_a"]}
    coastal_v_codes = {str(code).upper() for code in config["firmzone_codes"]["coastal_v"]}
    batch_size = int(config.get("batch_size", 65536))

    log("Cleaning NSI parquet into FAST CSV buckets...")
    state_summaries: dict[str, dict[str, Any]] = {}
    for state, entries in selected_state_index.items():
        log(f"Cleaning state={state} from {len(entries)} parquet object(s)...")
        summary = clean_state_to_fast_csv(
            state=state,
            state_objects=entries,
            local_object_paths=local_object_paths,
            run_dir=run_dir,
            mode=args.mode,
            bbox=bbox,
            raster_path=raster_local_path,
            allowed_occupancies=allowed_occupancies,
            found_type_map=found_type_map,
            coastal_a_codes=coastal_a_codes,
            coastal_v_codes=coastal_v_codes,
            batch_size=batch_size,
            resume=args.resume,
        )
        state_summaries[state] = summary
        log(
            "State={state} cleaned: input_rows={inp}, written_rows={out}".format(
                state=state,
                inp=summary.get("input_rows", 0),
                out=summary.get("written_rows", 0),
            )
        )

    data_quality_report = {
        "run_id": run_id,
        "state_count": len(state_summaries),
        "state_written_rows": {state: summary.get("written_rows", 0) for state, summary in state_summaries.items()},
        "state_input_rows": {state: summary.get("input_rows", 0) for state, summary in state_summaries.items()},
        "state_dropped": serialize_counter_map(state_summaries, "dropped"),
        "state_found_type_counts": serialize_counter_map(state_summaries, "found_type_counts"),
    }
    write_json(run_dir / "reports" / "data_quality_report.json", data_quality_report)

    flc_report = {
        "run_id": run_id,
        "state_assigned_flc_counts": serialize_counter_map(state_summaries, "assigned_flc_counts"),
        "state_firmzone_counts": serialize_counter_map(state_summaries, "firmzone_counts"),
        "state_written_by_flc": serialize_counter_map(state_summaries, "written_by_flc"),
    }
    write_json(run_dir / "reports" / "flc_assignment_report.json", flc_report)

    fast_tasks: list[dict[str, Any]] = []
    for state, summary in state_summaries.items():
        for flc, csv_path in summary.get("csv_paths", {}).items():
            path = Path(csv_path)
            if not path.exists() or path.stat().st_size <= 0:
                continue
            fast_tasks.append(
                {
                    "state": state,
                    "flc": flc,
                    "input_csv": str(path),
                    "raster_path": str(raster_local_path),
                    "output_dir": str(run_dir / "fast_output" / f"state={state}" / f"flc={flc}"),
                }
            )

    if not fast_tasks:
        raise RuntimeError("No FAST task generated. Check cleaning filters and source data coverage.")

    # --- Task 7: Parallel execution with progress and retry ---
    max_w = min(args.max_workers, len(fast_tasks)) or 1
    log(f"Running FAST for {len(fast_tasks)} state/flc task(s) with max_workers={max_w}...")
    fast_results: list[dict[str, Any]] = []
    failed_tasks: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_w) as executor:
        future_map = {
            executor.submit(
                run_fast_job, task=task, resume=args.resume,
                fast_python=args.fast_python, run_fast_path=run_fast_path,
                fast_project_root=fast_project_root,
            ): task
            for task in fast_tasks
        }
        done_count = 0
        for future in as_completed(future_map):
            done_count += 1
            result = future.result()
            log(f"  [{done_count}/{len(fast_tasks)}] {result['state']}/{result['flc']} -> {'OK' if result.get('success') else 'FAIL'}")
            if result.get("success"):
                fast_results.append(result)
            else:
                failed_tasks.append(future_map[future])

    # Retry failed tasks once
    if failed_tasks:
        log(f"Retrying {len(failed_tasks)} failed task(s)...")
        for task in failed_tasks:
            result = run_fast_job(
                task=task, resume=False, fast_python=args.fast_python,
                run_fast_path=run_fast_path, fast_project_root=fast_project_root,
            )
            fast_results.append(result)
            log(f"  Retry {task['state']}/{task['flc']} -> {'OK' if result.get('success') else 'FAIL'}")

    fast_results.sort(key=lambda item: (item["state"], item["flc"]))
    log(
        "FAST runs finished: total={total}, success={success}, failed={failed}".format(
            total=len(fast_results),
            success=len([item for item in fast_results if item.get("success")]),
            failed=len([item for item in fast_results if not item.get("success")]),
        )
    )
    write_json(run_dir / "reports" / "fast_execution_report.json", {"runs": fast_results})

    successful_runs = [result for result in fast_results if result.get("success")]
    if not successful_runs:
        raise RuntimeError("All FAST runs failed. See fast_execution_report.json.")

    final_predictions_path, merged_rows = merge_predictions(
        run_dir=run_dir,
        run_id=run_id,
        raster_name=Path(selected_raster["name"]).name,
        state_summaries=state_summaries,
        successful_runs=successful_runs,
    )

    upload_report = None
    if args.upload_results:
        log("Uploading result artifacts back to Oracle...")
        upload_report = upload_results(
            profile=args.oci_profile,
            namespace=namespace,
            bucket=args.bucket,
            run_dir=run_dir,
            run_id=run_id,
            retries=int(config.get("upload_retries", 3)),
        )
        write_json(run_dir / "reports" / "upload_report.json", upload_report)

    manifest.update(
        {
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "run_dir": str(run_dir),
            "selected_raster_local_path": str(raster_local_path),
            "state_count": len(state_summaries),
            "fast_task_count": len(fast_tasks),
            "fast_success_count": len(successful_runs),
            "fast_failure_count": len(fast_tasks) - len(successful_runs),
            "predictions_csv": str(final_predictions_path),
            "merged_rows": merged_rows,
            "upload_report": upload_report,
        }
    )
    write_json(run_dir / "reports" / "run_manifest.json", manifest)

    payload = {
        "success": True,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "selected_raster": selected_raster["name"],
        "states_processed": len(state_summaries),
        "fast_runs_total": len(fast_tasks),
        "fast_runs_success": len(successful_runs),
        "predictions_csv": str(final_predictions_path),
        "merged_rows": merged_rows,
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    exit_code = 0
    try:
        exit_code = main()
    except Exception as exc:
        error_payload = {"success": False, "error": str(exc)}
        print(json.dumps(error_payload, indent=2))
        exit_code = 1
    raise SystemExit(exit_code)
