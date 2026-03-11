# ARC Population Impact — L/M/H Pipeline Implementation Plan

> Generated: 2026-03-10
> Based on: DIRECTION.md + 3 documentation discovery subagent reports
> Execution model: Each phase is self-contained, executable in a fresh chat context

---

## Phase 0: Allowed APIs & Anti-Pattern Guards

### Confirmed Available Resources

| Resource | Identifier | Confirmed In |
|----------|-----------|--------------|
| Predictions table | `arc_storm_surge.predictions` | README.md:90, 01_county_damage_agg.py:30 |
| County boundaries | `arc_analysis.us_county_boundaries` | 01_county_damage_agg.py:36 |
| NSI table | `red_cross_hurricane.nsi_data` (or `_parquet`) | 01_county_damage_agg.py:101-117 |
| Census API | `https://api.census.gov/data/2022/acs/acs5` | 02_fetch_census_svi.py:47-98 |
| CDC SVI | `https://data.cdc.gov/api/views/4d8n-kk8a/rows.csv` | 02_fetch_census_svi.py:128-173 |

### Key FAST Output Columns (from README.md §Output Column Reference)

| Column | Type | Use |
|--------|------|-----|
| `Depth_Grid` | Double | Surge depth at building (ft) — **primary intensity classifier** |
| `Depth_in_Struc` | Double | Water inside structure (ft) — **impact/displacement indicator** |
| `BldgDmgPct` | Double | Structural damage (%) — **damage-based classification fallback** |
| `Occ` | String | Occupancy type — filter `LIKE 'RES%'` for residential |
| `Latitude` / `Longitude` | Double | WGS84 coordinates for spatial join |
| `FltyId` | String | Building ID for deduplication |

### NSI Population Columns (from 01_county_damage_agg.py:150-158, conditional availability)

| Column | Description | Availability |
|--------|-------------|-------------|
| `pop2pmu65` | Population under 65 (nighttime) | Probe via schema discovery |
| `pop2pmo65` | Population 65+ (nighttime) | Probe via schema discovery |
| `cbfips` | Census block FIPS → derive `county_fips5 = SUBSTR(cbfips, 1, 5)` | Always available |

### Reusable Code Snippets

| Snippet | Source File | Lines | What It Does |
|---------|-----------|-------|--------------|
| `AthenaClient` class | `01_county_damage_agg.py` | 44–98 | boto3 Athena wrapper with polling + pagination |
| NSI table discovery | `01_county_damage_agg.py` | 101–131 | Probe table existence + column availability |
| County agg SQL template | `01_county_damage_agg.py` | 162–256 | Three-stage CTE: dedup → NSI join → county GROUP BY |
| Census API fetch | `02_fetch_census_svi.py` | 47–98 | ACS 5-year population by county |
| SVI CSV download | `02_fetch_census_svi.py` | 128–173 | CDC SVI with fallback |
| Colab config block | `deploy_population_impact.ipynb` | Cell 2 | Constants + multipliers |

### Anti-Pattern Guards

- **DO NOT** predict `shelter_pop` directly — produce L/M/H population counts instead
- **DO NOT** use ML models (XGBoost/RF/Ridge) — this is a deterministic classification + aggregation task
- **DO NOT** invent Athena columns — always reference README.md §Output Column Reference
- **DO NOT** use `n_displaced_buildings` as column name in CSV — the exported name is `n_displaced_bldgs`
- **DO NOT** skip FltyId deduplication — duplicates inflate counts across advisories
- **DO NOT** assume NSI pop columns exist — use `01_county_damage_agg.py`'s schema discovery pattern
- **DO NOT** hardcode county FIPS — derive from NSI `cbfips` via `SUBSTR(CAST(cbfips AS VARCHAR), 1, 5)`

### Design Decisions (Pre-Resolved)

