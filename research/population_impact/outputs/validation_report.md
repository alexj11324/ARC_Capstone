# ARC Population Impact Model — Validation Report

> CMU Heinz MSPPM 2026 Capstone | American Red Cross
> Generated: 2026-03-10

## Executive Summary

We built a three-tier model to predict county-level shelter population needs following hurricane storm surge events. Using 3.5M building-level FAST damage predictions aggregated to 386 county-event rows, joined with ARC Ground Truth data for 131 county-event records, we achieved **56 matched observations** across 5 events.

**Key finding**: The median shelter rate is **0.73%** — approximately 1 in 137 displaced persons seeks ARC shelter. This rate varies by over 100× across events, driven primarily by the nature of flooding (surge vs. rainfall/inland) and local demographics.

**Recommendation**: Use the deterministic Tier 1 model with scenario multipliers for planning. The ML calibration (Tier 2) did not improve over the baseline given current data constraints.

## 1. Data Pipeline

```
FAST predictions (3.5M buildings, Athena)
    → Spatial join with county boundaries
    → 386 county-event damage aggregates (9 events)
    → Join with Ground Truth (131 county-events)
    → 56 matched rows (5 events)
```

### 1.1 Data Coverage Gap

| Event | GT Counties | FAST Counties | Matched | Status |
|-------|------------|---------------|---------|--------|
| BERYL_2024 | 6 | 24 | 5 | Partial coverage |
| DEBBY_2024 | 4 | 67 | 0 | **No overlap** |
| FLORENCE_2018 | 20 | 67 | 9 | Partial |
| HELENE_2024 | 30 | 42 | 16 | Good coverage |
| IAN_2022 | 17 | 50 | 0 | **No overlap** |
| IDALIA_2023 | 14 | 40 | 0 | **No overlap** |
| IDA_2021 | 20 | 43 | 17 | Good coverage |
| MICHAEL_2018 | 9 | 3 | 0 | **No overlap** |
| MILTON_2024 | 11 | 50 | 9 | Partial |

**Root cause of 0-overlap events**: The FAST surge predictions were generated from rasters that covered different geographic areas than where ARC operated shelters. For example, Ian's FAST predictions cover northeast Florida (Clay, Duval, Flagler counties) while GT shelters were in southwest Florida (Charlotte, Collier, Lee counties — the actual landfall area).

## 2. Tier 1: Deterministic Displacement Estimate

**Method**: Residential buildings with >15% damage (FEMA displacement criterion) × average household size (2.53 persons) × calibrated shelter rate.

| Parameter | Value | Source |
|-----------|-------|--------|
| Displacement threshold | 15% building damage | FEMA standard |
| Avg household size | 2.53 | US Census Bureau |
| Shelter rate (median) | 0.73% | 55 observations |

**Tier 1 Performance**:
- RMSE: 546.6
- MAE: 315.9

### Shelter Rate Distribution

The observed shelter rate (shelter_pop / displaced_pop_estimate) is extremely heavy-tailed:

- Minimum: 0.005% (Galveston/Beryl — massive surge but few seek shelter)
- Median: 0.73%
- Mean: 15.1% (skewed by Florence outliers)
- Maximum: 242% (Florence/New Hanover — more sheltered than estimated displaced)

**Interpretation**: Florence-type events produce inland flooding far beyond the surge damage model's estimate, leading to shelter rates >100%. For surge-dominated events (Beryl, Ida, Milton), rates are <1%.

## 3. Tier 2: ML Calibration (LOEO-CV)

**Method**: Ensemble of Gradient Boosting + Random Forest + Ridge regression on 12 log-scaled and ratio features, with Leave-One-Event-Out cross-validation.

**Features used**:
- Log-scaled: displaced population, building count, economic loss, county population
- Rates: damage_rate, severe_rate, displacement_rate
- Raw: avg_damage_pct, max_damage_pct
- Demographic: pct_elderly, median_household_income

### Overall LOEO-CV Results

| Metric | Value | Interpretation |
|--------|-------|---------------|
| RMSE | 407.6 | Better than Tier 1 (546.6) |
| MAE | 225.8 | Better than Tier 1 (315.9) |
| R² | -0.308 | Worse than mean prediction |
| Log RMSE | 1.88 | ~6.5× multiplicative error |
| MAPE | 397% | Dominated by small-value counties |

### Per-Event Fold Results

| Event | N | RMSE | MAE | logRMSE | MAPE | R² |
|-------|---|------|-----|---------|------|----|
| BERYL_2024 | 5 | 110 | 78 | 1.68 | 944% | 0.148 |
| HELENE_2024 | 16 | 152 | 114 | 1.80 | 809% | -21.974 |
| IDA_2021 | 17 | 209 | 145 | 1.37 | 117% | -0.520 |
| MILTON_2024 | 9 | 285 | 202 | 1.54 | 199% | -0.357 |
| FLORENCE_2018 | 9 | 907 | 682 | 2.97 | 85% | -1.282 |

