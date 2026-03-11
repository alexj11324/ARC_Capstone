#!/usr/bin/env python3
"""Step 3: Build feature matrix, train shelter prediction model, and validate.

Joins county-level damage aggregates with Ground Truth and Census/SVI data.
Trains a three-tier prediction model:
  Tier 1: Deterministic FEMA displacement estimate
  Tier 2: XGBoost calibration with LOEO-CV
  Tier 3: EVT (GPD) uncertainty quantification

Output: predictions, LOEO-CV report, model artifacts.

Usage:
    python 03_build_and_train.py \
        --features data/county_event_features.csv \
        --ground-truth "../../Ground Truth Data.xlsx" \
        --census data/census_county_population.csv \
        --svi data/cdc_svi_by_county.csv \
        --output-dir outputs
"""

from __future__ import annotations

import argparse
import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)

# Event name mapping (Ground Truth uses short names, predictions use EVENT_YEAR)
GT_EVENT_MAP = {
    "Beryl": "BERYL_2024",
    "Debby": "DEBBY_2024",
    "Florence": "FLORENCE_2018",
    "Helene": "HELENE_2024",
    "Ian": "IAN_2022",
    "Idalia": "IDALIA_2023",
    "Ida": "IDA_2021",
    "Michael": "MICHAEL_2018",
    "Milton": "MILTON_2024",
}

STATE_ABBR = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY",
}

# FEMA displacement threshold (buildings with > this % damage → residents displaced)
DISPLACEMENT_THRESHOLD = 15.0  # percent
# Maximum realistic shelter rate (shelter / county_pop)
MAX_SHELTER_RATE = 0.15


def log(msg: str) -> None:
    print(f"[train] {msg}", flush=True)


# ─────────────────────────── Data Loading ───────────────────────────

