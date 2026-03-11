# ARC Capstone — End-to-End Pipeline Flowchart

## Full Pipeline (NHC Advisory to ARC Planning Spreadsheet)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          DATA SOURCES                                   │
├──────────────────┬──────────────────────┬───────────────────────────────┤
│  NHC Advisory    │  NSI 2022            │  Census ACS 5-year            │
│  (storm surge    │  National Structure  │  (county population)          │
│   forecast)      │  Inventory (30M+     │                               │
│                  │   buildings)         │                               │
└────────┬─────────┴──────────┬───────────┴───────────────┬───────────────┘
         │                    │                           │
         v                    v                           │
┌─────────────────┐  ┌─────────────────────┐              │
│  SLOSH Model    │  │  Building Inventory  │              │
│  Storm Surge    │  │  CSV (FltyId, Occ,   │              │
│  Simulation     │  │  Cost, Area, Stories,│              │
└────────┬────────┘  │  FoundationType,     │              │
         │           │  FirstFloorHt,       │              │
         v           │  Lat/Lon)            │              │
┌─────────────────┐  └──────────┬──────────┘              │
│  Flood Depth    │             │                          │
│  Raster (.tif)  │             │                          │
└────────┬────────┘             │                          │
         │                      │                          │
         v                      v                          │
       ┌──────────────────────────┐                        │
       │      FAST Engine         │                        │
       │  (Hazus Flood Assessment │                        │
       │   Structure Tool)        │                        │
       └────────────┬─────────────┘                        │
                    │                                      │
                    v                                      │
       ┌──────────────────────────┐                        │
       │  Building-Level          │                        │
       │  Predictions (Athena)    │                        │
       │  3.5M buildings          │                        │
       │  BldgDmgPct, Depth_Grid  │                        │
       │  Depth_in_Struc,         │                        │
       │  BldgLossUSD             │                        │
       └────────────┬─────────────┘                        │
                    │                                      │
  ══════════════════╪══════════════════════════════════════════════════
   L/M/H Pipeline   │                                      │
  ══════════════════╪══════════════════════════════════════════════════
                    │                                      │
                    v                                      │
       ┌──────────────────────────┐                        │
       │  Step 1: Deduplicate     │                        │
       │  MAX(damage) per bldg    │                        │
       │  across advisories       │                        │
       └────────────┬─────────────┘                        │
                    │                                      │
                    v                                      │
       ┌──────────────────────────┐                        │
       │  Step 2: Classify Zone   │                        │
       │  Surge >12ft  -> HIGH    │                        │
       │  Surge 9-12ft -> MEDIUM  │                        │
       │  Surge 4-8ft  -> LOW     │                        │
       │  (damage % fallback)     │                        │
       └────────────┬─────────────┘                        │
                    │                                      │
                    v                                      │
       ┌──────────────────────────┐                        │
       │  Step 3: Spatial Join    │                        │
       │  ST_CONTAINS(county,     │                        │
       │  ST_POINT(lon,lat))      │                        │
       │  -> county_fips5         │                        │
       └────────────┬─────────────┘                        │
                    │                                      │
                    v                                      │
       ┌──────────────────────────┐                        │
       │  Step 4: County Agg      │                        │
       │  GROUP BY event,county,  │                        │
       │  zone                    │                        │
       │  Pop = bldg_count x 2.53 │                        │
       └──────┬───────────┬───────┘                        │
              │           │                                │
              v           v                                │
  ┌─────────────────┐ ┌─────────────────┐                  │
  │  Long CSV       │ │  Wide CSV       │                  │
  │  836 rows       │ │  383 rows       │                  │
  │  event x county │ │  event x county │                  │
  │  x zone         │ │                 │                  │
  └─────────────────┘ └────────┬────────┘                  │
                               │                           │
                               v                           v
                      ┌──────────────────────────────────────┐
                      │  Census Join + ARC Conversion Rates   │
                      │  Shelter: H=5%, M=3%, L=1%            │
                      │  Feeding: H=12%, M=7%, L=3%           │
                      └──────────────────┬───────────────────┘
                                         │
                                         v
                      ┌──────────────────────────────────────┐
                      │  Planning Assumptions Output          │
                      │  planning_assumptions_output.csv      │
                      │  arc_planning_template_lmh.xlsx       │
                      └──────────────────┬───────────────────┘
                                         │
                                         v
                      ┌──────────────────────────────────────┐
                      │  ARC Mass Care Planning Assumptions   │
                      │  Spreadsheet — Columns J through R    │
                      └──────────────────────────────────────┘
