 ARC Capstone: Storm Surge Impact Prediction — Implementation Plan                                                                      
                                                                                                                                     
 Project Summary                                                          
                
 CMU Heinz MSPPM 2026 Capstone for American Red Cross. Goal: property-level storm surge/tsunami impact modeling using FEMA's FAST tool
 with NSI building inventory (30M+ structures) and SLOSH surge rasters.

 Critical Problem: The current pipeline produces 99.7% zero-loss outputs due to spatial mismatch — buildings are sent to FAST that fall
  entirely outside the flood raster's bounding box. The previous 6.5-hour Codex session and Reflection discussion identified this but
 did not fully resolve it.

 Root Cause Analysis

 1. Spatial mismatch: --state-scope all sends ALL 50 states to FAST, but the raster (e.g., Hurricane Beryl adv41) only covers a narrow
 coastal strip along the Gulf Coast. States like Washington, Maine, Rhode Island have zero overlap.
 2. BBox filtering is too coarse: The in_bbox() check in fast_e2e_from_oracle.py:571 uses the raster's rectangular bounding box, but
 hurricane surge rasters are narrow irregular shapes — the bbox still includes vast areas with no actual flood data.
 3. No event-state mapping: There's no mechanism to automatically select only the states actually affected by a given hurricane event.
 4. FltyId duplication: Multiple structures share the same bid within a state, causing FAST aggregation issues.

 Implementation Plan (12 Parallel Tasks)

 Phase 1: Critical Fixes (Tasks 1-4, parallel)

 Task 1: Smart State Selection via Raster Footprint
 - Read raster with rasterio, compute the actual non-nodata footprint (not just bbox)
 - Build a state→raster overlap index using the raster's valid-pixel convex hull
 - Auto-exclude states with zero overlap before any data processing
 - File: scripts/fast_e2e_from_oracle.py — modify main() and add compute_raster_footprint()

 Task 2: Raster-Aware Spatial Pre-Filter
 - Replace the simple in_bbox() check with actual raster value sampling at each building location
 - Use rasterio.sample() to check if the flood depth at (lon, lat) is > 0 before including in FAST CSV
 - This eliminates the 99.7% zero-loss problem at the source
 - File: scripts/fast_e2e_from_oracle.py — modify clean_state_to_fast_csv()

 Task 3: FltyId Deduplication
 - Add dedup logic: groupby(bid, state, flc) keeping the record with highest val_struct
 - Generate unique IDs via UUID = f"{bid}_{lat}_{lon}" when duplicates exist
 - File: scripts/fast_e2e_from_oracle.py — add deduplicate_buildings()

 Task 4: Event-State Mapping Config
 - Create configs/event_state_map.yaml with hurricane→affected-states mapping
 - Add --event CLI arg that auto-selects states from the mapping
 - Known mappings: Beryl 2024 → TX, LA, MS, AL, FL; etc.
 - Files: configs/event_state_map.yaml (new), scripts/fast_e2e_from_oracle.py

 Phase 2: Performance & Scale (Tasks 5-7, parallel)

 Task 5: H3 Hexagonal Pre-Indexing
 - Pre-compute H3 cell IDs (resolution 7) for the raster's valid flood area
 - Pre-compute H3 cell IDs for NSI building locations
 - Use set intersection for O(1) spatial filtering instead of per-point raster sampling
 - New file: scripts/h3_spatial_index.py

 Task 6: DuckDB Spatial Engine
 - Replace row-by-row Python processing with DuckDB spatial queries
 - Load parquet directly, apply spatial filter, export FAST CSV in one SQL pipeline
 - Target: process 30M rows in <60 seconds vs current hours
 - New file: scripts/duckdb_fast_pipeline.py

 Task 7: Parallel FAST Execution Optimization
 - Current max_workers=4 is conservative; profile and tune
 - Add progress reporting per state/flc combination
 - Add retry logic for individual FAST job failures
 - File: scripts/fast_e2e_from_oracle.py — modify main() thread pool section

 Phase 3: ML-Enhanced Prediction (Tasks 8-10, parallel)

 Task 8: Depth-Damage Function Analysis
 - Analyze FAST's lookup tables in FAST-main/Lookuptables/ to understand the DDF curves
 - Compare FAST's deterministic DDFs with ML-based alternatives from literature
 - Document which building types/occupancies have the most prediction uncertainty
 - Output: docs/ddf_analysis.md

 Task 9: ML Damage Prediction Model (Research Prototype)
 - Build a gradient-boosted model (XGBoost/LightGBM) using:
   - Features: occupancy type, foundation type, num_stories, first_floor_ht, building value, surge depth
   - Target: damage ratio (from FAST's DDF curves as training data)
 - Compare with FAST's deterministic output
 - New file: scripts/ml_damage_model.py

 Task 10: Surge Depth Interpolation from SLOSH
 - Current pipeline uses pre-made rasters; add ability to generate rasters from SLOSH parquet
 - Interpolate SLOSH polygon surge values to a regular grid
 - Support multiple hurricane categories (c1-c5) and scenarios (mean/high)
 - New file: scripts/slosh_to_raster.py

 Phase 4: Integration & Documentation (Tasks 11-12, parallel)

 Task 11: End-to-End Validation Pipeline
 - Create a validation script that runs the full pipeline on a known hurricane event
 - Compare outputs against FEMA's published damage estimates
 - Generate summary statistics: total damage by state, by occupancy type, by flood category
 - New file: scripts/validate_pipeline.py

 Task 12: Create CLAUDE.md and Update Documentation
 - Create comprehensive CLAUDE.md with project context, architecture, data contracts
 - Update README.md with current pipeline status
 - Document all new scripts and their usage
 - Files: CLAUDE.md (new), README.md

 Teammate Assignment Plan

 After plan approval, spawn these teammates:
 1. spatial-filter-dev → Tasks 1 + 2 (critical spatial fixes)
 2. dedup-dev → Task 3 (FltyId dedup)
 3. config-dev → Task 4 (event-state mapping)
 4. h3-indexer → Task 5 (H3 pre-indexing)
 5. duckdb-dev → Task 6 (DuckDB pipeline)
 6. fast-optimizer → Task 7 (parallel execution)
 7. ddf-analyst → Task 8 (DDF analysis)
 8. ml-modeler → Task 9 (ML damage model)
 9. raster-builder → Task 10 (SLOSH→raster)
 10. validator → Task 11 (validation pipeline)
 11. doc-writer → Task 12 (CLAUDE.md + docs)

 All teammates require plan approval before making changes.

 Key Research Insights (from Reflection.md + codex_chat.md)

 - H3 hexagonal indexing can reduce spatial filtering from O(n*m) to O(n) with pre-computed cell IDs
 - DuckDB spatial extension handles 30M+ rows efficiently on single machine
 - Apache Sedona is overkill for this scale; DuckDB is sufficient
 - STAC catalogs useful if managing multiple hurricane rasters
 - The Codex session confirmed the pipeline works end-to-end mechanically — the problem is purely data selection/filtering
 - FloodGenome (Texas A&M) and Smart Flood Resilience papers offer relevant ML approaches