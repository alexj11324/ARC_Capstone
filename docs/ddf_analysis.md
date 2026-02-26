# Depth-Damage Function (DDF) Analysis

## Overview

FAST uses HAZUS 4.0 deterministic depth-damage functions (DDFs) to map flood depth (feet) to structural/content damage ratios (%). Each DDF is a piecewise-linear curve indexed by depth from -4ft to +24ft relative to first-floor height.

## DDF Lookup Table Structure

Three LUT files, one per flood loss category (FLC):
- `Building_DDF_CoastalA_LUT_Hazus4p0.csv` — Coastal A zone
- `Building_DDF_CoastalV_LUT_Hazus4p0.csv` — Coastal V zone (wave action)
- `Building_DDF_Riverine_LUT_Hazus4p0.csv` — Riverine/inland flooding

Columns: `m4,m3,m2,m1,p0,p1,...,p24` = damage % at depths -4ft through +24ft.

Each row is keyed by `(Occupancy, SpecificOccupId, Stories, Basement)`.

## Key Findings

### 1. Slab/No-Basement Coastal Curves Are Extremely Steep

RES1 1-story slab (R11N) in CoastalA/V:
| Depth (ft) | 0 | 1 | 2 | 3 | 4+ |
|---|---|---|---|---|---|
| Damage % | 12 | 25 | 50 | 75 | 100 |

This BCAR-sourced curve reaches 100% damage at just 4ft of flooding. Compare with the Riverine equivalent (USACE-IWR sourced R11N): 13% at 0ft, 23% at 1ft, 32% at 2ft — much more gradual.

**Implication**: Coastal predictions are highly sensitive to small depth errors. A 1ft depth error at 2ft flooding changes damage from 50% to 75% (CoastalA) vs 32% to 40% (Riverine).

### 2. Basement vs No-Basement Creates Large Divergence

RES1 2-story at 0ft depth:
- No basement (R12N): 11% damage
- With basement (R12B): 19% damage

At -2ft (below first floor):
- No basement: 0%
- With basement: 8%

**Implication**: Foundation type classification accuracy directly impacts predictions. Misclassifying a basement home as slab (or vice versa) shifts damage by 8-20 percentage points.

### 3. Manufactured Housing (RES2) Is Most Vulnerable

RES2 (mobile homes) in CoastalA/V:
- At -2ft: already 15% damage
- At -1ft: 75% damage
- At 0ft: 100% total loss

In Riverine: 0ft = 11%, 1ft = 44%, 2ft = 63%.

**Implication**: RES2 structures dominate loss totals in any surge event. Accurate identification of manufactured housing in NSI data is critical.

### 4. Multi-Family Residential Uses Shared Curves

RES3A through RES3F (duplex through 50+ unit) all share the same USACE-Chicago DDF curves:
- Grade (no basement): DDF_ID 204 — 15% at 0ft, plateaus ~60% at 24ft
- Sub-grade (basement): DDF_ID 205 — 12% at 0ft, plateaus ~70% at 24ft

**Implication**: FAST does not differentiate damage between a duplex and a 50-unit apartment building per unit. Damage ratio is identical; only total cost differs via `Cost` field.

### 5. Commercial/Industrial DDFs Are Less Granular

Non-residential types (COM1-COM10, IND1-IND6, etc.) use Low/Mid/High Rise story bins rather than exact story counts. All share a smaller set of DDF curves from USACE-Chicago.

### 6. Hazard Applicability Flags

Each DDF row has `HazardRiverine`, `HazardCV`, `HazardCA` flags (0/1). Some curves are shared across hazard types (e.g., USACE-Chicago apartment curves apply to all three), while others are hazard-specific (e.g., BCAR slab curves only apply to CoastalA/V).

## Uncertainty Sources (Ranked by Impact)

1. **Flood depth accuracy** — DDFs are steep; ±1ft error = 10-25% damage shift for residential
2. **Foundation type misclassification** — NSI `found_type` has many text variants; mapping errors shift damage 8-20%
3. **Occupancy type accuracy** — RES2 vs RES1 misclassification changes damage by 50+ percentage points at low depths
4. **First-floor height** — Shifts the entire DDF curve; 1ft error in `found_ht` equivalent to 1ft depth error
5. **Coastal vs Riverine assignment** — BCAR coastal curves are much steeper than USACE riverine curves for the same building

## ML Enhancement Opportunities

The deterministic DDFs have known limitations:
- No uncertainty quantification (single point estimate per depth)
- No building age, material, or condition factors
- Same curve for all buildings of a given type regardless of local construction practices
- No wave action modeling in CoastalA (only CoastalV accounts for waves)

A gradient-boosted model trained on FAST DDF outputs could:
- Learn non-linear interactions between features (e.g., foundation × stories × depth)
- Provide prediction intervals via quantile regression
- Incorporate additional NSI fields not used by FAST (e.g., year built, construction class)

See `scripts/ml_damage_model.py` for the research prototype.
