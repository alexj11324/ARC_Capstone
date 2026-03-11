#!/usr/bin/env python3
"""Step 1: Aggregate FAST building-level predictions to county level via Athena.

Joins predictions with NSI (for population + county FIPS from cbfips),
deduplicates across advisories, and aggregates per (event, county).

Output: county_event_features table in Athena + local CSV.

Usage:
    python 01_county_damage_agg.py [--output-dir ./data]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3

REGION = "us-east-1"
WORKGROUP = "primary"
ATHENA_OUTPUT = "s3://red-cross-capstone-project-data/analysis/pop-impact/athena-temp/"
S3_OUTPUT_PREFIX = "s3://red-cross-capstone-project-data/analysis/pop-impact/"
DATABASE = "arc_analysis"
PREDICTIONS_TABLE = "arc_storm_surge.predictions"
# Try multiple possible NSI table references
NSI_TABLE_CANDIDATES = [
    "red_cross_hurricane.nsi_data",
    "red_cross_hurricane.nsi_data_parquet",
]
COUNTY_TABLE = "arc_analysis.us_county_boundaries"


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


class AthenaClient:
    def __init__(self, region: str = REGION, workgroup: str = WORKGROUP):
        self.client = boto3.client("athena", region_name=region)
        self.workgroup = workgroup

    def execute(self, sql: str, database: str = DATABASE, label: str = "") -> str:
        """Execute query and return query execution ID."""
        log(f"  [{label}] Submitting query...")
        resp = self.client.start_query_execution(
            QueryString=sql,
            QueryExecutionContext={"Database": database},
            ResultConfiguration={"OutputLocation": ATHENA_OUTPUT},
            WorkGroup=self.workgroup,
        )
        qid = resp["QueryExecutionId"]
        while True:
            status = self.client.get_query_execution(QueryExecutionId=qid)
            state = status["QueryExecution"]["Status"]["State"]
            if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
                break
            time.sleep(2)
        if state != "SUCCEEDED":
            reason = status["QueryExecution"]["Status"].get("StateChangeReason", "")
            raise RuntimeError(f"Athena [{label}] failed ({qid}): {state} — {reason}")
        stats = status["QueryExecution"].get("Statistics", {})
        scanned_mb = stats.get("DataScannedInBytes", 0) / 1e6
        runtime_s = stats.get("EngineExecutionTimeInMillis", 0) / 1000
        log(f"  [{label}] OK — scanned {scanned_mb:.1f} MB in {runtime_s:.1f}s")
        return qid

    def fetch_rows(self, sql: str, database: str = DATABASE, label: str = "") -> list[dict]:
        qid = self.execute(sql, database, label)
        rows = []
        headers = []
        next_token = None
        first = True
        while True:
            kwargs = {"QueryExecutionId": qid, "MaxResults": 1000}
            if next_token:
                kwargs["NextToken"] = next_token
            resp = self.client.get_query_results(**kwargs)
            data = resp["ResultSet"]["Rows"]
            start = 0
            if first and data:
                headers = [c.get("VarCharValue", "") for c in data[0]["Data"]]
                start = 1
                first = False
            for row in data[start:]:
                vals = [c.get("VarCharValue", "") for c in row["Data"]]
                padded = vals + [""] * (len(headers) - len(vals))
                rows.append(dict(zip(headers, padded)))
            next_token = resp.get("NextToken")
            if not next_token:
                break
        return rows


def discover_nsi_table(athena: AthenaClient) -> str:
    """Find the correct NSI table name in Athena."""
    for candidate in NSI_TABLE_CANDIDATES:
        db, table = candidate.split(".", 1)
        try:
            rows = athena.fetch_rows(
                f"SELECT column_name FROM information_schema.columns "
                f"WHERE table_schema = '{db}' AND table_name = '{table}' LIMIT 5",
                database="default",
                label=f"probe_{candidate}",
            )
            if rows:
                log(f"  Found NSI table: {candidate}")
                return candidate
        except RuntimeError:
            continue
    raise RuntimeError(f"No NSI table found. Tried: {NSI_TABLE_CANDIDATES}")


def get_nsi_columns(athena: AthenaClient, nsi_table: str) -> set[str]:
    """Get available columns in NSI table."""
    db, table = nsi_table.split(".", 1)
    rows = athena.fetch_rows(
        f"SELECT column_name FROM information_schema.columns "
        f"WHERE table_schema = '{db}' AND table_name = '{table}'",
        database="default",
        label="nsi_columns",
    )
    cols = {r["column_name"].lower() for r in rows}
    log(f"  NSI columns ({len(cols)}): {sorted(cols)[:20]}...")
    return cols


def build_county_agg_query(nsi_table: str, nsi_cols: set[str], run_id: str) -> str:
    """Build the main county-level aggregation query.

    Strategy:
    1. Deduplicate predictions: take max damage per (event, fltyid) across advisories
    2. Join with NSI on fltyid=bid to get county FIPS (from cbfips) and population
    3. Aggregate to county level
    """
    # Determine available population columns
    has_pop = "pop2pmu65" in nsi_cols and "pop2pmo65" in nsi_cols
    has_disable = "o65disable" in nsi_cols and "u65disable" in nsi_cols
    has_cbfips = "cbfips" in nsi_cols

    if not has_cbfips:
        raise RuntimeError("NSI table missing cbfips column — cannot derive county FIPS")

    pop_night_expr = (
        "COALESCE(n.pop2pmo65, 0) + COALESCE(n.pop2pmu65, 0)"
        if has_pop else "0"
    )
    pop_day_expr = (
        "COALESCE(n.pop2amo65, 0) + COALESCE(n.pop2amu65, 0)"
        if "pop2amu65" in nsi_cols else pop_night_expr
    )
    pop_elderly_expr = "COALESCE(n.pop2pmo65, 0)" if has_pop else "0"

    output_loc = f"{S3_OUTPUT_PREFIX}{run_id}/county_event_features/"

    return f"""
