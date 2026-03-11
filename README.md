# Immediate Tsunami and Storm Surge Population Impact Modeling

CMU Heinz MSPPM 2026 Capstone Project for the American Red Cross.

Property-level storm surge/tsunami impact modeling using FEMA's FAST tool, USACE National Structure Inventory (30M+ buildings), and NOAA SLOSH surge models. The pipeline produces **county-level Population Affected and Population Impacted estimates classified by Low/Medium/High intensity zones**, feeding directly into ARC's Mass Care Planning Assumptions Spreadsheet for shelter, feeding, and emergency supply planning.

## End-to-End Architecture

```
NHC Advisory (storm surge forecast)
    ↓
SLOSH Parquet → rasterize → GeoTIFF (.tif) ──┐
NSI Parquet (Oracle) → clean/filter → FAST CSV ┤→ FAST engine → building-level damage
                                                ↓
                                 Classify each building into L/M/H intensity zone
                                   (surge >12ft=High, 9-12ft=Med, 4-8ft=Low)
                                                ↓
                                 County-level aggregation (Athena spatial join)
                                                ↓
                                 CSV output: Pop Affected (L/M/H) + Pop Impacted (L/M/H)
                                                ↓
                                 ARC Planning Assumptions Spreadsheet (columns J-R)
                                   → Shelter / Feeding / DES estimates
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
  fast_e2e_from_oracle.py       # Main E2E pipeline: Oracle → FAST → predictions
  h3_spatial_index.py           # H3 hex spatial filtering
  duckdb_fast_pipeline.py       # DuckDB-accelerated pipeline
  slosh_to_raster.py            # SLOSH → GeoTIFF converter
research/
  population_impact/
    DIRECTION.md                # Project direction & pivot rationale
    PLAN_ZH.md / PLAN_EN.md     # Implementation plan (中/EN)
    scripts/
      01_county_damage_agg.py   # Athena county-level damage aggregation
      02_fetch_census_svi.py    # Census population + CDC SVI data
      03_build_and_train.py     # Feature matrix builder + model training
    data/
      county_event_features.csv # 386 county-event rows (9 events)
      census_county_population.csv
    outputs/
      validation_report.md      # LOEO-CV validation results
      predictions.csv           # County-level predictions vs ground truth
      arc_shelter_planning_template.xlsx
      metrics.json
    notebooks/
      deploy_population_impact.ipynb  # Colab deployment notebook
configs/
  fast_e2e.yaml                 # Pipeline configuration
  event_state_map.yaml          # Hurricane → state mapping
docs/
  pipeline_flowchart.md         # Architecture diagram
FAST-main/
  Python_env/run_fast.py        # FAST headless engine
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
- `research/population_impact/DIRECTION.md` — Project direction pivot & new pipeline design
- `research/population_impact/PLAN_ZH.md` — Implementation plan with experimental results
- `research/population_impact/outputs/validation_report.md` — Model validation report
- `docs/data_dictionary/NSI_DATA_DICTIONARY_EN.md` / `docs/data_dictionary/SLOSH_DATA_DICTIONARY_EN.md` — Field definitions

## Output

### Building-Level (FAST)

Per-building: `BldgDmgPct` (% damaged), `BldgLossUSD` ($ loss), `Depth_in_Struc` (ft), `Depth_Grid` (surge depth at location).

### County-Level (Population Impact)

Per county per event, classified by intensity zone:

| Output | Columns | Description |
|--------|---------|-------------|
| Population Affected | L / M / H | All residents in surge zone, by intensity |
| Population Impacted | L / M / H | Residents with structural damage requiring displacement |
| Households Needing Shelter | L / M / H | Impacted × ARC conversion rates (H=5%, M=3%, L=1%) |

**Intensity classification** (from ARC Mass Care Planning Assumptions, Figure 9):

| Zone | Storm Surge | Building Damage |
|------|------------|-----------------|
| High | >12 ft | >35% destroyed |
| Medium | 9-12 ft | 11-34% destroyed |
| Low | 4-8 ft | 0-10% destroyed |

Output format: CSV by county, compatible with ARC's Mass Care Planning Assumptions Spreadsheet (columns J-R).

---

## Prediction Results

Results for 9 hurricane events × 3 SLOSH advisories (27 runs, ~3.9M building predictions) are published in two locations:

### Oracle Object Storage (browse)
[https://objectstorage.us-ashburn-1.oraclecloud.com/n/id9odvkah5da/b/arc-capstone-processed-parquet/o/index.html](https://objectstorage.us-ashburn-1.oraclecloud.com/n/id9odvkah5da/b/arc-capstone-processed-parquet/o/index.html)

### AWS S3 + Athena (query)
Files are also mirrored to `s3://red-cross-capstone-project-data/arc-results/` in both CSV and Parquet format, queryable via AWS Athena:

```sql
-- Database: arc_storm_surge  |  Table: predictions
SELECT event, adv, COUNT(*) AS buildings,
       ROUND(SUM(BldgLossUSD)/1e9, 2) AS loss_billion_usd
FROM arc_storm_surge.predictions
GROUP BY event, adv
ORDER BY event, adv;
```

