# Immediate Tsunami and Storm Surge Population Impact Modeling

A rapid, programmatic geospatial data pipeline bridging predictive hurricane water models with FEMA's deep structural damage index. Designed to equip the American Red Cross and mass-care planners with data-driven shelter placement estimates within hours of severe weather advisories, without the latency of relational database bottlenecks.

## Key Features

- **No-DB "Storage-as-Compute" Engine**: Exclusively pivots around `DuckDB` pulling S3 `.parquet` objects, entirely side-stepping SQL Database IOPS bottlenecks.
- **Headless FEMA Assessment**: Hooks into FEMA's native FAST python core to probabilistically assess the physical damage percentage on millions of granular structures. 
- **Geospatial Parallelism**: Sub-divides NOAA's SLOSH `.tif` warning grids and maps them identically onto USACE's National Structure Inventory (NSI) to produce real-time county-by-county impact analytics.

---

## Tech Stack

- **Language**: Python 3.10+
- **Core Architecture Framework**: `duckdb` for in-memory analytics; `pyarrow` for columnar format streaming.
- **Geodata Tooling**: `rasterio` (for TIFF grids), `geopandas` (for vector boundaries), `h3` (for hex grids).
- **Physical Model**: FEMA Flood Assessment Structure Tool (FAST) headless engine.
- **Infrastructure**: AWS S3 (Blobs), AWS EC2 Spot Instances (Batch Worker Nodes), AWS Athena.

---

## Prerequisites

- **Python**: Version 3.10+ (via pyenv or native)
- **Conda/Mamba**: For clean distribution of GIS wheels (especially GDAL/Rasterio)
- **AWS CLI**: Pre-configured environment containing valid IAM roles (`aws configure`).

---

## Getting Started

### 1. Clone the Repository

```bash
git clone https://github.com/alexj11324/ARC_Capstone.git
cd ARC_Capstone
```

### 2. Setup the Python Environment

Due to native C-bindings used by geospatial tooling, installing pure headers via Conda is recommended over native Pip.

```bash
conda create -n arc-pipeline python=3.10 -y
conda activate arc-pipeline
pip install duckdb pyarrow pandas geopandas rasterio pypdf pyyaml h3
```

### 3. Execution Setup Matrix

The base execution revolves around NOAA models. Ensure you configure your states inside the YAML system.

```bash
# Modify routing behaviors here
nano configs/event_state_map.yaml
```

### 4. Running Pipeline Modules Locally

*Translate raw NOAA points to Depth TIFF:*
```bash
python scripts/slosh_to_raster.py --basin ny3mom --category 3 --tide high
```

*Run Core Damage Engine:*
```bash
python scripts/fast_e2e_from_oracle.py --state-scope Florida --raster-name auto --config configs/fast_e2e.yaml
```

---

## Architecture Overview

This section is for engineers inheriting the pipeline. Instead of relying on a multi-hour RDBMS spatial query, we rely on decoupled Bash nodes interacting seamlessly via DuckDB.

### Directory Structure

```text
ARC_Capstone/
├── C4-Documentation/        # Granular Architectural Diagrams (Component, Context)
├── conductor/               # Internal tracking status of tracks, configurations, and strict guidelines
├── configs/                 # YAML Event Router and end-to-end execution constants
├── docs/                    
│   ├── manual/              # Long-form system manual 
│   └── wiki/                # Deep Principals guide & Onboarding tutorials
├── FAST-main/               # Embedded FEMA FAST Assessment core module
└── scripts/                 # Core Pipeline operations 
    ├── duckdb_fast_pipeline.py    # Spatial intersections and logic orchestration
    ├── fast_e2e_from_oracle.py    # Legacy-named main trigger route
    ├── slosh_to_raster.py         # Sub-process building GeoTIFF from points
    ├── deploy_to_instances.py     # Remote AWS node bootstrapping
    └── launch_cloud_parallel.sh   # Ephemeral execution trigger
```

### Data Flow Execution

1. `SLOSH warning` is caught and converted to a bounding box raster map by `slosh_to_raster`.
2. Python uses `PyArrow` to sweep the `NSI_Baseline.parquet` (AWS S3 source).
3. `duckdb` memory engines are initialized; a query executes `ST_MakeValid` geometric bounding matching on both components inside memory.
4. Results (water heights at coordinates) are flattened and dumped as `fast_input.csv` down to the local file system.
5. The `FEMA FAST` sub-shell is invoked, reading the structures, comparing it with static building `Cost` tables, and producing a metric called `BldgDmgPct`.
6. Result mapped back to S3 for Red Cross operators to query in Athena.

---

## Environment Variables

For security bounds, do NOT commit actual AWS keys into the version log or YAML definitions!

### Required System Environments

| Variable | Description |
| --- | --- |
| `AWS_ACCESS_KEY_ID` | User IAM to read NSI baseline objects |
| `AWS_SECRET_ACCESS_KEY` | User IAM password key |
| `AWS_DEFAULT_REGION` | Usually `us-east-1` or `us-west-2` |

---

## Available Scripts

| Tool/Command | Responsibility |
| --- | --- |
| `python scripts/fast_e2e_from_oracle.py` | Runs End-to-End local batch node. |
| `bash scripts/launch_cloud_parallel.sh` | Orchestrates remote spinning via Boto3/CLI. |
| `bash scripts/monitor_parallel.sh` | Hooks into EC2/Batch lifecycle stream and surfaces active outputs to host. |
| `bash scripts/terminate_parallel.sh` | Safety parachute destroying active run queues. |

---

## Testing

*(Governed by Strict TDD under Conductor Rules)*

Execute all local unit tests (if configured via `pytest`) prior to PR generation, specifically mocking the `aws_s3_read()` logic utilizing mock parquet stubs locally to verify matrix offsets independent of cloud uptime.

```bash
# E.g.
pytest tests/
```

---

## Deployment

The system is deployed purely as ephemeral functions without long-standing web servers.

### AWS Spot Execution (Production Pattern)

To operate over 30 states, do not trigger entirely locally:

```bash
# Deploys Python environment onto nodes, triggers intersections concurrently 
bash scripts/launch_cloud_parallel.sh --regions us-east-1 --max-nodes 10
```

### AWS Athena (Final Serving)

Instead of a DB connection string, Red Cross analysts configure Excel using AWS ODBC Athena Plugin targeting the output S3 bucket defined in `configs/fast_e2e.yaml`.

---

## Troubleshooting

### Spatial Dependency Errors

**Error**: `ModuleNotFoundError: No module named 'rasterio._base'` or GDAL conflict.
**Solution**: Never try to pip install rasterio on Windows cleanly. Use conda exclusively: `conda install conda-forge::rasterio`. 

### FAST Engine Fails to Evaluate

**Error**: `ValueError: Missing required FAST column 'FirstFloorHt'`
**Solution**: Occurs if the DuckDB extraction query drops or renames NSI columns. Review `duckdb_fast_pipeline.py` schema output map and cross reference against expected headers inside `FAST-main/run_fast.py`.

### S3 Permission Denied
**Error**: `ArrowIOError: AWS Error ACCESS_DENIED`
**Solution**: Verify your AWS CLI is authenticated (`aws sts get-caller-identity`). Re-authenticate your SSO/MFA payload if expired.