| Decision | Resolution | Rationale |
|----------|-----------|-----------|
| Surge field for zone classification | `Depth_Grid` | Ground-level surge = event intensity; `Depth_in_Struc` depends on building elevation |
| "Affected" threshold | `Depth_Grid >= 4 ft` (Low starts at 4ft per ARC Figure 9) | Matches ARC's official Low intensity definition |
| "Impacted" definition | `BldgDmgPct > 0` AND residential | Any structural damage = impacted per ARC/Michael definition |
| Population source | NSI pop fields if available, else building count × 2.53 | Consistent with existing pipeline; Census as supplementary |
| Classification approach | Surge-primary, damage-fallback | Surge classifies zone; damage confirms impact within zone |
| Advisory selection | MAX(damage) across advisories per building (existing dedup logic) | Worst-case per building, already implemented in 01_county_damage_agg.py:170-189 |

---

## Phase 1: New Athena SQL — Intensity Zone Classification

### What to Implement

Create `research/population_impact/scripts/04_classify_lmh.py` that:

1. **Copy** the `AthenaClient` class from `01_county_damage_agg.py:44-98`
2. **Copy** the NSI table discovery from `01_county_damage_agg.py:101-131`
3. **Write a new SQL query** that classifies each building into L/M/H intensity zones and aggregates to county level

### SQL Specification

```sql
-- Stage 1: Dedup buildings across advisories (COPY from 01:170-189)
WITH deduped AS (
    SELECT event, fltyid,
           MAX(bldgdmgpct) AS bldgdmgpct,
           MAX(depth_grid) AS depth_grid,
           MAX(depth_in_struc) AS depth_in_struc,
           MAX(bldglossusd) AS bldglossusd,
           ARBITRARY(occ) AS occ,
           ARBITRARY(latitude) AS latitude,
           ARBITRARY(longitude) AS longitude
    FROM arc_storm_surge.predictions
    GROUP BY event, fltyid
),

-- Stage 2: Join NSI for county FIPS + population (ADAPT from 01:191-201)
joined AS (
    SELECT d.*,
           SUBSTR(CAST(n.cbfips AS VARCHAR), 1, 5) AS county_fips5,
           COALESCE(n.pop2pmu65, 0) + COALESCE(n.pop2pmo65, 0) AS pop_night,
           COALESCE(n.pop2pmo65, 0) AS pop_elderly,
           -- Intensity zone classification (NEW)
           CASE
               WHEN d.depth_grid > 12 THEN 'HIGH'
               WHEN d.depth_grid >= 9 THEN 'MEDIUM'
               WHEN d.depth_grid >= 4 THEN 'LOW'
               WHEN d.bldgdmgpct > 35 THEN 'HIGH'
               WHEN d.bldgdmgpct > 15 THEN 'MEDIUM'
               WHEN d.bldgdmgpct > 0 THEN 'LOW'
               ELSE 'NONE'
           END AS intensity_zone,
           -- Is this building "impacted"? (structural damage)
           CASE WHEN d.bldgdmgpct > 0 OR d.depth_in_struc > 0 THEN 1 ELSE 0 END AS is_impacted
    FROM deduped d
    LEFT JOIN {nsi_table} n ON LOWER(CAST(d.fltyid AS VARCHAR)) = LOWER(CAST(n.bid AS VARCHAR))
    WHERE d.occ LIKE 'RES%'  -- Residential only
),

-- Stage 3: County × intensity aggregation (NEW)
SELECT
    event,
    county_fips5,
    intensity_zone,
    -- Population Affected: all residential buildings in this zone
    COUNT(*) AS n_buildings_affected,
    SUM(pop_night) AS pop_affected,
    -- Population Impacted: buildings with actual damage in this zone
    SUM(is_impacted) AS n_buildings_impacted,
    SUM(CASE WHEN is_impacted = 1 THEN pop_night ELSE 0 END) AS pop_impacted,
    -- Damage context
    AVG(bldgdmgpct) AS avg_damage_pct,
    MAX(depth_grid) AS max_surge_ft,
    SUM(bldglossusd) AS total_loss_usd
FROM joined
WHERE intensity_zone != 'NONE'
  AND county_fips5 IS NOT NULL
GROUP BY event, county_fips5, intensity_zone
ORDER BY event, county_fips5, intensity_zone
```

4. **Pivot** the long-format result (one row per county × zone) into wide format (one row per county with L/M/H columns) using Python pandas:

