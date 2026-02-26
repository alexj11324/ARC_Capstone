# Immediate Tsunami and Storm Surge Population Impact Modeling

CMU Heinz MSPPM 2026 Capstone Project for the American Red Cross.

Property-level storm surge/tsunami impact modeling using FEMA's FAST tool, USACE National Structure Inventory (30M+ buildings), and NOAA SLOSH surge models. Estimates building damage, displaced population, and high-need populations to inform Red Cross shelter and casework planning.

## Architecture

```
NSI Parquet (Oracle) → clean/filter → FAST CSV ─┐
SLOSH Parquet → rasterize → GeoTIFF (.tif) ─────┤→ FAST engine → damage predictions
```

See `docs/pipeline_flowchart.md` for the full Mermaid diagram.

## Prerequisites

- Python 3.10+
- OCI CLI configured with access to `arc-capstone-processed-parquet` bucket
- FAST engine (`FAST-main/Python_env/run_fast.py`)

```bash
pip install pyarrow rasterio pyyaml h3 duckdb geopandas
```

## Quick Start

```bash
# Run the E2E pipeline for a single state
python scripts/fast_e2e_from_oracle.py \
  --state-scope Florida \
  --raster-name auto \
  --config configs/fast_e2e.yaml

# DuckDB-accelerated variant
python scripts/duckdb_fast_pipeline.py --state Florida

# Convert SLOSH to raster
python scripts/slosh_to_raster.py --basin ny3mom --category 3 --tide high
```

## Project Structure

```
scripts/
  fast_e2e_from_oracle.py   # Main E2E pipeline
  h3_spatial_index.py       # H3 hex spatial filtering
  duckdb_fast_pipeline.py   # DuckDB-accelerated pipeline
  slosh_to_raster.py        # SLOSH → GeoTIFF converter
configs/
  fast_e2e.yaml             # Pipeline configuration
  event_state_map.yaml      # Hurricane → state mapping
docs/
  pipeline_flowchart.md     # Architecture diagram
FAST-main/
  Python_env/run_fast.py    # FAST headless engine
```

## Data Sources

| Source | Description | Format |
|--------|-------------|--------|
| NSI | USACE National Structure Inventory 2022 | Parquet, partitioned by state |
| SLOSH | NOAA MOM surge grids | Parquet, partitioned by basin |
| SVI | CDC Social Vulnerability Index | Census tract level |

## Key Documentation

- `CLAUDE.md` — Agent instructions, data contracts, known issues
- `AGENTS.md` — Execution contract and column mapping rules
- `NSI_DATA_DICTIONARY_EN.md` / `SLOSH_DATA_DICTIONARY_EN.md` — Field definitions

## Output

Per-building: `BldgDmgPct` (% damaged), `BldgLossUSD` ($ loss), `Depth_in_Struc` (ft). These feed into population disruption and Red Cross service demand estimates.

## Team

CMU Heinz College — Master of Science in Public Policy and Management, 2026