def load_ground_truth(path: Path) -> pd.DataFrame:
    """Load and normalize Ground Truth Excel."""
    raw = pd.read_excel(path)
    df = pd.DataFrame()
    df["event_key"] = raw["Event"].map(GT_EVENT_MAP)
    df["county_fips5"] = (
        raw["State FIPS"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(2)
        + raw["County FIPS"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(3)
    )
    df["county_name"] = raw["County"]
    df["state_name"] = raw["State"]
    df["planned_shelter"] = pd.to_numeric(raw["Planned Shelter Population"], errors="coerce")
    df["actual_shelter"] = pd.to_numeric(raw["Actual Shelter Population"], errors="coerce")
    # Use actual if available, otherwise planned
    df["shelter_pop"] = df["actual_shelter"].fillna(df["planned_shelter"])
    df = df.dropna(subset=["event_key", "county_fips5"]).copy()
    log(f"Ground Truth: {len(df)} rows, {df['event_key'].nunique()} events")
    return df


def load_features(path: Path) -> pd.DataFrame:
    """Load county-level damage features from Athena export."""
    df = pd.read_csv(path)
    # Ensure county_fips5 is 5-char string (handle int, float, or string input)
    df["county_fips5"] = (
        df["county_fips5"]
        .astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.zfill(5)
    )
    # Convert numeric columns
    numeric_cols = [c for c in df.columns
                    if c not in ("event", "county_fips5", "state", "state_abbr", "county_name")]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    log(f"Features: {len(df)} rows, {df['event'].nunique()} events, "
        f"fips dtype={df['county_fips5'].dtype}, sample={df['county_fips5'].head(3).tolist()}")
    return df


def load_census(path: Path) -> pd.DataFrame:
    """Load Census population data."""
    df = pd.read_csv(path, dtype={"county_fips5": str})
    df["county_fips5"] = df["county_fips5"].str.zfill(5)
    log(f"Census: {len(df)} counties")
    return df


def load_svi(path: Path) -> pd.DataFrame:
    """Load CDC SVI data."""
    df = pd.read_csv(path, dtype={"county_fips5": str})
    df["county_fips5"] = df["county_fips5"].str.zfill(5)
    log(f"SVI: {len(df)} counties")
    return df


# ─────────────────────────── Feature Matrix ───────────────────────────

def build_feature_matrix(
    features: pd.DataFrame,
    ground_truth: pd.DataFrame,
    census: pd.DataFrame | None = None,
    svi: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Join all data sources into a single training matrix."""
    # Ensure both sides have string FIPS
    ground_truth = ground_truth.copy()
    features = features.copy()
    ground_truth["county_fips5"] = ground_truth["county_fips5"].astype(str).str.zfill(5)
    features["county_fips5"] = features["county_fips5"].astype(str).str.zfill(5)

    # Diagnostic: per-event match stats
    log("Join diagnostics:")
    for event in sorted(ground_truth["event_key"].dropna().unique()):
        gt_fips = set(ground_truth.loc[ground_truth["event_key"] == event, "county_fips5"])
        ft_fips = set(features.loc[features["event"] == event, "county_fips5"])
        overlap = gt_fips & ft_fips
        log(f"  {event}: GT={len(gt_fips)} counties, Features={len(ft_fips)} counties, "
            f"Match={len(overlap)}")

    # Merge features with ground truth
    merged = pd.merge(
        ground_truth,
        features,
        left_on=["event_key", "county_fips5"],
        right_on=["event", "county_fips5"],
        how="inner",
    )
    log(f"After GT-features inner join: {len(merged)} rows")

    if len(merged) == 0:
        log("ERROR: 0 matches. Falling back to left join for diagnostics...")
        log(f"  GT fips dtype={ground_truth['county_fips5'].dtype}, "
            f"Features fips dtype={features['county_fips5'].dtype}")
        log(f"  GT fips sample: {ground_truth['county_fips5'].head(3).tolist()}")
        log(f"  Features fips sample: {features['county_fips5'].head(3).tolist()}")
        merged = pd.merge(
            ground_truth, features,
            left_on=["event_key", "county_fips5"],
            right_on=["event", "county_fips5"],
            how="left",
        )
        log(f"  Left join: {merged['n_buildings'].notna().sum()} matched out of {len(merged)}")

    # Merge Census data
    if census is not None and not census.empty:
        merged = pd.merge(
            merged, census[["county_fips5", "total_population", "pct_elderly",
                           "median_household_income", "total_housing_units"]],
            on="county_fips5", how="left",
        )
        log(f"After Census join: {merged['total_population'].notna().sum()} matched")

    # Merge SVI data
    if svi is not None and not svi.empty:
        merged = pd.merge(
            merged, svi[["county_fips5", "svi_overall", "svi_socioeconomic",
                         "svi_household_disability"]],
            on="county_fips5", how="left",
        )
        log(f"After SVI join: {merged['svi_overall'].notna().sum()} matched")

    return merged


# ─────────────────────────── Tier 1: Deterministic ───────────────────────────

def tier1_deterministic(df: pd.DataFrame) -> pd.DataFrame:
    """Tier 1: FEMA-based deterministic displacement estimate.

    displaced_pop = sum of nighttime population in residential buildings
                    with damage > displacement threshold.
    shelter_estimate = displaced_pop × global_shelter_rate
    """
    df = df.copy()

    # Use the displaced_pop_night column from Athena aggregation if available
    disp_col = "displaced_pop_night"
    if disp_col in df.columns and df[disp_col].notna().any():
        df["displaced_pop_estimate"] = df[disp_col].fillna(0)
    else:
        # Fallback: estimate from residential displaced building counts
        # n_res_displaced = residential buildings with >15% damage (FEMA criterion)
        log("Using building count proxy for displaced population")
        avg_household = 2.53  # US Census average household size
        # Prefer n_res_displaced (residential displaced), fallback to n_displaced_bldgs
        if "n_res_displaced" in df.columns:
            df["displaced_pop_estimate"] = df["n_res_displaced"].fillna(0) * avg_household
            log(f"  Using n_res_displaced × {avg_household}")
        elif "n_displaced_bldgs" in df.columns:
            df["displaced_pop_estimate"] = df["n_displaced_bldgs"].fillna(0) * avg_household
            log(f"  Using n_displaced_bldgs × {avg_household}")
        else:
            log("  WARNING: No displacement column found, using n_damaged")
            df["displaced_pop_estimate"] = df.get("n_damaged", pd.Series(0)) * avg_household

    # Compute observed shelter rate from Ground Truth
    mask = (df["shelter_pop"].notna()) & (df["displaced_pop_estimate"] > 0)
    if mask.sum() > 0:
        observed_rates = df.loc[mask, "shelter_pop"] / df.loc[mask, "displaced_pop_estimate"]
        observed_rates = observed_rates.replace([np.inf, -np.inf], np.nan).dropna()
        observed_rates = observed_rates[observed_rates > 0]
        if len(observed_rates) > 0:
            global_shelter_rate = observed_rates.median()
        else:
            global_shelter_rate = 0.10  # FEMA default
    else:
        global_shelter_rate = 0.10

    log(f"Tier 1: global shelter rate = {global_shelter_rate:.4f} "
        f"(from {mask.sum()} observations)")

    df["tier1_shelter_estimate"] = (
        df["displaced_pop_estimate"] * global_shelter_rate
    ).clip(lower=0)

    # Apply population cap
    if "total_population" in df.columns:
        pop_cap = df["total_population"].fillna(1e6) * MAX_SHELTER_RATE
        df["tier1_shelter_estimate"] = df["tier1_shelter_estimate"].clip(upper=pop_cap)

    return df, global_shelter_rate


# ─────────────────────────── Tier 2: ML Calibration ───────────────────────────

FEATURE_COLS = [
    "displaced_pop_estimate",
    "n_buildings",
    "n_damaged",
    "n_displaced_bldgs",
    "n_severe",
    "n_residential",
    "n_res_displaced",
    "avg_damage_pct",
    "max_damage_pct",
    "total_bldg_loss_usd",
]

OPTIONAL_FEATURE_COLS = [
    "total_population",
    "pct_elderly",
    "median_household_income",
    "svi_overall",
    "svi_socioeconomic",
    "svi_household_disability",
    "avg_depth_ft",
    "max_depth_ft",
    "total_res_pop",
    "displaced_elderly",
]

TARGET_COL = "shelter_pop"


def _engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """Create derived features that better capture damage intensity patterns."""
    df = df.copy()
    # Damage rates (more predictive than raw counts)
    n_bldg = df["n_buildings"].fillna(1).clip(lower=1)
    df["damage_rate"] = df["n_damaged"].fillna(0) / n_bldg
    df["severe_rate"] = df["n_severe"].fillna(0) / n_bldg
    df["displacement_rate"] = df.get("n_res_displaced", pd.Series(0, index=df.index)).fillna(0) / n_bldg
    # Log-scaled features (handle the 3+ orders of magnitude range)
    df["log_displaced_pop"] = np.log1p(df["displaced_pop_estimate"].fillna(0))
    df["log_n_buildings"] = np.log1p(df["n_buildings"].fillna(0))
    df["log_bldg_loss"] = np.log1p(df["total_bldg_loss_usd"].fillna(0))
    df["log_total_pop"] = np.log1p(df.get("total_population", pd.Series(0, index=df.index)).fillna(0))
    # Interaction: damage intensity × county size
    df["damage_x_pop"] = df["avg_damage_pct"].fillna(0) * df["log_total_pop"]
    return df


TIER2_FEATURES = [
    # Log-scale features
    "log_displaced_pop", "log_n_buildings", "log_bldg_loss", "log_total_pop",
    # Rate features
    "damage_rate", "severe_rate", "displacement_rate",
    # Raw intensity features
    "avg_damage_pct", "max_damage_pct",
    # Interaction
    "damage_x_pop",
]

TIER2_OPTIONAL = [
    "pct_elderly", "svi_overall", "svi_socioeconomic",
    "median_household_income",
]


def tier2_loeo_cv(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Tier 2: Calibration with Leave-One-Event-Out CV.

    Predicts log(shelter_pop) using derived features. Ensemble of GBR + RF + Ridge.
    """
    try:
        from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
        from sklearn.linear_model import Ridge
        from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        log("WARNING: sklearn not available, skipping Tier 2")
        df["tier2_shelter_estimate"] = df.get("tier1_shelter_estimate", 0)
        return df, {"error": "sklearn not installed"}

    # Engineer features
    df = _engineer_features(df)

    # Determine available features
    available_features = [c for c in TIER2_FEATURES + TIER2_OPTIONAL if c in df.columns]
    log(f"Tier 2: using {len(available_features)} features: {available_features}")

    # Filter to rows with valid target
    valid = df[TARGET_COL].notna() & (df[TARGET_COL] > 0)
    train_df = df[valid].copy()

    if len(train_df) < 10:
        log(f"WARNING: Only {len(train_df)} valid training rows, Tier 2 may be unreliable")

    events = train_df["event_key"].unique()
    log(f"Tier 2: {len(train_df)} training rows across {len(events)} events")

    # Prepare features and log-transformed target
    X_raw = train_df[available_features].fillna(0).values
    y = train_df[TARGET_COL].values
    y_log = np.log1p(y)

    # LOEO-CV
    predictions = np.zeros(len(train_df))
    predictions_log = np.zeros(len(train_df))
    fold_metrics = []

    for event in events:
        test_mask = train_df["event_key"].values == event
        train_mask = ~test_mask

        if train_mask.sum() < 5:
            log(f"  Skipping event {event}: only {train_mask.sum()} training samples")
            predictions[test_mask] = train_df.loc[test_mask, "tier1_shelter_estimate"].values
            continue

        # Scale features per fold (no data leakage)
        scaler = StandardScaler()
        X_train = scaler.fit_transform(X_raw[train_mask])
        X_test = scaler.transform(X_raw[test_mask])
        y_train_log = y_log[train_mask]
        y_test = y[test_mask]

        # Train ensemble on log-scale target
        models = [
            ("gbr", GradientBoostingRegressor(
                n_estimators=200, max_depth=3, learning_rate=0.05,
                min_samples_leaf=5, subsample=0.8, random_state=42
            )),
            ("rf", RandomForestRegressor(
                n_estimators=200, max_depth=4, min_samples_leaf=5, random_state=42
            )),
            ("ridge", Ridge(alpha=100.0)),
        ]

        fold_preds = []
        for name, model in models:
            model.fit(X_train, y_train_log)
            pred_log = model.predict(X_test)
            pred = np.expm1(pred_log).clip(min=0)
            fold_preds.append(pred)

        # Average ensemble (in original scale)
        ensemble_pred = np.mean(fold_preds, axis=0).clip(min=0)
        predictions[test_mask] = ensemble_pred

        # Per-fold metrics
        rmse = float(np.sqrt(mean_squared_error(y_test, ensemble_pred)))
        mae = float(mean_absolute_error(y_test, ensemble_pred))
        # Log-scale metrics (more appropriate for heavy-tailed data)
        log_rmse = float(np.sqrt(mean_squared_error(np.log1p(y_test), np.log1p(ensemble_pred))))
        mape = float(np.mean(np.abs(y_test - ensemble_pred) / np.maximum(y_test, 1)) * 100)

        fold_metrics.append({
            "event": event,
            "n_test": int(test_mask.sum()),
            "rmse": rmse,
            "mae": mae,
            "log_rmse": log_rmse,
            "mape": mape,
            "r2": float(r2_score(y_test, ensemble_pred)) if test_mask.sum() > 1 else None,
        })
        log(f"  {event}: n={test_mask.sum()}, RMSE={rmse:.0f}, MAE={mae:.0f}, "
            f"logRMSE={log_rmse:.2f}, MAPE={mape:.0f}%")

    # Merge back
    df = df.copy()
    df["tier2_shelter_estimate"] = np.nan
    df.loc[valid, "tier2_shelter_estimate"] = predictions

    # For rows without GT, use Tier 1
    df["tier2_shelter_estimate"] = df["tier2_shelter_estimate"].fillna(
        df.get("tier1_shelter_estimate", 0)
    )

    # Overall metrics (both scales)
    overall_metrics = {
        "rmse": float(np.sqrt(mean_squared_error(y, predictions))),
        "mae": float(mean_absolute_error(y, predictions)),
        "r2": float(r2_score(y, predictions)),
        "log_rmse": float(np.sqrt(mean_squared_error(np.log1p(y), np.log1p(predictions)))),
        "mape": float(np.mean(np.abs(y - predictions) / np.maximum(y, 1)) * 100),
        "n_samples": int(len(y)),
        "n_events": int(len(events)),
        "fold_metrics": fold_metrics,
    }

    log(f"Tier 2 LOEO-CV overall: RMSE={overall_metrics['rmse']:.0f}, "
        f"MAE={overall_metrics['mae']:.0f}, R²={overall_metrics['r2']:.3f}, "
        f"logRMSE={overall_metrics['log_rmse']:.2f}, MAPE={overall_metrics['mape']:.0f}%")

    return df, overall_metrics


# ─────────────────────────── Tier 3: EVT ───────────────────────────

def tier3_evt(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Tier 3: GPD tail estimation for prediction intervals.

    Fits Generalized Pareto Distribution to the tail of shelter rates.
    """
    try:
        from scipy.stats import genpareto
    except ImportError:
        log("WARNING: scipy not available, skipping Tier 3")
        return df, {"error": "scipy not installed"}

    df = df.copy()

    # Compute shelter rates
    disp = df["displaced_pop_estimate"].fillna(0)
    shelter = df["shelter_pop"].fillna(0)
    mask = (disp > 10) & (shelter > 0)  # Need meaningful displaced population

    if mask.sum() < 10:
        log(f"WARNING: Only {mask.sum()} valid observations for EVT")
        df["tier3_p90"] = df.get("tier2_shelter_estimate", 0) * 1.5
        df["tier3_p95"] = df.get("tier2_shelter_estimate", 0) * 2.0
        return df, {"error": f"too few observations ({mask.sum()})"}

    rates = (shelter[mask] / disp[mask]).values
    rates = rates[np.isfinite(rates) & (rates > 0)]

    # POT threshold: 80th percentile of shelter rates
    threshold = np.percentile(rates, 80)
    exceedances = rates[rates > threshold] - threshold

    if len(exceedances) < 5:
        log(f"WARNING: Only {len(exceedances)} exceedances above threshold {threshold:.4f}")
        df["tier3_p90"] = df.get("tier2_shelter_estimate", 0) * 1.5
        df["tier3_p95"] = df.get("tier2_shelter_estimate", 0) * 2.0
        return df, {"threshold": float(threshold), "n_exceedances": len(exceedances),
                     "error": "too few exceedances"}

    # Fit GPD to exceedances
    try:
        xi, loc, sigma = genpareto.fit(exceedances, floc=0)
    except Exception as e:
        log(f"WARNING: GPD fit failed: {e}")
        df["tier3_p90"] = df.get("tier2_shelter_estimate", 0) * 1.5
        df["tier3_p95"] = df.get("tier2_shelter_estimate", 0) * 2.0
        return df, {"error": str(e)}

    log(f"Tier 3 EVT: GPD shape ξ={xi:.4f}, scale σ={sigma:.4f}, "
        f"threshold={threshold:.4f}, n_exceedances={len(exceedances)}")

    # Compute return level quantiles
    rate_p90 = threshold + genpareto.ppf(0.90, xi, loc=0, scale=sigma)
    rate_p95 = threshold + genpareto.ppf(0.95, xi, loc=0, scale=sigma)
    rate_p99 = threshold + genpareto.ppf(0.99, xi, loc=0, scale=sigma)

    log(f"  Rate quantiles: p90={rate_p90:.4f}, p95={rate_p95:.4f}, p99={rate_p99:.4f}")

    # Apply EVT confidence bounds to all predictions
    df["tier3_p90"] = (df["displaced_pop_estimate"].fillna(0) * rate_p90).clip(lower=0)
    df["tier3_p95"] = (df["displaced_pop_estimate"].fillna(0) * rate_p95).clip(lower=0)
    df["tier3_p99"] = (df["displaced_pop_estimate"].fillna(0) * rate_p99).clip(lower=0)

    # Population cap
    if "total_population" in df.columns:
        pop_cap = df["total_population"].fillna(1e6) * MAX_SHELTER_RATE
        for col in ["tier3_p90", "tier3_p95", "tier3_p99"]:
            df[col] = df[col].clip(upper=pop_cap)

    evt_metrics = {
        "gpd_xi": float(xi),
        "gpd_sigma": float(sigma),
        "threshold": float(threshold),
        "n_exceedances": int(len(exceedances)),
        "rate_p90": float(rate_p90),
        "rate_p95": float(rate_p95),
        "rate_p99": float(rate_p99),
    }

    return df, evt_metrics


# ─────────────────────────── Stress Tests ───────────────────────────

def run_stress_tests(df: pd.DataFrame) -> list[dict]:
    """Physical constraint checks and stress tests."""
    issues = []

    # Check 1: Shelter > county population
    if "total_population" in df.columns:
        for tier in ["tier1_shelter_estimate", "tier2_shelter_estimate"]:
            if tier in df.columns:
                violations = df[df[tier] > df["total_population"].fillna(1e9)]
                if len(violations) > 0:
                    issues.append({
                        "check": f"{tier} > county_population",
                        "severity": "CRITICAL",
                        "n_violations": len(violations),
                        "examples": violations[["event_key", "county_fips5", tier, "total_population"]].head(3).to_dict("records"),
                    })

    # Check 2: High shelter in low-damage counties
    for tier in ["tier1_shelter_estimate", "tier2_shelter_estimate"]:
        if tier in df.columns and "avg_damage_pct" in df.columns:
            violations = df[(df[tier] > 100) & (df["avg_damage_pct"] < 1)]
            if len(violations) > 0:
                issues.append({
                    "check": f"High shelter ({tier}) with <1% avg damage",
                    "severity": "WARNING",
                    "n_violations": len(violations),
                })

    # Check 3: SVI paradox (high shelter in low-vulnerability county)
    if "svi_overall" in df.columns:
        for tier in ["tier1_shelter_estimate", "tier2_shelter_estimate"]:
            if tier not in df.columns:
                continue
            p95 = df[tier].quantile(0.95) if df[tier].notna().any() else 0
            violations = df[(df[tier] > p95) & (df["svi_overall"] < 0.2)]
            if len(violations) > 0:
                issues.append({
                    "check": f"SVI paradox: high {tier} + low SVI",
                    "severity": "INFO",
                    "n_violations": len(violations),
                })

    if not issues:
        issues.append({"check": "all_passed", "severity": "OK", "n_violations": 0})

    return issues


# ─────────────────────────── Report Generation ───────────────────────────

def generate_report(
    df: pd.DataFrame,
    tier1_rate: float,
    tier2_metrics: dict,
    evt_metrics: dict,
    stress_issues: list[dict],
    output_dir: Path,
) -> None:
    """Generate markdown validation report."""
    lines = [
        "# ARC Population Impact Model — Validation Report",
        "",
        f"Generated: {pd.Timestamp.now().isoformat()}",
        "",
        "## Data Summary",
        "",
        f"- Training rows (with shelter data): {df[TARGET_COL].notna().sum()}",
        f"- Total county-event rows: {len(df)}",
        f"- Events: {sorted(df['event_key'].dropna().unique())}",
        "",
        "## Tier 1: Deterministic Displacement",
        "",
        f"- Global shelter rate (median): **{tier1_rate:.4f}**",
        f"- Interpretation: ~{tier1_rate*100:.1f}% of FEMA-displaced population seeks ARC shelter",
        "",
    ]

    # Tier 1 comparison table
    valid = df[TARGET_COL].notna()
    if valid.sum() > 0:
        t1_rmse = np.sqrt(((df.loc[valid, "tier1_shelter_estimate"] - df.loc[valid, TARGET_COL]) ** 2).mean())
        t1_mae = (df.loc[valid, "tier1_shelter_estimate"] - df.loc[valid, TARGET_COL]).abs().mean()
        lines.extend([
            f"- Tier 1 RMSE: {t1_rmse:.1f}",
            f"- Tier 1 MAE: {t1_mae:.1f}",
            "",
        ])

    # Tier 2
    lines.extend([
        "## Tier 2: LOEO-CV Calibration",
        "",
    ])
    if "error" in tier2_metrics:
        lines.append(f"- Error: {tier2_metrics['error']}")
    else:
        lines.extend([
            f"- Overall RMSE: **{tier2_metrics['rmse']:.1f}**",
            f"- Overall MAE: **{tier2_metrics['mae']:.1f}**",
            f"- Overall R²: **{tier2_metrics['r2']:.3f}**",
            f"- Samples: {tier2_metrics['n_samples']}, Events: {tier2_metrics['n_events']}",
            "",
            "### Per-Event Fold Results",
            "",
            "| Event | N | RMSE | MAE | logRMSE | MAPE | R² |",
            "|-------|---|------|-----|---------|------|----|",
        ])
        for fm in tier2_metrics.get("fold_metrics", []):
            r2_str = f"{fm['r2']:.3f}" if fm.get("r2") is not None else "N/A"
            log_rmse = f"{fm.get('log_rmse', 0):.2f}"
            mape = f"{fm.get('mape', 0):.0f}%"
            lines.append(
                f"| {fm['event']} | {fm['n_test']} | {fm['rmse']:.0f} | "
                f"{fm['mae']:.0f} | {log_rmse} | {mape} | {r2_str} |"
            )
    lines.append("")

    # Tier 3
    lines.extend([
        "## Tier 3: EVT Uncertainty Quantification",
        "",
    ])
    if "error" in evt_metrics:
        lines.append(f"- Note: {evt_metrics.get('error', 'N/A')}")
    else:
        lines.extend([
            f"- GPD shape (ξ): {evt_metrics['gpd_xi']:.4f}",
            f"- GPD scale (σ): {evt_metrics['gpd_sigma']:.4f}",
            f"- Threshold (u): {evt_metrics['threshold']:.4f}",
            f"- Exceedances: {evt_metrics['n_exceedances']}",
            "",
            "### Shelter Rate Quantiles (worst-case planning)",
            "",
            f"- 90th percentile rate: {evt_metrics['rate_p90']:.4f}",
            f"- 95th percentile rate: {evt_metrics['rate_p95']:.4f}",
            f"- 99th percentile rate: {evt_metrics['rate_p99']:.4f}",
        ])
    lines.append("")

    # Stress tests
    lines.extend([
        "## Stress Test Results",
        "",
        "| Check | Severity | Violations |",
        "|-------|----------|------------|",
    ])
    for issue in stress_issues:
        lines.append(f"| {issue['check']} | {issue['severity']} | {issue['n_violations']} |")
    lines.append("")

    # Comparison table: GT vs Predictions (sample)
    lines.extend([
        "## Sample Predictions vs Ground Truth",
        "",
        "| Event | County | GT Shelter | Tier 1 | Tier 2 | Displaced |",
        "|-------|--------|-----------|--------|--------|-----------|",
    ])
    sample = df[df[TARGET_COL].notna()].sort_values(TARGET_COL, ascending=False).head(20)
    for _, row in sample.iterrows():
        gt = f"{row[TARGET_COL]:.0f}" if pd.notna(row[TARGET_COL]) else "N/A"
        t1 = f"{row.get('tier1_shelter_estimate', 0):.0f}"
        t2 = f"{row.get('tier2_shelter_estimate', 0):.0f}"
        disp = f"{row.get('displaced_pop_estimate', 0):.0f}"
        lines.append(
            f"| {row.get('event_key', '')} | {row.get('county_fips5', '')} "
            f"| {gt} | {t1} | {t2} | {disp} |"
        )

    report_path = output_dir / "validation_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    log(f"Report written to {report_path}")


# ─────────────────────────── Main ───────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Build and train shelter prediction model")
    parser.add_argument("--features", required=True, help="County event features CSV")
    parser.add_argument("--ground-truth", required=True, help="Ground Truth Excel path")
    parser.add_argument("--census", default=None, help="Census population CSV")
    parser.add_argument("--svi", default=None, help="CDC SVI CSV")
    parser.add_argument("--output-dir", default="outputs", help="Output directory")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load data
    log("Loading data...")
    gt = load_ground_truth(Path(args.ground_truth))
    features = load_features(Path(args.features))
    census = load_census(Path(args.census)) if args.census else None
    svi = load_svi(Path(args.svi)) if args.svi else None

    # Build feature matrix
    log("Building feature matrix...")
    df = build_feature_matrix(features, gt, census, svi)
    df.to_csv(output_dir / "feature_matrix.csv", index=False)
    log(f"Feature matrix: {len(df)} rows → {output_dir / 'feature_matrix.csv'}")

    if len(df) == 0:
        log("ERROR: Feature matrix is empty. Check data joins.")
        return

    # Tier 1: Deterministic displacement
    log("\n=== Tier 1: Deterministic Displacement ===")
    df, tier1_rate = tier1_deterministic(df)

    # Tier 2: ML Calibration with LOEO-CV
    log("\n=== Tier 2: LOEO-CV Calibration ===")
    df, tier2_metrics = tier2_loeo_cv(df)

    # Tier 3: EVT
    log("\n=== Tier 3: EVT Uncertainty ===")
    df, evt_metrics = tier3_evt(df)

    # Stress tests
    log("\n=== Stress Tests ===")
    stress_issues = run_stress_tests(df)
    for issue in stress_issues:
        log(f"  [{issue['severity']}] {issue['check']}: {issue['n_violations']} violations")

    # Save predictions
    prediction_cols = [
        "event_key", "county_fips5", "county_name", "state_name",
        "shelter_pop", "planned_shelter", "actual_shelter",
        "displaced_pop_estimate",
        "tier1_shelter_estimate", "tier2_shelter_estimate",
    ]
    if "tier3_p90" in df.columns:
        prediction_cols.extend(["tier3_p90", "tier3_p95", "tier3_p99"])
    available_cols = [c for c in prediction_cols if c in df.columns]
    df[available_cols].to_csv(output_dir / "predictions.csv", index=False)

    # Save full dataset
    df.to_csv(output_dir / "full_results.csv", index=False)

    # Save metrics
    all_metrics = {
        "tier1_shelter_rate": float(tier1_rate),
        "tier2": tier2_metrics,
        "tier3_evt": evt_metrics,
        "stress_tests": stress_issues,
        "n_training_rows": int(df[TARGET_COL].notna().sum()),
        "n_total_rows": int(len(df)),
    }
    (output_dir / "metrics.json").write_text(
        json.dumps(all_metrics, indent=2, default=str), encoding="utf-8"
    )

    # Generate report
    generate_report(df, tier1_rate, tier2_metrics, evt_metrics, stress_issues, output_dir)

    log(f"\nDone. All outputs in {output_dir}/")


if __name__ == "__main__":
    main()
