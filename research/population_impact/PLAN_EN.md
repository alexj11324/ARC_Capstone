# ARC Disaster Population Impact Prediction — Implementation Plan

> CMU Heinz MSPPM 2026 Capstone | American Red Cross
> Date: 2026-03-10

## 1. Problem Diagnosis

### 1.1 Gemini Proposal Assessment

The Gemini Deep Think proposal suggests a **Geo-EVT-VAE hybrid architecture** combining GANs/VAEs with Extreme Value Theory. While intellectually sophisticated, it fundamentally misdiagnoses the problem:

**What Gemini gets right:**
- Convert absolute numbers to rates (shelter/population) — eliminates scale effects
- LOEO-CV validation — the only valid approach for event-clustered data
- Physical constraints (population cap, distance decay) — essential
- Colab deployment over xlwings — correct for ARC's IT constraints
- EVT for tail risk — useful for worst-case planning

**What Gemini gets wrong:**
- **Misframes an aggregation problem as a generation problem.** We already have 3.5M building-level damage predictions in Athena. The path from building damage → population displacement → shelter needs is a well-established FEMA methodology, not a generative modeling task.
- **GANs/VAEs on 108 records will overfit.** Mode collapse is guaranteed. The "data augmentation" rationale doesn't apply when we have rich building-level data upstream.
- **Neural network GPD parameter estimation is unnecessary.** With ~10-15 extreme observations, directly fitting GPD via MLE is both simpler and more statistically principled.
- **PINN-style loss functions add complexity without payoff.** Physical constraints are better enforced as deterministic post-processing rules.

### 1.2 Core Insight

The actual pipeline is:

```
FAST building damage (3.5M rows, in Athena)
    → aggregate to county level (engineering)
    → apply FEMA displacement functions (physics)
    → calibrate shelter rate with Ground Truth (statistics)
    → quantify uncertainty with EVT (optional enhancement)
```

This is **90% data engineering, 10% statistics**. Not a deep learning problem.

## 2. Three-Tier Architecture

### Tier 1: Deterministic Aggregation Pipeline (Core Deliverable)

**Verifiable, interpretable, defensible.**

Data flow:
```
arc_storm_surge.predictions (building-level)
    ↓ Spatial join with county boundaries
County-event damage aggregates (county-level)
    ↓ Join with NSI population fields
Displaced population estimate per county
    ↓ FEMA displacement function
Estimated Population Impacted
```

FEMA displacement criteria:
- Residential buildings (RES*) with `depth_in_struc > 0` → occupants displaced
- Or `bldgdmgpct > 15%` → functionally displaced (structure uninhabitable)
- Displaced population = sum of NSI night population for qualifying buildings

### Tier 2: Statistical Calibration Model

**Learns the conversion: displaced population → actual shelter population.**

Not everyone who is displaced goes to a Red Cross shelter. Many stay with family, in hotels, or self-evacuate. The `shelter_rate = actual_shelter / displaced_pop` varies by:
- Social vulnerability (SVI score)
- Disaster severity (damage intensity)
- County demographics (elderly, disabled populations)

Model: XGBoost regression with ~10 features, validated via LOEO-CV.
Baseline: global calibration factor (ratio of known shelter to displaced).

### Tier 3: EVT Uncertainty Quantification

**Provides prediction intervals for worst-case planning.**

Fit Generalized Pareto Distribution (GPD) to the tail of shelter rates.
Output: point estimate + 90th/95th/99th percentile confidence bounds.

## 3. Feature Engineering

### County-Level Feature Matrix

Each row = one (event, county) pair.

| Feature | Source | Description |
|---------|--------|-------------|
| `displaced_pop` | Tier 1 | FEMA-standard displaced population |
| `n_buildings_total` | FAST agg | Total buildings in county within event footprint |
| `n_buildings_damaged` | FAST agg | Buildings with damage > 0 |
| `n_buildings_severe` | FAST agg | Buildings with damage > 50% |
| `total_bldg_loss_usd` | FAST agg | Total economic loss |
| `avg_damage_pct` | FAST agg | Mean building damage |
| `max_depth_ft` | FAST agg | Maximum surge depth |
| `avg_depth_ft` | FAST agg | Mean surge depth |
| `county_pop` | Census API | Total county population |
| `svi_score` | CDC SVI | Social Vulnerability Index (0-1) |
| `pct_elderly` | NSI agg | Population 65+ ratio |
| `pct_disabled` | NSI agg | Disabled population ratio |

**Target variables (from Ground Truth):**
- `planned_shelter_population` — Red Cross pre-event estimate
- `actual_shelter_population` — Actual shelter occupancy

## 4. Validation Strategy

### 4.1 Leave-One-Event-Out CV

9 events → 9 folds. Each fold trains on 8 events, tests on 1.
Metrics: RMSE, MAE, R², MAPE, coverage of prediction intervals.

### 4.2 Physical Constraint Checks (Post-Processing Rules)

```
Hard constraints:
  shelter ∈ [0, county_population × 0.15]
  shelter = 0 if max_surge < 0.3 ft and max_damage_pct < 5%

Soft constraints (flag for review):
  Low SVI + high shelter rate → anomaly
  Low surge + high shelter → anomaly
  Adjacent county with higher surge but lower shelter → anomaly
```

### 4.3 Stress Tests

- Synthetic Cat-5 direct hit on Miami-Dade: verify prediction < population cap
- Zero-surge county: verify prediction ≈ 0
- Maximum historical surge × 1.5: verify reasonable extrapolation via EVT

## 5. Implementation Scripts

| Script | Purpose | Dependencies |
|--------|---------|-------------|
| `01_county_damage_agg.py` | Athena: aggregate FAST predictions to county level | boto3 |
| `02_nsi_pop_by_county.py` | Athena: aggregate NSI population by county | boto3 |
| `03_fetch_census_svi.py` | Fetch Census + SVI data | requests |
| `04_build_feature_matrix.py` | Join all data sources + Ground Truth | pandas |
| `05_displacement_model.py` | Tier 1: deterministic displacement estimate | numpy |
| `06_shelter_calibration.py` | Tier 2: XGBoost calibration + LOEO-CV | xgboost, sklearn |
| `07_evt_confidence.py` | Tier 3: GPD tail estimation | scipy |
| `08_validate.py` | Full validation suite + stress tests | all above |

## 6. Deliverables

| Deliverable | Format | Audience |
|-------------|--------|----------|
| County-level impact predictions (9 events) | CSV/Parquet | Technical team |
| LOEO-CV validation report | Markdown + charts | Capstone reviewers |
| Colab deployment notebook | .ipynb | ARC emergency responders |
| Excel impact template | .xlsx | ARC emergency responders |
| Model card (accuracy, limitations, usage) | Markdown | All stakeholders |
| Architecture comparison (Gemini vs actual) | Markdown | Capstone reviewers |

## 7. Compute Budget

| Resource | Est. Cost | Purpose |
|----------|-----------|---------|
| AWS Athena | ~$2-5 | Aggregation queries (scan-based billing) |
| GCP Compute | ~$5-10 | Model validation + Colab testing |
| Census API | Free | Population data |
| CDC SVI | Free | Vulnerability data download |
| **Total** | **< $15** | Under $20 budget |
