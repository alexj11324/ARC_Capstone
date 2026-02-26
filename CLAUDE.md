# CLAUDE.md — ARC Capstone

## Project Overview

CMU Heinz MSPPM 2026 Capstone for American Red Cross. Property-level storm surge/tsunami impact modeling using FEMA's FAST (Flood Assessment Structure Tool) with NSI building inventory (30M+ structures) and SLOSH surge rasters. Goal: estimate building damage, displaced population, and high-need populations for Red Cross shelter/casework planning.

## Architecture

```
NSI Parquet (Oracle) → clean/filter → FAST CSV → FAST engine → damage predictions
SLOSH Parquet        → rasterize   → GeoTIFF flood depth raster ↗
```

- Key entry point: `scripts/fast_e2e_from_oracle.py`
- FAST headless engine: `FAST-main/Python_env/run_fast.py` (no GUI for production)
- `hazus_notinuse.py` is NOT obsolete — it is the active FAST execution engine
- `manage.py` is Windows-only (`ctypes.windll`); do not import on macOS/Linux

## Key Scripts

| Script | Purpose |
|--------|---------|
| `scripts/fast_e2e_from_oracle.py` | Main E2E pipeline: OCI download → NSI→FAST CSV → run FAST → upload |
| `scripts/h3_spatial_index.py` | H3 hex spatial pre-filtering for raster-aware building selection |
| `scripts/duckdb_fast_pipeline.py` | DuckDB-accelerated pipeline variant |
| `scripts/slosh_to_raster.py` | SLOSH Parquet → GeoTIFF converter |

## Data Contracts

### NSI → FAST CSV Column Mapping (see AGENTS.md §3-4)

| NSI Field | FAST Column | Notes |
|-----------|-------------|-------|
| `bid` | `FltyId` | Deduplicate before writing |
| `occtype` | `Occ` | e.g. RES1, COM1 |
| `val_struct` | `Cost` | Replacement cost ($) |
| `sqft` | `Area` | Floor area (sqft) |
| `num_story` | `NumStories` | Stories above ground |
| `found_type` | `FoundationType` | Numeric via `found_type_map`: Pier=2, Basement=4, Crawl=5, Slab=7 |
| `found_ht` | `FirstFloorHt` | Feet above grade |
| `latitude` / `longitude` | `Latitude` / `Longitude` | WGS84 |
| `val_cont` | `ContentCost` | Optional |

### SLOSH → Raster

- Geometry: `geometry_wkt` | Surge: `cN_mean`/`cN_high` (N=0..5) | Terrain: `topography`
- Inundation depth = surge elevation - topography; output GeoTIFF in feet

### FAST Runtime Parameters

- `flC`: `CoastalA` (default) | `CoastalV` (high-risk) | `Riverine` (inland)
- `raster`: path to `.tif` flood depth raster

## Configuration

- `configs/fast_e2e.yaml` — batch_size, firmzone codes, foundation type mapping
- `configs/event_state_map.yaml` — hurricane → affected state mapping

## Common Commands

```bash
# Full E2E pipeline (impact-only, default)
python scripts/fast_e2e_from_oracle.py \
  --state-scope Florida --raster-name auto --config configs/fast_e2e.yaml

# Full domain mode (all buildings)
python scripts/fast_e2e_from_oracle.py --state-scope Florida --mode full-domain

# DuckDB-accelerated variant
python scripts/duckdb_fast_pipeline.py --state Florida

# SLOSH → raster
python scripts/slosh_to_raster.py --basin ny3mom --category 3 --tide high

# H3 spatial pre-indexing
python scripts/h3_spatial_index.py --raster path/to/raster.tif --resolution 7
```

## Known Issues

### 99.7% Zero-Loss Spatial Mismatch (CRITICAL)

Buildings with valid FIRM zones bypass bbox filter in `fast_e2e_from_oracle.py`, producing rows outside raster coverage. FAST returns depth=0 for out-of-bounds coords, inflating zero-loss output. **Fix**: apply raster-aware spatial pre-filtering (H3 hex or bbox clip) to ALL buildings before FAST, regardless of firmzone.

### FltyId Deduplication (HIGH)

No dedup on `bid` across parquet files — duplicate FltyIds inflate damage totals. Track `seen_bids: set` and skip duplicates.

### Partial FAST Output (MEDIUM)

`run_fast_job` checks returncode + file existence but not row count. Partial writes on crash pass the success check.

## Spatial Filtering Rules

1. `impact-only` mode: drop ALL buildings outside `raster_bbox_wgs84(raster_path)`
2. BBox is coarse; for irregular footprints consider raster valid-pixel convex hull
3. Do NOT use FIRM zones as proxy for event footprint (FIRM = long-term risk; raster = event)

## What NOT to Do

- Do not run GUI mode for production
- Do not import `manage.py` on macOS/Linux
- Do not use FIRM zone as spatial filter in `impact-only` mode
- Do not skip FltyId deduplication
- Do not expand scope beyond what is requested
- Do not ask questions answered by AGENTS.md or this file