**Analysis**: The negative R² indicates the model is less accurate than simply predicting the mean shelter population for each county. This is expected given:
1. Only 56 training rows across 5 events
2. Displacement proxy from building counts poorly correlates with actual shelter needs
3. Event-type heterogeneity (surge vs. rainfall flooding) creates bimodal distributions

## 4. Tier 3: EVT Uncertainty Quantification

**Method**: Generalized Pareto Distribution (GPD) fit to the upper tail of observed shelter rates.

| Parameter | Value |
|-----------|-------|
| GPD shape (ξ) | 1.3504 |
| GPD scale (σ) | 0.1369 |
| Threshold (u) | 3.54% |
| Exceedances | 11 |

**Shelter Rate Quantiles**:
- 90th percentile: 220.6% (Florence-type events)
- 95th percentile: 572.7%
- 99th percentile: 5083.7%

**Caveat**: The GPD shape parameter ξ > 1 indicates an infinite-mean distribution, meaning the tail is extremely heavy. The 95th and 99th percentile rates are not physically meaningful — they exceed the county population cap. For practical planning, we recommend fixed scenario multipliers:

| Scenario | Multiplier | Rationale |
|----------|-----------|-----------|
| Point estimate | 1× | Median historical shelter rate |
| Moderate | 3× | ~75th percentile of observations |
| Severe | 10× | Comparable to Florence-type inland flooding |
| Worst case | 30× | Extreme sustained flooding, conservative cap |

## 5. Stress Test Results

| Check | Severity | Violations |
|-------|----------|------------|
| all_passed | OK | 0 |

## 6. Sample Predictions vs Ground Truth

| Event | County | GT Shelter | Tier 1 | Tier 2 | Displaced Est. |
|-------|--------|-----------|--------|--------|----------------|
| FLORENCE_2018 | 37129 (New Hanover) | 1762 | 5 | 25 | 729 |
| FLORENCE_2018 | 37133 (Onslow) | 1549 | 15 | 29 | 1,991 |
| FLORENCE_2018 | 37019 (Brunswick) | 1047 | 10 | 17 | 1,440 |
| MILTON_2024 | 12057 (Hillsborough) | 726 | 352 | 114 | 48,290 |
| IDA_2021 | 22109 (Terrebonne) | 509 | 309 | 73 | 42,448 |
| IDA_2021 | 22051 (Jefferson) | 448 | 2,017 | 83 | 276,886 |
| MILTON_2024 | 12115 (Sarasota) | 432 | 351 | 57 | 48,161 |
| IDA_2021 | 22033 (E. Baton Rouge) | 422 | 57 | 218 | 7,782 |
| BERYL_2024 | 48201 (Harris) | 307 | 125 | 279 | 17,121 |
| HELENE_2024 | 12103 (Pinellas) | 117 | 1,423 | 365 | 195,402 |

## 7. Architecture Comparison: Gemini Proposal vs Implementation

| Aspect | Gemini Proposal | Actual Implementation |
|--------|----------------|----------------------|
| Core approach | Geo-EVT-VAE hybrid | Deterministic aggregation + calibration |
| Data augmentation | GAN/VAE to generate synthetic events | Not needed — rich building-level data upstream |
| ML complexity | Neural network GPD estimation | Direct MLE GPD fit |
| Training data | 108 records | 56 matched county-events |
| Validation | LOEO-CV (correct) | LOEO-CV (confirmed) |
| Physical constraints | PINN-style loss function | Post-processing caps |
| Rate conversion | Absolute → rate (correct) | Shelter rate = shelter/displaced (confirmed) |

**What Gemini got right**: Rate conversion, LOEO-CV, physical constraints, Colab deployment.
**What Gemini got wrong**: The problem is 90% data engineering and 10% statistics. GANs/VAEs on ~100 records is guaranteed to overfit. The bottleneck is data coverage, not model complexity.

## 8. Recommendations

1. **Re-run FAST for missing events**: Ian, Debby, Idalia, Michael need FAST predictions generated from advisory-specific rasters covering their actual landfall areas
2. **Add rainfall displacement**: Storm surge is only one flooding mechanism. Integrating NWS rainfall data would capture Florence-type inland flooding
3. **Expand NSI population data**: The current proxy (building count × 2.53) is crude. If NSI population fields (pop2pmo65, pop2pmu65) can be recovered or re-downloaded, use them directly
4. **Event-type stratification**: Separately calibrate shelter rates for surge-dominated vs. rainfall-dominated events
5. **Use the severe scenario (10× multiplier) for initial planning**: It captures Florence-type events without the instability of raw GPD tail estimates
