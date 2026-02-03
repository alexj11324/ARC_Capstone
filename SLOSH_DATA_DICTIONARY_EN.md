# SLOSH Dataset Feature Dictionary (Parquet Version)

The SLOSH dataset in this project represents the NOAA **S**ea, **L**ake, and **O**verland **S**urges from **H**urricanes model results.

**⚠️ Important Concepts**:
1.  **NOT Time-Series**: This dataset is **static**. It does not represent the flood progression over time.
2.  **MOMs (Maximum of Maximums)**: This is a **risk assessment dataset**. Each value represents the **maximum potential water level** (worst-case snapshot) at that specific location, derived from thousands of simulated hurricane tracks and speeds for a given intensity category.
3.  **Elevation vs. Depth**: The values in the table represent the **Surge Elevation** (water surface height relative to datum), NOT the depth of water on the ground.

## 1. Core Calculation Formula

To assess the actual flood risk at a specific location, you MUST use the following logic:

> **Inundation Depth = Surge Elevation - Topography Height**

*   **If Result > 0**: The area is inundated. The result is the water depth.
*   **If Result <= 0 or Surge is NULL**: The area is NOT inundated (Dry).

---

## 2. Feature Definitions

### 2.1 Identification & Spatial Info

| Feature Name | Meaning | Type | Details |
| :--- | :--- | :--- | :--- |
| **basin** | Basin Code | STRING | **Partition Key**. Identifies the geographic region (e.g., `ny3mom` for NY, `ap3mom` for FL). |
| **poly_id** | Grid ID | STRING | Unique identifier for each grid cell. |
| **i_index** | Polar I | INT | Internal model grid coordinate I. |
| **j_index** | Polar J | INT | Internal model grid coordinate J. |
| **geometry_wkt** | **Polygon Geometry** | STRING (WKT) | Format: `POLYGON((...))`. Used for spatial joins (`ST_Contains`) to map specific coordinates to grid cells. |
| **topography** | **Topography** | INT | Average ground elevation of the grid cell (in Feet). Datum is typically NAVD88. |

### 2.2 Surge Elevation Estimates

These features represent the **maximum water surface elevation** (in Feet) for specific hurricane scenarios.
*   **Data Cleaning Note**: Raw values of `99.9` (indicating Dry/Not Inundated) have been converted to `NULL`.

| Feature | Hurricane Category | Tide Scenario | Business Meaning |
| :--- | :--- | :--- | :--- |
| **c0_mean** | Tropical Storm (TS) | Mean Tide | Max surge elevation for TS at mean tide. |
| **c0_high** | Tropical Storm (TS) | **High Tide** | Max surge elevation for TS at high tide (Higher Risk). |
| **c1_mean / c1_high** | **Category 1** | Mean / High | Worst-case surge ceiling for Cat 1 Hurricane. |
| **c2_mean / c2_high** | **Category 2** | Mean / High | Worst-case surge ceiling for Cat 2 Hurricane. |
| **c3_mean / c3_high** | **Category 3** | Mean / High | Worst-case surge ceiling for Cat 3 (Major) Hurricane. |
| **c4_mean / c4_high** | **Category 4** | Mean / High | Worst-case surge ceiling for Cat 4 Hurricane. |
| **c5_mean / c5_high** | **Category 5** | Mean / High | Worst-case surge ceiling for Cat 5 (Catastrophic) Hurricane. |

---

## 3. Usage Recommendations

1.  **Worst-Case Analysis**: For disaster planning or insurance risk, prioritize `_high` suffix columns (e.g., `c3_high`), as they account for high tide conditions.
2.  **Spatial Join**: Since this is gridded data, to find the risk for a specific building, perform a Spatial Join between the building's coordinates (Point) and the `geometry_wkt` (Polygon).
3.  **Units**: All vertical measurements are in **Feet**.