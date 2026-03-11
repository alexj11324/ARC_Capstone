# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CMU Heinz MSPPM 2026 Capstone for American Red Cross. Property-level storm surge/tsunami impact modeling using FEMA's FAST (Flood Assessment Structure Tool) with NSI building inventory (30M+ structures) and SLOSH surge rasters.

**Goal**: Produce county-level **Population Affected** and **Population Impacted** estimates classified by **Low/Medium/High intensity zones**, feeding into ARC's Mass Care Planning Assumptions Spreadsheet for shelter, feeding, and emergency supply planning.

**Key insight**: ARC does NOT need us to predict shelter population directly. Their spreadsheet is a "chain of equations" — we provide population counts at any point in the chain, and their planning factors finish the rest. Our best insertion point is **Population Impacted (L/M/H) by county**, derived from building-level FAST damage classified by surge depth thresholds. See `research/population_impact/DIRECTION.md` for the full pivot rationale.

## Architecture

Two pipelines feed into each other:

```
PRODUCTION PIPELINE (upstream)
  NHC Advisory → SLOSH raster → FAST engine → building-level damage (3.5M buildings, in Athena)

POPULATION IMPACT PIPELINE (downstream)
  Athena predictions → Dedup across advisories → Classify L/M/H zones → Spatial join counties
      → County aggregation → Census join + ARC conversion rates → Planning Assumptions CSV/Excel
```

Key architecture decisions:
- **Storage-as-compute**: DuckDB for local analytics, Athena for cloud queries — no persistent DB
- **Headless FAST**: `FAST-main/Python_env/run_fast.py` for production; never use GUI mode
- **`hazus_notinuse.py`** is NOT obsolete — it is the active FAST execution engine despite its name
- **`manage.py`** is Windows-only (`ctypes.windll`); do not import on macOS/Linux

## Environment Setup

```bash
pip install duckdb pyarrow pandas geopandas rasterio pyyaml h3 boto3 requests openpyxl scikit-learn
```

AWS credentials required for Athena queries. OCI CLI required for Oracle Object Storage access.

## Common Commands

```bash
# === Production Pipeline ===
# Full E2E pipeline (impact-only, default)
python scripts/fast_e2e_from_oracle.py \
  --state-scope Florida --raster-name auto --config configs/fast_e2e.yaml

# Full domain mode (all buildings)
python scripts/fast_e2e_from_oracle.py --state-scope Florida --mode full-domain

# SLOSH → raster
python scripts/slosh_to_raster.py --basin ny3mom --category 3 --tide high

# === Population Impact Pipeline ===
cd research/population_impact

# Phase 1: Classify buildings into L/M/H zones, aggregate to county level (requires Athena)
python scripts/04_classify_lmh.py --output-dir data

# Phase 2: Join Census population, apply ARC conversion rates, export CSV + Excel
python scripts/05_format_for_spreadsheet.py --input data/county_lmh_features.csv --output-dir outputs

# Phase 3: Validate against Ground Truth
python scripts/06_validate_lmh.py --input outputs/planning_assumptions_output.csv \
  --gt "../../Ground Truth Data.xlsx" --output-dir outputs

# Dry-run (print SQL without executing)
python scripts/04_classify_lmh.py --dry-run
```

## Key Scripts

### Production Pipeline

| Script | Purpose |
|--------|---------|
| `scripts/fast_e2e_from_oracle.py` | Main E2E: OCI download → NSI→FAST CSV → run FAST → upload |
| `scripts/slosh_to_raster.py` | SLOSH Parquet → GeoTIFF converter |
| `scripts/h3_spatial_index.py` | H3 hex spatial pre-filtering for raster-aware building selection |
| `scripts/duckdb_fast_pipeline.py` | DuckDB-accelerated pipeline variant |
| `scripts/validate_pipeline.py` | Pipeline output validation |
| `scripts/launch_cloud_parallel.sh` | AWS Spot instance parallel execution |

### Population Impact Pipeline (`research/population_impact/scripts/`)

| Script | Purpose |
|--------|---------|
| `01_county_damage_agg.py` | Athena county-level damage aggregation (legacy) |
| `02_fetch_census_svi.py` | Census ACS 5-year population + CDC SVI data fetch |
| `03_build_and_train.py` | Feature matrix + ML shelter model (legacy, replaced by L/M/H approach) |
| `04_classify_lmh.py` | **Phase 1**: Athena L/M/H classification + county aggregation via spatial join |
| `05_format_for_spreadsheet.py` | **Phase 2**: Census join + ARC conversion rates → CSV/Excel |
| `06_validate_lmh.py` | **Phase 3**: GT comparison, RMSE/MAE/R2, threshold sensitivity |

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

### Athena Tables

| Table | Description |
|-------|-------------|
| `arc_storm_surge.predictions_csv` | 3.5M building predictions (CSV-backed, avoids Parquet type mismatch) |
| `arc_analysis.us_county_boundaries` | County polygons with `geometry`, `county_fips5`, `county_name`, `state_abbr` |

### FAST Runtime Parameters

- `flC`: `CoastalA` (default) | `CoastalV` (high-risk) | `Riverine` (inland)
- `raster`: path to `.tif` flood depth raster

## Configuration

- `configs/fast_e2e.yaml` — batch_size, firmzone codes, foundation type mapping
- `configs/event_state_map.yaml` — hurricane → affected state mapping (11 events: Beryl, Debby, Florence, Harvey, Helene, Ian, Ida, Idalia, Katrina, Michael, Milton)