```

---

## Script-Level Pipeline Map

```
 Upstream (existing)               Phase 1                   Phase 2                  Phase 3
 ═══════════════════     ═══════════════════════     ═════════════════════     ═══════════════════

 slosh_to_raster.py      04_classify_lmh.py          05_format_for_           06_validate_lmh.py
 SLOSH -> GeoTIFF        Athena query:               spreadsheet.py           GT comparison:
        |
        v                 predictions_csv             county_lmh_              planning_
 fast_e2e_from_           ─────────────>              features.csv             assumptions_
 oracle.py                                            ────────────>            output.csv
 NSI+Raster->FAST         1. Dedup across advs        1. Join Census           ────────────>
        |                  2. Classify L/M/H           2. Conversion rates      1. Join GT
        v                  3. Spatial join county      3. Sanity checks         2. RMSE/MAE/R2
                           4. County aggregation       4. Round integers        3. Per-event
 Athena                           |                           |                    breakdown
 predictions_csv                  |                           |                       |
 (3.5M bldgs)              ┌─────┴─────┐              ┌──────┴──────┐                |
                            |           |              |             |                v
                       long.csv    wide.csv        output.csv   output.xlsx    validation_
                       (836 rows)  (383 rows)      (383 rows)                  report.md

                                                            Deployment
                                                   ═══════════════════════
                                                   deploy_population_
                                                   impact.ipynb (Colab)
```

---

## ARC Planning Assumptions Column Mapping

```
Pipeline Output                    ARC Spreadsheet
─────────────────                  ─────────────────
pop_affected_low          ──────>  Column J  (Pop Affected - Low)
pop_affected_medium       ──────>  Column K  (Pop Affected - Medium)
pop_affected_high         ──────>  Column L  (Pop Affected - High)
pop_impacted_low          ──────>  Column M  (Pop Impacted - Low)
pop_impacted_medium       ──────>  Column N  (Pop Impacted - Medium)
pop_impacted_high         ──────>  Column O  (Pop Impacted - High)
hh_shelter_low            ──────>  Column P  (HH Shelter - Low)
hh_shelter_medium         ──────>  Column Q  (HH Shelter - Medium)
hh_shelter_high           ──────>  Column R  (HH Shelter - High)
```

---

## Intensity Zone Classification Logic

```
Building from FAST predictions
    |
    +-- depth_grid > 12 ft ──────────> HIGH
    +-- depth_grid >= 9 ft ──────────> MEDIUM
    +-- depth_grid >= 4 ft ──────────> LOW
    |
    |   (fallback if surge < 4 ft)
    +-- bldgdmgpct > 35% ───────────> HIGH
    +-- bldgdmgpct > 15% ───────────> MEDIUM
    +-- bldgdmgpct > 0%  ───────────> LOW
    |
    +-- otherwise ───────────────────> NONE (excluded)
```

---

## Data Volume Summary

| Stage | Volume | Notes |
|-------|--------|-------|
| FAST predictions | ~3.5M buildings | 9 events x 3 advisories |
| After dedup | ~1.2M buildings | MAX across advisories per event |
| After RES filter + spatial join | ~800K buildings | Residential only, matched to counties |
| County x zone (long) | 836 rows | 9 events, 203 unique counties |
| County-event (wide) | 383 rows | One row per event-county pair |
| Final output | 383 rows | With Census pop + shelter/feeding estimates |