```python
# Pivot from long to wide
wide = long_df.pivot_table(
    index=['event', 'county_fips5'],
    columns='intensity_zone',
    values=['pop_affected', 'pop_impacted', 'n_buildings_affected', 'n_buildings_impacted'],
    fill_value=0
).reset_index()
# Flatten multi-level columns: pop_affected_HIGH → pop_affected_high
wide.columns = ['_'.join(c).strip('_').lower() for c in wide.columns]
```

5. **Save** output to `research/population_impact/data/county_lmh_features.csv`

### Fallback for Missing NSI Pop Columns

If NSI `pop2pmu65` / `pop2pmo65` are not available (discovered at runtime):
- Replace `SUM(pop_night)` with `COUNT(*) * 2.53` (building count × avg household size)
- Log a warning about the fallback

### Verification Checklist

- [ ] SQL executes without error on Athena
- [ ] Output has rows for all 9 events (or at least the 5 with GT overlap)
- [ ] Each county has at most 3 intensity zones (LOW, MEDIUM, HIGH)
- [ ] `pop_impacted <= pop_affected` for every county × zone
- [ ] `SUM(pop_affected)` per event is in reasonable range (not 0, not > state population)
- [ ] Wide-format CSV has columns: `pop_affected_low`, `pop_affected_medium`, `pop_affected_high`, `pop_impacted_low`, `pop_impacted_medium`, `pop_impacted_high`

---

## Phase 2: Census Population Join & Spreadsheet Formatting

### What to Implement

Create `research/population_impact/scripts/05_format_for_spreadsheet.py` that:

1. **Load** `county_lmh_features.csv` (Phase 1 output)
2. **Load** Census population data — **Copy** the Census API pattern from `02_fetch_census_svi.py:47-98`, or load existing `data/census_county_population.csv`
3. **Join** Census county population to validate population caps
4. **Apply ARC conversion rates** to compute shelter/feeding estimates:

```python
# ARC Mass Care Conversion Rates (Figure 16)
CONVERSION_RATES = {
    'shelter': {'high': 0.05, 'medium': 0.03, 'low': 0.01},
    'feeding': {'high': 0.12, 'medium': 0.07, 'low': 0.03},
}

for service, rates in CONVERSION_RATES.items():
    for zone, rate in rates.items():
        df[f'hh_{service}_{zone}'] = df[f'pop_impacted_{zone}'] * rate
```

5. **Format output** to match Planning Assumptions Spreadsheet columns J-R:

```python
OUTPUT_COLUMNS = [
    'event', 'county_fips5', 'county_name', 'state', 'census_pop',
    # Columns J/K/L: Population Affected
    'pop_affected_low', 'pop_affected_medium', 'pop_affected_high',
    # Columns M/N/O: Population Impacted
    'pop_impacted_low', 'pop_impacted_medium', 'pop_impacted_high',
    # Columns P/Q/R: Households Needing Shelter
    'hh_shelter_low', 'hh_shelter_medium', 'hh_shelter_high',
    # Bonus: Feeding estimates
    'hh_feeding_low', 'hh_feeding_medium', 'hh_feeding_high',
]
```

6. **Save** to `research/population_impact/outputs/planning_assumptions_output.csv`
7. **Also export** to Excel with two sheets: "Estimates" and "Parameters"

### Sanity Checks (Hardcoded Post-Processing)

```python
# Cap: impacted <= affected per zone
for zone in ['low', 'medium', 'high']:
    df[f'pop_impacted_{zone}'] = df[[f'pop_impacted_{zone}', f'pop_affected_{zone}']].min(axis=1)

# Cap: total affected <= county population
total_affected = df[['pop_affected_low', 'pop_affected_medium', 'pop_affected_high']].sum(axis=1)
excess = total_affected / df['census_pop']
if (excess > 1.0).any():
    log.warning(f"{(excess > 1.0).sum()} counties have affected > census_pop, capping")
    # Scale down proportionally
```

### Documentation References

- ARC conversion rates: DIRECTION.md § 2.5 (Figure 16)
- Spreadsheet columns J-R: DIRECTION.md § 2.6
- Census API pattern: `02_fetch_census_svi.py:47-98`
- County name lookup: Census API returns `NAME` field

### Verification Checklist

