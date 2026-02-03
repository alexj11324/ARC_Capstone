# NSI Dataset Feature Dictionary (Parquet Version)

The NSI data in this project is sourced from the **USACE National Structure Inventory (2022)**. It provides point-level data for structures across the US, including structural characteristics and population estimates. The data is partitioned by State.

## 1. Identification & Location

| Feature Name | Meaning | Type | Details |
| :--- | :--- | :--- | :--- |
| **state** | **State Name** | STRING | **Partition Key**. The US State where the structure is located (e.g., `Delaware`). |
| **bid** | Building ID | STRING | Unique identifier assigned by NSI for each structure. |
| **x / longitude** | Longitude | DOUBLE | Longitude coordinate (WGS84). |
| **y / latitude** | Latitude | DOUBLE | Latitude coordinate (WGS84). |
| **cbfips** | Census Block FIPS | STRING | 15-digit Census Block code, useful for joining with Census demographic data. |
| **ftprntid** | Footprint ID | STRING | ID linking to external building footprint datasets (Microsoft/FEMA). |

## 2. Structural Characteristics

These features describe physical attributes essential for assessing flood vulnerability and potential damage.

| Feature Name | Meaning | Type | Details |
| :--- | :--- | :--- | :--- |
| **occtype** | **Occupancy Type** | STRING | Core classification. e.g., `RES1` (Single Family), `COM1` (Retail), `RES3` (Multi-Family). |
| **bldgtype** | Building Material | STRING | Construction material (e.g., Wood, Masonry, Concrete). |
| **num_story** | Number of Stories | INT | Estimated number of stories above ground. |
| **sqft** | Area (SqFt) | DOUBLE | Total floor area in square feet. |
| **found_type** | Foundation Type | STRING | e.g., `Slab`, `Crawl`, `Pile`, `Basement`. Critical for determining First Floor Elevation. |
| **found_ht** | **Foundation Height** | DOUBLE | Height of the first floor above the ground (in Feet). |
| **ground_elv** | **Ground Elevation** | DOUBLE | Elevation of the ground at the structure's location (in Feet). |
| **val_struct** | Structure Value | DOUBLE | Estimated replacement cost of the structure ($). |
| **val_cont** | Content Value | DOUBLE | Estimated value of contents inside the structure ($). |
| **st_damcat** | Damage Category | STRING | `RES` (Residential), `COM` (Commercial), `IND` (Industrial), `PUB` (Public). |

## 3. Population & Social Vulnerability

These features estimate the population present in the structure, crucial for Red Cross evacuation planning and impact analysis.

| Feature Name | Meaning | Type | Details |
| :--- | :--- | :--- | :--- |
| **pop2pmo65** | Night Pop (>65) | INT | Estimated population over 65 present at night (2 PM). |
| **pop2pmu65** | Night Pop (<65) | INT | Estimated population under 65 present at night. |
| **pop2amo65** | Day Pop (>65) | INT | Estimated population over 65 present during the day (2 AM). |
| **pop2amu65** | Day Pop (<65) | INT | Estimated population under 65 present during the day. |
| **o65disable** | Elderly Disability | DOUBLE | Probability/Rate of disability among the >65 population. |
| **u65disable** | General Disability | DOUBLE | Probability/Rate of disability among the <65 population. |

## 4. Risk Assessment Logic

For flood impact analysis, use the following logic:

> **First Floor Elevation (FFE) = Ground Elevation (ground_elv) + Foundation Height (found_ht)**

*   **Yard Inundation**: If `SLOSH Surge` > `ground_elv`. (The property is wet).
*   **Structure Inundation**: If `SLOSH Surge` > `FFE`. (Water enters the building, causing significant damage).

## 5. Metadata

*   **source**: Origin of the record (e.g., `HIFLD`, `Microsoft`, `CoreLogic`).
*   **firmzone**: FEMA Flood Insurance Rate Map zone (e.g., `AE`, `VE`, `X`).