CREATE TABLE {DATABASE}.county_event_features_{run_id}
WITH (
    format = 'PARQUET',
    external_location = '{output_loc}'
) AS
WITH
-- Step 1: Deduplicate predictions across advisories (take worst case per building)
deduped AS (
    SELECT
        event,
        fltyid,
        MAX(bldgdmgpct) AS bldgdmgpct,
        MAX(bldglossusd) AS bldglossusd,
        MAX(COALESCE(contentlossusd, 0)) AS contentlossusd,
        MAX(depth_grid) AS depth_grid,
        MAX(depth_in_struc) AS depth_in_struc,
        -- Keep first non-null values for categorical fields
        ARBITRARY(occ) AS occ,
        ARBITRARY(cost) AS cost,
        ARBITRARY(numstories) AS numstories,
        ARBITRARY(state) AS state,
        ARBITRARY(flc) AS flc,
        ARBITRARY(latitude) AS latitude,
        ARBITRARY(longitude) AS longitude
    FROM {PREDICTIONS_TABLE}
    GROUP BY event, fltyid
),
-- Step 2: Join with NSI for county FIPS and population
joined AS (
    SELECT
        d.*,
        SUBSTR(CAST(n.cbfips AS VARCHAR), 1, 5) AS county_fips5,
        CAST({pop_night_expr} AS DOUBLE) AS pop_night,
        CAST({pop_day_expr} AS DOUBLE) AS pop_day,
        CAST({pop_elderly_expr} AS DOUBLE) AS pop_elderly
    FROM deduped d
    LEFT JOIN {nsi_table} n
        ON CAST(d.fltyid AS VARCHAR) = CAST(n.bid AS VARCHAR)
)
-- Step 3: Aggregate to county level
SELECT
    event,
    county_fips5,
    ARBITRARY(state) AS state,

    -- Building counts
    COUNT(*) AS n_buildings,
    SUM(CASE WHEN bldgdmgpct > 0 THEN 1 ELSE 0 END) AS n_damaged,
    SUM(CASE WHEN bldgdmgpct > 15 THEN 1 ELSE 0 END) AS n_displaced_buildings,
    SUM(CASE WHEN bldgdmgpct > 50 THEN 1 ELSE 0 END) AS n_severe,
    SUM(CASE WHEN occ LIKE 'RES%' THEN 1 ELSE 0 END) AS n_residential,
    SUM(CASE WHEN occ LIKE 'RES%' AND bldgdmgpct > 0 THEN 1 ELSE 0 END) AS n_res_damaged,

    -- Damage metrics
    AVG(bldgdmgpct) AS avg_damage_pct,
    MAX(bldgdmgpct) AS max_damage_pct,
    APPROX_PERCENTILE(bldgdmgpct, 0.90) AS p90_damage_pct,
    SUM(bldglossusd) AS total_bldg_loss_usd,
    SUM(contentlossusd) AS total_content_loss_usd,

    -- Flood depth metrics
    AVG(CASE WHEN depth_grid > 0 THEN depth_grid END) AS avg_depth_ft,
    MAX(depth_grid) AS max_depth_ft,
    APPROX_PERCENTILE(depth_grid, 0.90) AS p90_depth_ft,

    -- Population estimates (FEMA displacement: residential + damage > 15%)
    SUM(CASE WHEN occ LIKE 'RES%' AND bldgdmgpct > 15
        THEN pop_night ELSE 0 END) AS displaced_pop_night,
    SUM(CASE WHEN occ LIKE 'RES%' AND bldgdmgpct > 15
        THEN pop_day ELSE 0 END) AS displaced_pop_day,
    SUM(CASE WHEN occ LIKE 'RES%' AND bldgdmgpct > 15
        THEN pop_elderly ELSE 0 END) AS displaced_elderly,

    -- Also compute displacement at different thresholds for sensitivity analysis
    SUM(CASE WHEN occ LIKE 'RES%' AND depth_in_struc > 0
        THEN pop_night ELSE 0 END) AS displaced_pop_inundated,
    SUM(CASE WHEN occ LIKE 'RES%' AND bldgdmgpct > 5
        THEN pop_night ELSE 0 END) AS displaced_pop_5pct,
    SUM(CASE WHEN occ LIKE 'RES%' AND bldgdmgpct > 30
        THEN pop_night ELSE 0 END) AS displaced_pop_30pct,

    -- Total residential population in event footprint
    SUM(CASE WHEN occ LIKE 'RES%' THEN pop_night ELSE 0 END) AS total_res_pop,
    -- Total population (all building types)
    SUM(pop_night) AS total_pop_footprint,

    -- Centroid for distance calculations
    AVG(latitude) AS centroid_lat,
    AVG(longitude) AS centroid_lon