**Coverage**

| Event | Advisories | Buildings | Notes |
|-------|-----------|-----------|-------|
| BERYL_2024 | 39, 40, 41 | ~107K each | TX/LA Gulf Coast |
| DEBBY_2024 | 18, 19, 20 | ~103K each | FL/GA/NC/SC/VA |
| FLORENCE_2018 | 63, 64, 65 | 17K–32K | NC/SC/VA Atlantic |
| HELENE_2024 | 14, 15, 16 | 240K–475K | FL/GA/NC/SC |
| IAN_2022 | 31, 32, 33 | ~119K–122K | FL/NC/SC |
| IDALIA_2023 | 18, 19, 20 | 62K–124K | FL/GA/SC |
| IDA_2021 | 16, 17, 18 | ~412K each | AL/LA/MS |
| MICHAEL_2018 | 20, 21, 22 | ~900 each | Coastal GA (small raster footprint) |
| MILTON_2024 | 20, 21, 22 | 70K–208K | FL |

### Output Column Reference

**Building Attributes**

| Column | Description |
|--------|-------------|
| `FltyId` | NSI unique building ID |
| `Occ` | Occupancy type (RES1=single-family, RES3=multi-family, COM1=commercial, …) |
| `Cost` | Replacement cost ($) |
| `Area` | Floor area (sqft) |
| `NumStories` | Stories above ground |
| `FoundationType` | 2=Pier, 4=Basement, 5=Crawlspace, 7=Slab |
| `FirstFloorHt` | First floor height above grade (ft) |
| `Latitude` / `Longitude` | WGS84 coordinates |
| `state` | State name |

**Flood Depth**

| Column | Description |
|--------|-------------|
| `Depth_Grid` | Surge depth from SLOSH raster at building location (ft) |
| `Depth_in_Struc` | Effective depth inside structure = `Depth_Grid` − `FirstFloorHt` (ft) |

**Damage & Loss**

| Column | Description |
|--------|-------------|
| `BldgDmgPct` | Structural damage percentage (%) |
| `BldgLossUSD` | Structural loss ($) |
| `ContentCost` | Contents replacement value ($) |
| `ContDmgPct` | Contents damage percentage (%) |
| `ContentLossUSD` | Contents loss ($) |
| `InventoryLossUSD` | Inventory loss ($ — commercial buildings) |

**Debris & Recovery**

| Column | Description |
|--------|-------------|
| `Debris_Fin` | Finish debris (tons) |
| `Debris_Struc` | Structural debris (tons) |
| `Debris_Found` | Foundation debris (tons) |
| `Debris_Tot` | Total debris (tons) |
| `Restor_Days_Min` / `Restor_Days_Max` | Estimated restoration days (range) |

**Partition & Provenance**

| Column | Description |
|--------|-------------|
| `event` | Hurricane event slug (Athena partition key) |
| `adv` | Advisory number (Athena partition key) |
| `raster_name` | Source SLOSH raster filename |
| `run_id` | Pipeline run ID (timestamp-based) |
| `flc` | Flood class: CoastalA / CoastalV / Riverine |

**Common Athena Queries**

```sql
-- Damage summary by state for a single event/advisory
SELECT state,
       COUNT(*)                          AS buildings,
       ROUND(AVG(Depth_Grid), 1)         AS avg_depth_ft,
       ROUND(SUM(BldgLossUSD)/1e6, 1)   AS loss_M_usd,
       ROUND(SUM(Debris_Tot))            AS debris_tons
FROM arc_storm_surge.predictions
WHERE event = 'IDA_2021' AND adv = 18
GROUP BY state ORDER BY loss_M_usd DESC;

-- Residential buildings with depth > 4 ft (high-need proxy)
SELECT state, Occ, COUNT(*) AS households
FROM arc_storm_surge.predictions
WHERE event = 'HELENE_2024' AND Depth_Grid > 4
  AND Occ LIKE 'RES%'
GROUP BY state, Occ ORDER BY households DESC;

-- Cross-event loss comparison
SELECT event,
       SUM(BldgLossUSD)/1e9  AS total_loss_B,
       COUNT(*)               AS buildings
FROM arc_storm_surge.predictions
GROUP BY event ORDER BY total_loss_B DESC;
```

---

## Population Impact Research

The `research/population_impact/` directory contains the population impact estimation pipeline. Key finding from client meetings: ARC needs **Population Affected/Impacted by county in L/M/H intensity zones** — not direct shelter population prediction.

The pipeline classifies 3.5M building-level FAST predictions into intensity zones using surge depth thresholds, aggregates to county level, and outputs in a format compatible with ARC's Mass Care Planning Assumptions Spreadsheet.

See `research/population_impact/DIRECTION.md` for the full rationale and implementation plan.

---

## Team

CMU Heinz College — Master of Science in Public Policy and Management, 2026
