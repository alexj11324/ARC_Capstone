# Processed Parquet 数据说明

本文档记录 Red Cross Capstone 项目在 AWS S3 上**已加工好的 Parquet** 数据的位置、分区方式及 Schema，便于查询与下游（如 FAST）使用。

---

## 1. 数据位置总览

| 数据类型 | S3 路径 | Athena 数据库 | 备注 |
|----------|---------|---------------|------|
| NSI（建筑） | `s3://red-cross-capstone-project-data/processed/nsi/` | `red_cross_hurricane` | 按州分区，最新一批约 2026-02-10 生成 |
| SLOSH（风暴潮） | `s3://red-cross-capstone-project-data/processed/slosh/` | `red_cross_hurricane` | 按 basin 分区，同上 |

表中另有指向 `processed/nsi-parquet`、`processed/slosh-parquet`、`processed/slosh-parquet-v2` 的 Athena 表，可能为历史路径或不同 ETL 版本，以实际 S3 列表为准。

---

## 2. NSI Processed Parquet

### 2.1 路径与分区

- **路径**：`s3://red-cross-capstone-project-data/processed/nsi/`
- **分区键**：`state`（州名，如 `Alabama`, `Florida`, `North_Carolina`）
- **单分区示例**：`processed/nsi/state=Alabama/part-00000-xxx.c000.snappy.parquet`
- **压缩**：Snappy

### 2.2 Athena 表

- **表名**：`nsi_data` 或 `processed_nsi_parquet`（可能指向不同 S3 前缀，见上）
- **数据库**：`red_cross_hurricane`

### 2.3 Schema（列一致，类型可能略有差异）

| 列名 | 类型 | 说明 |
|------|------|------|
| bid | string | 建筑唯一 ID |
| bldgtype | string | 建筑类型/材料 |
| cbfips | string | Census Block FIPS |
| fd_id | bigint | 关联 ID |
| firmzone | string | FEMA FIRM 分区 |
| found_ht | double | 地基/首层高度（英尺） |
| found_type | string | 地基类型（Slab / Crawl / Pile / Basement） |
| ftprntid | string | 足迹 ID |
| ftprntsrc | string | 足迹来源 |
| ground_elv | double | 地面海拔（英尺） |
| ground_elv_m | double | 地面海拔（米） |
| med_yr_blt | int / bigint | 建造年份中值 |
| num_story | int / bigint | 楼层数 |
| o65disable | double | 65+ 残障比例 |
| occtype | string | 占用类型（RES1, COM1 等） |
| pop2amo65 | int / bigint | 日间 65+ 人口 |
| pop2amu65 | int / bigint | 日间 <65 人口 |
| pop2pmo65 | int / bigint | 晚间 65+ 人口 |
| pop2pmu65 | int / bigint | 晚间 <65 人口 |
| source | string | 数据来源 |
| sqft | double | 建筑面积（平方英尺） |
| st_damcat | string | 损坏类别（RES/COM/IND/PUB） |
| students | int / bigint | 学生数 |
| u65disable | double | <65 残障比例 |
| val_cont | double | 内容价值（美元） |
| val_struct | double | 结构价值（美元） |
| val_vehic | int / bigint | 车辆价值相关 |
| x | double | 经度 |
| y | double | 纬度 |
| longitude | double | 经度 |
| latitude | double | 纬度 |
| processed_at | timestamp | 加工时间 |
| source_file | string | （仅部分表）源文件名 |

分区列 `state` 为 string，在路径中体现为 `state=Alabama` 等。

---

## 3. SLOSH Processed Parquet

### 3.1 路径与分区

- **路径**：`s3://red-cross-capstone-project-data/processed/slosh/`
- **分区键**：`basin`（盆地/风暴代号，如 `ap3mom`, `br3mom`, `ch2mom`）
- **单分区示例**：`processed/slosh/basin=ap3mom/ap3mom_AGL.parquet`
- **压缩**：Snappy

### 3.2 Athena 表（两套 Schema）

- **slosh_data**：指向 `s3://.../processed/slosh-parquet`（或等价路径）
- **slosh_data_parquet**：指向 `s3://.../processed/slosh-parquet-v2`
- **数据库**：`red_cross_hurricane`

两表均按 `basin` (string) 分区；**列定义不同**，见下。

### 3.3 Schema：slosh_data（v1）

| 列名 | 类型 | 说明 |
|------|------|------|
| poly_id | string | 多边形 ID |
| i_index | int | 网格 i |
| j_index | int | 网格 j |
| c1_mean, c1_high | double | 类别 1 均值/高值 |
| c2_mean, c2_high | double | 类别 2 |
| c3_mean, c3_high | double | 类别 3 |
| c4_mean, c4_high | double | 类别 4 |
| c5_mean, c5_high | double | 类别 5 |
| topograp_9 | double | 地形相关 |
| topography | double | 地形/高程 |
| geometry_wkt | string | 几何 WKT |

无 `c0_mean` / `c0_high`。

### 3.4 Schema：slosh_data_parquet（v2）

| 列名 | 类型 | 说明 |
|------|------|------|
| poly_id | string | 多边形 ID |
| i_index | int | 网格 i |
| j_index | int | 网格 j |
| c0_mean, c0_high | double | 类别 0 均值/高值 |
| c1_mean, c1_high | double | 类别 1 |
| c2_mean, c2_high | double | 类别 2 |
| c3_mean, c3_high | double | 类别 3 |
| c4_mean, c4_high | double | 类别 4 |
| c5_mean, c5_high | double | 类别 5 |
| topography | int | 地形（整型） |
| geometry_wkt | string | 几何 WKT |

无 `topograp_9`；`topography` 为 int。

---

## 4. 使用与查询示例

- **NSI 按州查**（Athena）  
  ```sql
  SELECT bid, longitude, latitude, sqft, occtype, num_story, found_type, found_ht, val_struct, val_cont
  FROM red_cross_hurricane.nsi_data
  WHERE state = 'Florida'
  LIMIT 10;
  ```

- **SLOSH 按 basin 查**（Athena）  
  ```sql
  SELECT poly_id, c1_mean, c2_mean, topography, geometry_wkt
  FROM red_cross_hurricane.slosh_data
  WHERE basin = 'ap3mom'
  LIMIT 10;
  ```

- **S3 列举**（CLI）  
  ```bash
  aws s3 ls s3://red-cross-capstone-project-data/processed/nsi/ --recursive
  aws s3 ls s3://red-cross-capstone-project-data/processed/slosh/ --recursive
  ```

---

## 5. 与 FAST 的对应关系

- **建筑清单 CSV**：可从 NSI Parquet 中选取并重命名列，导出为 CSV，参见项目内 FAST 输入要求及 `docs/data_dictionary/NSI_DATA_DICTIONARY_ZH.md`。
- **水深栅格**：FAST 需要 GeoTIFF；需用 SLOSH 的几何 + 某一档水深/浪高列做栅格化得到 .tif，而非直接读 Parquet。

---

*文档根据当前 S3 与 Athena 元数据整理，若路径或表名有变更请以实际环境为准并更新此文档。*
