# Processed Parquet Documentation

This document summarizes the **processed Parquet datasets** stored in AWS S3 for the Red Cross Hurricane Capstone project: locations, partitioning, and schemas, for easier querying and downstream use (e.g., FAST).

---

## 1. Overview of Data Locations

| Data Type | S3 Path | Athena Database | Notes |
|----------|---------|-----------------|-------|
| NSI (Structures) | `s3://red-cross-capstone-project-data/processed/nsi/` | `red_cross_hurricane` | Partitioned by State; latest batch generated around 2026-02-10 |
| SLOSH (Storm Surge) | `s3://red-cross-capstone-project-data/processed/slosh/` | `red_cross_hurricane` | Partitioned by `basin`; same processing batch |

In addition, Athena tables may reference `processed/nsi-parquet`, `processed/slosh-parquet`, and `processed/slosh-parquet-v2`. These are historical or alternate ETL outputs; always verify against the actual S3 layout.

---

## 2. NSI Processed Parquet

### 2.1 Path & Partitioning

- **Path**: `s3://red-cross-capstone-project-data/processed/nsi/`
- **Partition Key**: `state` (e.g., `Alabama`, `Florida`, `North_Carolina`)
- **Example Object**: `processed/nsi/state=Alabama/part-00000-xxx.c000.snappy.parquet`
- **Compression**: Snappy

### 2.2 Athena Tables

- **Tables**: `nsi_data`, `processed_nsi_parquet` (may point to slightly different prefixes)
- **Database**: `red_cross_hurricane`

### 2.3 Schema (Columns aligned, minor type differences)

| Column | Type | Description |
|--------|------|-------------|
| bid | string | Building ID |
| bldgtype | string | Building type/material |
| cbfips | string | Census Block FIPS |
| fd_id | bigint | Foreign key / join ID |
| firmzone | string | FEMA FIRM zone |
| found_ht | double | Foundation / first-floor height (feet) |
| found_type | string | Foundation type (`Slab` / `Crawl` / `Pile` / `Basement`) |
| ftprntid | string | Footprint ID |
| ftprntsrc | string | Footprint source |
| ground_elv | double | Ground elevation (feet) |
| ground_elv_m | double | Ground elevation (meters) |
| med_yr_blt | int / bigint | Median year built |
| num_story | int / bigint | Number of stories |
| o65disable | double | Disability rate for 65+ |
| occtype | string | Occupancy type (RES1, COM1, etc.) |
| pop2amo65 | int / bigint | Daytime population >65 |
| pop2amu65 | int / bigint | Daytime population <65 |
| pop2pmo65 | int / bigint | Nighttime population >65 |
| pop2pmu65 | int / bigint | Nighttime population <65 |
| source | string | Data source |
| sqft | double | Building area (sqft) |
| st_damcat | string | Damage category (RES/COM/IND/PUB) |
| students | int / bigint | Student count |
| u65disable | double | Disability rate for <65 |
| val_cont | double | Content value (USD) |
| val_struct | double | Structure value (USD) |
| val_vehic | int / bigint | Vehicle-related value |
| x | double | Longitude (duplicate of `longitude`) |
| y | double | Latitude (duplicate of `latitude`) |
| longitude | double | Longitude |
| latitude | double | Latitude |
| processed_at | timestamp | Processing timestamp |
| source_file | string | (Some tables only) Raw source filename |

Partition column `state` is of type `string` and appears in the S3 path as `state=Alabama`, etc.

---

## 3. SLOSH Processed Parquet

### 3.1 Path & Partitioning

- **Path**: `s3://red-cross-capstone-project-data/processed/slosh/`
- **Partition Key**: `basin` (e.g., `ap3mom`, `br3mom`, `ch2mom`)
- **Example Object**: `processed/slosh/basin=ap3mom/ap3mom_AGL.parquet`
- **Compression**: Snappy

### 3.2 Athena Tables (Two Schemas)

- **slosh_data**: points to `s3://.../processed/slosh-parquet` (v1)
- **slosh_data_parquet**: points to `s3://.../processed/slosh-parquet-v2` (v2)
- **Database**: `red_cross_hurricane`

Both tables are partitioned by `basin` (string), but have **different column definitions**.

### 3.3 Schema: slosh_data (v1)

| Column | Type | Description |
|--------|------|-------------|
| poly_id | string | Grid cell ID |
| i_index | int | Grid index i |
| j_index | int | Grid index j |
| c1_mean, c1_high | double | Category 1 storm surge elevation (mean / high tide) |
| c2_mean, c2_high | double | Category 2 |
| c3_mean, c3_high | double | Category 3 |
| c4_mean, c4_high | double | Category 4 |
| c5_mean, c5_high | double | Category 5 |
| topograp_9 | double | Topography-related field |
| topography | double | Topography/elevation (feet) |
| geometry_wkt | string | Polygon geometry (WKT) |

No `c0_mean` / `c0_high` in this version.

### 3.4 Schema: slosh_data_parquet (v2)

| Column | Type | Description |
|--------|------|-------------|
| poly_id | string | Grid cell ID |
| i_index | int | Grid index i |
| j_index | int | Grid index j |
| c0_mean, c0_high | double | Tropical Storm surge elevation (mean / high tide) |
| c1_mean, c1_high | double | Category 1 |
| c2_mean, c2_high | double | Category 2 |
| c3_mean, c3_high | double | Category 3 |
| c4_mean, c4_high | double | Category 4 |
| c5_mean, c5_high | double | Category 5 |
| topography | int | Topography (integer-coded) |
| geometry_wkt | string | Polygon geometry (WKT) |

No `topograp_9`; `topography` is an integer.

---

## 4. Example Usage

- **NSI by State (Athena)**  
  ```sql
  SELECT bid, longitude, latitude, sqft, occtype, num_story, found_type, found_ht, val_struct, val_cont
  FROM red_cross_hurricane.nsi_data
  WHERE state = 'Florida'
  LIMIT 10;
  ```

- **SLOSH by Basin (Athena)**  
  ```sql
  SELECT poly_id, c1_mean, c2_mean, topography, geometry_wkt
  FROM red_cross_hurricane.slosh_data
  WHERE basin = 'ap3mom'
  LIMIT 10;
  ```

- **List S3 Objects (CLI)**  
  ```bash
  aws s3 ls s3://red-cross-capstone-project-data/processed/nsi/ --recursive
  aws s3 ls s3://red-cross-capstone-project-data/processed/slosh/ --recursive
  ```

---

## 5. Relationship to FAST

- **Building Inventory CSV**: Derived from NSI Parquet by selecting and renaming columns; see `NSI_DATA_DICTIONARY_EN.md` and the FAST input specification.
- **Flood Depth Raster**: FAST consumes GeoTIFF; you must rasterize SLOSH geometry plus a chosen surge elevation column into a .tif, rather than pointing FAST directly to Parquet.

---

*This document reflects the current S3 and Athena metadata. If paths or table definitions change, please update this document accordingly.*