- [ ] Output CSV has exactly the columns in `OUTPUT_COLUMNS`
- [ ] `pop_impacted_X <= pop_affected_X` for all rows and zones
- [ ] `sum(pop_affected_*) <= census_pop` for all rows
- [ ] `hh_shelter_high = pop_impacted_high × 0.05` (spot check)
- [ ] Excel file opens correctly with two sheets
- [ ] No NaN values in output (all filled with 0)

---

## Phase 3: Validation Against Historical Ground Truth

### What to Implement

Create `research/population_impact/scripts/06_validate_lmh.py` that:

1. **Load** Phase 2 output (`planning_assumptions_output.csv`)
2. **Load** Ground Truth — **Copy** the GT loading pattern from `03_build_and_train.py:75-92`
3. **Join** on `(event, county_fips5)` — expect 56 matches across 5 events (PLAN_ZH.md § 8.1)
4. **Compare** our L/M/H estimates against GT `shelter_pop`:
   - Our total shelter estimate = `hh_shelter_low + hh_shelter_medium + hh_shelter_high`
   - Compare against GT `shelter_pop`
   - Compute RMSE, MAE, R² on the comparison
5. **Threshold sensitivity analysis** — vary surge depth thresholds and measure output change:

```python
THRESHOLD_VARIANTS = [
    {'name': 'default',  'low': 4, 'med': 9,  'high': 12},
    {'name': 'tight',    'low': 3, 'med': 8,  'high': 11},
    {'name': 'loose',    'low': 5, 'med': 10, 'high': 13},
    {'name': 'verylow',  'low': 2, 'med': 6,  'high': 10},
]
# For each variant: re-run classification SQL, compare results
```

6. **Per-event summary table**: for each event, show total pop_affected, pop_impacted, shelter estimate vs GT
7. **Generate** validation report: `research/population_impact/outputs/lmh_validation_report.md`

### Comparison Framework

```python
# For each matched county-event:
total_shelter_est = hh_shelter_low + hh_shelter_medium + hh_shelter_high
total_shelter_gt = shelter_pop  # from Ground Truth

# Metrics
rmse = sqrt(mean((est - gt)**2))
mae = mean(abs(est - gt))
r2 = 1 - sum((est - gt)**2) / sum((gt - gt.mean())**2)
# Also: per-event RMSE breakdown
```

### Expected Baseline

From PLAN_ZH.md § 8.2:
- Previous Tier 1 (flat 0.73% shelter rate): RMSE = 546.6
- Previous Tier 2 (ML ensemble): RMSE = 407.6, R² = -0.308
- **Target**: The L/M/H approach should be at least comparable (RMSE ≤ 550), and more importantly, produce output in the format ARC actually needs

### Verification Checklist

- [ ] 56 matched county-event rows (5 events)
- [ ] Validation report includes per-event metrics table
- [ ] Threshold sensitivity shows which variant best matches GT
- [ ] Sanity checks pass (0 violations)
- [ ] Report recommends final threshold values

---

## Phase 4: Updated Colab Notebook

### What to Implement

Rewrite `research/population_impact/notebooks/deploy_population_impact.ipynb` to:

1. **Cell 0-1**: Title + pip install (keep existing structure)
2. **Cell 2**: New configuration — **Replace** old `SHELTER_RATE` config with:

```python
# Intensity zone thresholds (ARC Mass Care Planning Assumptions, Figure 9)
SURGE_THRESHOLDS = {'HIGH': 12, 'MEDIUM': 9, 'LOW': 4}
DAMAGE_THRESHOLDS = {'HIGH': 35, 'MEDIUM': 15, 'LOW': 0}

# ARC conversion rates (Figure 16)
SHELTER_RATES = {'HIGH': 0.05, 'MEDIUM': 0.03, 'LOW': 0.01}
FEEDING_RATES = {'HIGH': 0.12, 'MEDIUM': 0.07, 'LOW': 0.03}

AVG_HOUSEHOLD_SIZE = 2.53  # US Census Bureau
```

3. **Cell 3-4**: Data loading — accept building-level FAST output CSV (not pre-aggregated county data)
4. **Cell 5**: Building classification function:

```python
def classify_building(row):
    surge = row.get('Depth_Grid', 0) or 0
    dmg = row.get('BldgDmgPct', 0) or 0
    if surge > SURGE_THRESHOLDS['HIGH']:   return 'HIGH'
    if surge >= SURGE_THRESHOLDS['MEDIUM']: return 'MEDIUM'
    if surge >= SURGE_THRESHOLDS['LOW']:    return 'LOW'
    if dmg > DAMAGE_THRESHOLDS['HIGH']:    return 'HIGH'
    if dmg > DAMAGE_THRESHOLDS['MEDIUM']:  return 'MEDIUM'
    if dmg > DAMAGE_THRESHOLDS['LOW']:     return 'LOW'
    return 'NONE'
```

5. **Cell 6**: County aggregation + pivot to wide format
6. **Cell 7**: Apply ARC conversion rates
7. **Cell 8**: Summary dashboard (L/M/H breakdown)
8. **Cell 9**: Visualization — stacked bar chart by county (L/M/H colored)
9. **Cell 10**: CSV + Excel export

### Documentation References

- Existing notebook structure: `deploy_population_impact.ipynb` Cell 2, 6, 8
- New config constants: DIRECTION.md § 2.2, § 2.5
- Classification logic: DIRECTION.md § 3.3

### Verification Checklist

- [ ] Notebook runs end-to-end in Colab without errors
- [ ] Output CSV matches Planning Assumptions Spreadsheet format (columns J-R)
- [ ] Demo data produces non-zero L/M/H populations
- [ ] Export generates valid Excel file
- [ ] Model card updated to reflect L/M/H approach (not shelter_pop prediction)

---

## Phase 5: Final Verification

### Cross-Phase Checks

1. **Schema consistency**: Grep all output files for column name consistency
   ```bash
   grep -r "pop_affected" research/population_impact/scripts/ research/population_impact/notebooks/
   grep -r "pop_impacted" research/population_impact/scripts/ research/population_impact/notebooks/
   grep -r "hh_shelter" research/population_impact/scripts/ research/population_impact/notebooks/
   ```

2. **Anti-pattern scan**: Ensure no ML model training in new pipeline
   ```bash
   grep -rn "GradientBoosting\|RandomForest\|XGBoost\|Ridge\|sklearn" research/population_impact/scripts/04_classify_lmh.py research/population_impact/scripts/05_format_for_spreadsheet.py
   # Expected: 0 matches
   ```

3. **Threshold consistency**: Verify same thresholds used in SQL, Python, and notebook
   ```bash
   grep -rn "12\|>= 9\|>= 4\|> 35\|> 15" research/population_impact/scripts/04_*.py research/population_impact/scripts/05_*.py research/population_impact/notebooks/deploy_population_impact.ipynb
   ```

4. **Output format check**: Verify CSV column order matches DIRECTION.md § 4.4
   ```bash
   head -1 research/population_impact/outputs/planning_assumptions_output.csv
   ```

5. **End-to-end smoke test**: Run Phase 1 → Phase 2 → verify output CSV has expected rows and columns

### Documentation Updates

- [ ] DIRECTION.md § 8 updated with actual results
- [ ] README.md reflects new pipeline
- [ ] CLAUDE.md reflects new pipeline (already done)
- [ ] Colab notebook model card updated

### Deliverables Checklist

| Deliverable | Format | Path |
|-------------|--------|------|
| County L/M/H population estimates | CSV | `outputs/planning_assumptions_output.csv` |
| Validation report | Markdown | `outputs/lmh_validation_report.md` |
| Threshold sensitivity analysis | Table in report | (embedded) |
| Colab deployment notebook | .ipynb | `notebooks/deploy_population_impact.ipynb` |
| Excel template | .xlsx | `outputs/arc_planning_template.xlsx` |
| Classification SQL | .py (embedded) | `scripts/04_classify_lmh.py` |

---

## Dependency Graph

```
Phase 0 (this document)
    ↓
Phase 1: Athena SQL + L/M/H classification
    ↓ produces: county_lmh_features.csv
Phase 2: Census join + spreadsheet formatting
    ↓ produces: planning_assumptions_output.csv + .xlsx
Phase 3: Validation against GT
    ↓ produces: lmh_validation_report.md
Phase 4: Updated Colab notebook (can run in parallel with Phase 3)
    ↓ produces: updated deploy_population_impact.ipynb
Phase 5: Final verification (after all above)
```