FROM joined
WHERE county_fips5 IS NOT NULL
GROUP BY event, county_fips5
"""


def build_summary_query(run_id: str) -> str:
    """Summary statistics of the aggregation."""
    table = f"{DATABASE}.county_event_features_{run_id}"
    return f"""
SELECT
    event,
    COUNT(*) AS n_counties,
    SUM(n_buildings) AS total_buildings,
    SUM(n_damaged) AS total_damaged,
    SUM(displaced_pop_night) AS total_displaced,
    ROUND(SUM(total_bldg_loss_usd) / 1e6, 1) AS total_loss_millions,
    ROUND(AVG(avg_damage_pct), 2) AS mean_avg_damage,
    MAX(max_depth_ft) AS max_depth
FROM {table}
GROUP BY event
ORDER BY event
"""


def export_to_csv(rows: list[dict], output_path: Path) -> None:
    if not rows:
        log("  No rows to export")
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    log(f"  Exported {len(rows)} rows to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Aggregate FAST predictions to county level")
    parser.add_argument("--output-dir", default="data", help="Local output directory")
    parser.add_argument("--run-id", default=None, help="Run ID (default: timestamp)")
    parser.add_argument("--dry-run", action="store_true", help="Print SQL without executing")
    args = parser.parse_args()

    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    log(f"Run ID: {run_id}")

    athena = AthenaClient()

    # Discover NSI table
    log("Step 0: Discovering NSI table...")
    nsi_table = discover_nsi_table(athena)

    # Get NSI schema
    log("Step 0b: Reading NSI schema...")
    nsi_cols = get_nsi_columns(athena, nsi_table)

    # Build and run aggregation query
    log("Step 1: Building county aggregation query...")
    agg_sql = build_county_agg_query(nsi_table, nsi_cols, run_id)

    if args.dry_run:
        print("\n=== AGGREGATION QUERY ===")
        print(agg_sql)
        return

    # Drop existing table if any
    try:
        athena.execute(
            f"DROP TABLE IF EXISTS {DATABASE}.county_event_features_{run_id}",
            label="drop_existing",
        )
    except RuntimeError:
        pass

    athena.execute(agg_sql, label="county_aggregation")

    # Fetch and export summary
    log("Step 2: Fetching summary statistics...")
    summary_rows = athena.fetch_rows(
        build_summary_query(run_id),
        label="summary",
    )
    export_to_csv(summary_rows, output_dir / "county_agg_summary.csv")

    # Print summary
    print("\n=== County Aggregation Summary ===")
    for row in summary_rows:
        print(
            f"  {row['event']:20s}  counties={row['n_counties']:>4s}  "
            f"buildings={row['total_buildings']:>10s}  damaged={row['total_damaged']:>8s}  "
            f"displaced={row['total_displaced']:>8s}  loss=${row['total_loss_millions']}M"
        )

    # Fetch full feature data
    log("Step 3: Fetching full county feature data...")
    feature_rows = athena.fetch_rows(
        f"SELECT * FROM {DATABASE}.county_event_features_{run_id} ORDER BY event, county_fips5",
        label="fetch_features",
    )
    export_to_csv(feature_rows, output_dir / "county_event_features.csv")

    # Save metadata
    meta = {
        "run_id": run_id,
        "table": f"{DATABASE}.county_event_features_{run_id}",
        "nsi_table": nsi_table,
        "n_rows": len(feature_rows),
        "n_events": len(set(r.get("event", "") for r in feature_rows)),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (output_dir / "county_agg_metadata.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )

    log(f"Done. {len(feature_rows)} county-event rows exported to {output_dir}/")


if __name__ == "__main__":
    main()