## Output Artifacts

### Production Pipeline

```
exports/fast_e2e_<run_id>/
  run_manifest.json          ← entry point for tracing
  input/                     ← FAST input CSVs
  fast_output/               ← raw FAST results
  final/predictions_*.csv    ← merged predictions
  reports/                   ← quality, flc_assignment
```

### Population Impact Pipeline

```
research/population_impact/
  data/
    county_lmh_long.csv          ← 836 rows (event × county × zone)
    county_lmh_features.csv      ← 383 rows (event × county, wide format)
    county_lmh_metadata.json
    census_county_population.csv
  outputs/
    planning_assumptions_output.csv    ← Final deliverable (columns J-R format)
    arc_planning_template_lmh.xlsx     ← Excel with Estimates + Parameters sheets
    lmh_validation_report.md
    lmh_validation_joined.csv
```

## ARC Mass Care Planning Assumptions

Aligns with ARC's official framework (Job Tool V.6.0).

### Intensity Classification (Figure 9)

| Zone | Storm Surge | Building Damage |
|------|------------|-----------------|
| High | >12 ft | >35% destroyed |
| Medium | 9-12 ft | 11-34% destroyed |
| Low | 4-8 ft | 0-10% destroyed |

### Mass Care Conversion Rates (Figure 16)

| Impact Zone | Shelter % | Feeding % |
|-------------|-----------|-----------|
| High | 5.0% | 12.0% |
| Medium | 3.0% | 7.0% |
| Low | 1.0% | 3.0% |

### Affected vs Impacted

- **Affected**: Everyone in the disaster area (any surge exposure, `depth_grid > 0`)
- **Impacted**: Subset with actual structural damage (`bldgdmgpct > 0` or `depth_in_struc > 0`)

### Planning Assumptions Spreadsheet Columns

| Columns | Content |
|---------|---------|
| J / K / L | Population Affected: Low / Medium / High |
| M / N / O | Population Impacted: Low / Medium / High |
| P / Q / R | Households Needing Shelter: Low / Medium / High |

### FAST → Intensity Zone Mapping

```python
if depth_grid > 12:    zone = 'HIGH'
elif depth_grid >= 9:  zone = 'MEDIUM'
elif depth_grid >= 4:  zone = 'LOW'
# Damage fallback (when surge < 4 ft)
elif bldgdmgpct > 35:  zone = 'HIGH'
elif bldgdmgpct > 15:  zone = 'MEDIUM'
elif bldgdmgpct > 0:   zone = 'LOW'
else:                   zone = 'NONE'
```

## Known Issues

### 99.7% Zero-Loss Spatial Mismatch (CRITICAL)

Buildings with valid FIRM zones bypass bbox filter in `fast_e2e_from_oracle.py`, producing rows outside raster coverage. FAST returns depth=0 for out-of-bounds coords, inflating zero-loss output. **Fix**: apply raster-aware spatial pre-filtering (H3 hex or bbox clip) to ALL buildings before FAST, regardless of firmzone.

### FltyId Deduplication (HIGH)

No dedup on `bid` across parquet files — duplicate FltyIds inflate damage totals. Track `seen_bids: set` and skip duplicates.

### Partial FAST Output (MEDIUM)

`run_fast_job` checks returncode + file existence but not row count. Partial writes on crash pass the success check.

### NSI Data Deleted (INFO)

NSI raw data was deleted from Athena (`red_cross_hurricane.nsi_data` has 0 rows). The population impact pipeline uses spatial join with `arc_analysis.us_county_boundaries` instead of NSI cbfips lookup, and estimates population as building count × 2.53 (avg US household size).

## Spatial Filtering Rules

1. `impact-only` mode: drop ALL buildings outside `raster_bbox_wgs84(raster_path)`
2. BBox is coarse; for irregular footprints consider raster valid-pixel convex hull
3. Do NOT use FIRM zones as proxy for event footprint (FIRM = long-term risk; raster = event)

## Key Documentation

| Document | Content |
|----------|---------|
| `AGENTS.md` | Execution contract, data contracts, guardrails |
| `research/population_impact/DIRECTION.md` | Project pivot rationale: ML shelter prediction → L/M/H classification |
| `research/population_impact/IMPLEMENTATION_PLAN.md` | 5-phase implementation plan |
| `docs/AGENT_QUICKSTART.md` | Bilingual onboarding guide (EN/ZH) |
| `docs/pipeline_flowchart.md` | Full ASCII pipeline diagrams |
| `docs/data_dictionary/NSI_DATA_DICTIONARY_EN.md` | NSI field definitions |
| `docs/data_dictionary/SLOSH_DATA_DICTIONARY_EN.md` | SLOSH field definitions |

## What NOT to Do

- Do not run GUI mode for production
- Do not import `manage.py` on macOS/Linux
- Do not use FIRM zone as spatial filter in `impact-only` mode
- Do not skip FltyId deduplication
- Do not predict shelter_pop directly — produce L/M/H population affected/impacted instead
- Do not use `arc_storm_surge.predictions` (Parquet) — use `predictions_csv` (CSV-backed) to avoid INT64/DOUBLE type mismatch
- Do not join with NSI tables for county FIPS — use spatial join with `us_county_boundaries`
- Do not expand scope beyond what is requested
- Do not ask questions answered by AGENTS.md or this file
