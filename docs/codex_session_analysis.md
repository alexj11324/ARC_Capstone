# Codex Session Analysis — Feb 23, 2026 (13:51–20:26)

**Model**: gpt-5.3-codex | **Duration**: 6h 34m | **Branch**: `codex/fast-data-variable-gap`

---

## Session Outcome

Pipeline ran end-to-end technically (32/32 FAST sub-tasks succeeded), but output is **business-invalid**: 99.73% of output rows have zero loss due to spatial mismatch between NSI data and raster coverage.

---

## Critical Failure: Spatial Mismatch (Root Cause)

### What happened
- Raster used: `BERYL_2024_adv41_e10_ResultMaskRaster.tif`
- Raster bbox (WGS84): lon `[-98.54, -93.55]`, lat `[25.82, 30.88]` — covers Gulf Coast Texas/Louisiana only
- NSI input: 31,798,262 rows across 16 states (entire Southeast US)
- Only **3,801 / 1,418,177** output rows (0.268%) fell within raster bbox
- Only **1 row** had positive depth/loss (Louisiana | CoastalA)

### Root cause in code
The `impact-only` / bbox pre-filter in `fast_e2e_from_oracle.py` only filtered rows where `firmzone` was empty/unknown. Rows with valid FEMA flood zone codes (A, AE, V, VE, etc.) bypassed the bbox filter entirely and entered FAST even when geographically outside the raster footprint. FAST then sampled zero depth for those buildings, producing zero-loss records.

### Fix needed
All rows must pass a spatial bbox check against the raster before entering FAST — firmzone value must not bypass this filter.

---

## Data Volume & Filtering

| Stage | Count |
|-------|-------|
| NSI input rows | 31,798,262 |
| Written to FAST input | 1,418,177 (4.46%) |
| Discarded reason | `unknown_firmzone_outside_bbox` (30,380,085 rows) |
| Within raster bbox | 3,801 (0.268% of output) |
| Rows with positive loss | 1 |

Discard label `unknown_firmzone_outside_bbox` is misleading — it conflates two distinct rejection reasons (unknown firmzone vs. outside bbox) into one label, making it impossible to diagnose which filter caused each rejection.

---

## FltyId Duplication

- Total output rows: 1,418,177
- Unique FltyId: 1,346,881
- Duplicate rows: **71,296** (5.02%)
- Pattern: duplicates occur within same state + same flC bucket (not cross-state/cross-flC)
- Likely cause: NSI source data contains duplicate structure records, or the firmzone-based splitting assigns the same building to multiple flC buckets when a building has ambiguous firmzone codes

---

## Zero-Loss Output Pattern

- flC distribution: CoastalA = 1,382,512 rows, CoastalV = 35,665 rows, Riverine = 0 rows
- Riverine = 0 is expected for a coastal storm surge event (Beryl)
- CoastalA dominates because most NSI A-zone buildings are classified there
- Near-zero loss is entirely explained by spatial mismatch, not by FAST computation errors

---

## Other Failures Encountered

| Failure | Impact | Fix Applied |
|---------|--------|-------------|
| OCI `bulk-upload` stuck (1 obj / 20 min) | Blocked data transfer | Switched to file-by-file loop with checksum |
| `oci os object get --force` not supported | Blocked e2e download stage | Pre-delete temp file before download |
| `osgeo`/GDAL missing locally | FAST cannot run on local machine | Deferred to cloud; bootstrap installs GDAL via conda |
| `hazpy` not on conda channel | Bootstrap failure | pip fallback to `hazpy==0.0.4` |
| Lookuptables case mismatch (Linux) | Fatal on Linux | Case-insensitive directory detection added |
| CSV/Parquet engine split | Result inconsistency risk | Unified to single local Python engine |

---

## Successful Deliverables

1. **`run_fast.py`** — headless CLI for FAST (no GUI/Windows required)
2. **`hazus_notinuse.py`** — `local_with_options()` API with explicit field map, raster paths, flC, output dir
3. **`fast_e2e_from_oracle.py`** — full orchestration: Oracle discovery → NSI streaming → firmzone flC split → parallel FAST → merge
4. **`bootstrap_cloud_fast_env.sh`** — cloud env setup (Miniconda + GDAL + rasterio + pyarrow + hazpy)
5. **`configs/fast_e2e.yaml`** — default pipeline config
6. **`AGENTS.md`** — project-wide agent rules (flC strategy, field mapping, prohibited questions)
7. **`docs/FAST_REQUIRED_DATA_VS_OUR_DATA_ZH.md`** — FAST field requirements vs NSI/SLOSH availability

---

## Key Technical Decisions Made

- **flC auto-assignment**: firmzone-based split — VE/V → CoastalV, A*/AE/AH/AO → CoastalA, others → Riverine
- **CoastalA as default** for storm surge scenarios (not Riverine)
- **flC is run-level**, not row-level — requires separate FAST invocation per flC group
- **Stream processing** via `iter_batches` for 29M+ row NSI (avoids memory overflow)
- **State-level parallelism** with max_workers=4
- **Oracle Always Free** as compute platform (not EC2)

---

## Unimplemented Recommendations (from session end)

1. **Fix bbox filter**: Apply raster bbox check to ALL rows regardless of firmzone value
2. **Event-scoped state selection**: For Beryl raster (Texas/Louisiana bbox), only process those states — not all 16
3. These two changes together would reduce input from 31.8M to ~tens of thousands of rows and produce meaningful loss estimates

---

## Output Artifacts

- Merged predictions CSV: `exports/fast_e2e_20260223_230434/final/predictions_BERYL_2024_adv41_e10_ResultMaskRaster_20260223_230434.csv` (465MB, 1,418,177 rows)
- Run manifest: `exports/fast_e2e_20260223_230434/reports/run_manifest.json`
- Data quality report: `exports/fast_e2e_20260223_230434/reports/data_quality_report.json`
- Raster bbox: `exports/fast_e2e_20260223_230434/reports/raster_bbox.json`
- flC assignment report: `exports/fast_e2e_20260223_230434/reports/flc_assignment_report.json`
