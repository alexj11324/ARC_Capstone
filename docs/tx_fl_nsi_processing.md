# TX/FL NSI 数据处理与 Beryl 2024 FAST Pipeline 运行记录

> 日期：2026-02-24 | 执行者：Claude Code

## 背景

Pipeline 代码已修复（raster 过滤、depth 采样、去重、event mapping），但 **Texas 和 Florida 缺少 processed parquet**。OCI bucket 和 S3 `processed/` 仅有 16 个州，TX/FL 只有 raw GPKG。这两个州是 Hurricane Beryl 2024 的关键州。

**Blocker**：`--event beryl_2024` 会失败，因为 `nsi/state=Texas/` 和 `nsi/state=Florida/` 在 OCI 中不存在。

## 数据盘点

| 位置 | Texas | Florida |
|------|-------|---------|
| S3 `raw/nsi/` | `nsi_2022_48_Texas.gpkg.zip` (994MB) | `nsi_2022_12_Florida.gpkg.zip` (760MB) |
| S3 `processed/nsi/` | ❌ 缺失 | ❌ 缺失 |
| OCI `nsi/` | ❌ 缺失 → ✅ 已补 | ❌ 缺失 → ✅ 已补 |

---

## 执行步骤

### Step 1：创建转换脚本

创建 `scripts/nsi_raw_to_parquet.py`，支持两种引擎：

- **DuckDB spatial**（主引擎）：单 SQL pass，流式处理，低内存
- **geopandas + pyogrio**（fallback）：兼容性更好

核心逻辑：
1. 读取 GPKG，检测 CRS（非 WGS84 则 reproject）
2. 从 geometry 提取 `x`, `y`, `longitude`, `latitude`
3. 添加 `processed_at` 时间戳
4. 缺失列填 NULL，按 32 列 target schema 输出
5. Snappy 压缩 Parquet

```bash
python scripts/nsi_raw_to_parquet.py --input <gpkg> --output <parquet> [--engine duckdb|geopandas]
```

Target schema（32 列，与 Alabama 等已有 parquet 一致）：
```
bid, bldgtype, cbfips, fd_id, firmzone, found_ht, found_type,
ftprntid, ftprntsrc, ground_elv, ground_elv_m, med_yr_blt,
num_story, o65disable, occtype, pop2amo65, pop2amu65, pop2pmo65,
pop2pmu65, source, sqft, st_damcat, students, u65disable,
val_cont, val_struct, val_vehic, x, y, longitude, latitude,
processed_at
```

### Step 2：下载、转换、上传

#### 2a. 从 S3 下载 GPKG.zip 并解压

```bash
aws s3 cp s3://red-cross-capstone-project-data/raw/nsi/nsi_2022_48_Texas.gpkg.zip /tmp/
aws s3 cp s3://red-cross-capstone-project-data/raw/nsi/nsi_2022_12_Florida.gpkg.zip /tmp/
unzip /tmp/nsi_2022_48_Texas.gpkg.zip -d /tmp/nsi_tx/
unzip /tmp/nsi_2022_12_Florida.gpkg.zip -d /tmp/nsi_fl/
```

#### 2b. 本地转换 GPKG → Parquet

最初尝试在 OCI 实例上转换，但 OCI VM（ARM/aarch64, Ubuntu 20.04, Python 3.8）环境太裸：
- duckdb 无 aarch64/Python 3.8 预编译 wheel，源码编译卡住
- 无 GDAL/ogr2ogr
- pyOpenSSL 损坏导致 pip 不可用（已通过 mv crypto.py 修复）

**最终方案**：本地转换 + 直接上传 OCI Object Storage。

```bash
python scripts/nsi_raw_to_parquet.py --input "/tmp/nsi_tx/nsi_2022_48.gpkg" --output /tmp/nsi_tx.parquet
python scripts/nsi_raw_to_parquet.py --input "/tmp/nsi_fl/nsi_2022_12.gpkg" --output /tmp/nsi_fl.parquet
```

实际使用 geopandas fallback（本地未装 duckdb），结果一致。

| 州 | 输入行数 | 输出大小 | Schema |
|----|---------|---------|--------|
| Texas | 10,210,426 | 793MB (local) / 832MB (OCI) | 32 列 OK |
| Florida | 8,373,586 | 611MB (local) / 641MB (OCI) | 32 列 OK |

#### 2c. 上传到 OCI Object Storage

```bash
oci --profile DEFAULT os object put --namespace-name id9odvkah5da \
  --bucket-name arc-capstone-processed-parquet \
  --name "nsi/state=Texas/part-00000.snappy.parquet" \
  --file /tmp/nsi_tx.parquet --force

oci --profile DEFAULT os object put --namespace-name id9odvkah5da \
  --bucket-name arc-capstone-processed-parquet \
  --name "nsi/state=Florida/part-00000.snappy.parquet" \
  --file /tmp/nsi_fl.parquet --force
```

OCI 自动分片上传（TX 7 parts, FL 5 parts），均成功。

### Step 3：运行 FAST Pipeline

```bash
python scripts/fast_e2e_from_oracle.py \
  --event beryl_2024 --no-resume --mode impact-only --max-workers 4
```

运行过程中遇到并解决了以下问题：

| 问题 | 解决方式 |
|------|---------|
| `event_state_map.yaml` 用州缩写（TX）但 OCI 分区键是全名（Texas） | 将 yaml 中所有州名改为全名 |
| `No module named 'utm'` | `pip install utm` |
| `No module named 'osgeo'` | `conda install -y -c conda-forge gdal`（pip 编译失败） |

第3次运行成功，结果：

- Raster 覆盖范围：lon -98.06 ~ -93.55, lat 25.84 ~ 30.88（TX 沿海）
- AL/FL/MS 无 raster 重叠，自动跳过（符合预期，Beryl 主要影响 TX）
- Louisiana：1,830,521 输入 → 1 row 在 raster 范围内
- Texas：10,210,426 输入 → 104,522 rows 在 raster 范围内
- FAST runs：3/3 成功（TX/CoastalA, TX/CoastalV, LA/CoastalA）

### Step 4：验证

```bash
python scripts/validate_pipeline.py \
  exports/fast_e2e_20260224_055259/final/predictions_BERYL_2024_adv41_e10_ResultMaskRaster_20260224_055259.csv
```

验证结果：

| 指标 | 值 |
|------|-----|
| 总行数 | 104,523 |
| Zero-loss 行数 | 15,101 (14.45%) |
| Texas 行数 | 104,522 |
| Louisiana 行数 | 1 |
| CoastalA | 102,263 |
| CoastalV | 2,260 |
| Texas 总损失 | $7,410,837.5 |
| 验证状态 | **PASSED** |

Top 5 建筑类型：RES1 (82,262), RES2 (6,308), RES3A (4,085), COM4 (3,981), COM3 (940)

---

## 产出文件

| 文件 | 说明 |
|------|------|
| `scripts/nsi_raw_to_parquet.py` | GPKG/GeoJSON → Parquet 转换脚本（可复用） |
| `configs/event_state_map.yaml` | 修复州名为全名（匹配 OCI 分区键） |
| `exports/fast_e2e_20260224_055259/` | 本次 pipeline 完整输出 |
| `exports/.../final/predictions_BERYL_2024_*.csv` | 最终预测结果（104,523 rows） |

---

## 踩坑与经验

1. **OCI 实例不适合做数据转换**：ARM/aarch64 + Python 3.8 生态太差，duckdb/GDAL 都装不上。大文件转换建议在本地或 x86 实例上做，再上传 Object Storage。

2. **event_state_map.yaml 必须用全名**：OCI 分区键是 `state=Texas` 不是 `state=TX`，pipeline 的 `filter_states()` 做精确匹配。

3. **GDAL 在 macOS 上只能用 conda 装**：pip 需要编译 C++ 绑定，缺少系统级 GDAL 库会失败。用 `conda install -c conda-forge gdal` 最稳。

4. **Zero-loss 14.45% 略高于 10% 目标**：raster bbox 是矩形，但实际风暴潮覆盖是不规则形状，bbox 边缘区域的建筑 depth=0。后续可用 H3 spatial pre-filtering 或 raster valid-pixel convex hull 进一步优化。

---

## 复现命令（完整）

```bash
# 1. 下载 & 解压
aws s3 cp s3://red-cross-capstone-project-data/raw/nsi/nsi_2022_48_Texas.gpkg.zip /tmp/
aws s3 cp s3://red-cross-capstone-project-data/raw/nsi/nsi_2022_12_Florida.gpkg.zip /tmp/
unzip /tmp/nsi_2022_48_Texas.gpkg.zip -d /tmp/nsi_tx/
unzip /tmp/nsi_2022_12_Florida.gpkg.zip -d /tmp/nsi_fl/

# 2. 转换
python scripts/nsi_raw_to_parquet.py --input "/tmp/nsi_tx/nsi_2022_48.gpkg" --output /tmp/nsi_tx.parquet
python scripts/nsi_raw_to_parquet.py --input "/tmp/nsi_fl/nsi_2022_12.gpkg" --output /tmp/nsi_fl.parquet

# 3. 上传 OCI
oci os object put --namespace-name id9odvkah5da --bucket-name arc-capstone-processed-parquet \
  --name "nsi/state=Texas/part-00000.snappy.parquet" --file /tmp/nsi_tx.parquet --force
oci os object put --namespace-name id9odvkah5da --bucket-name arc-capstone-processed-parquet \
  --name "nsi/state=Florida/part-00000.snappy.parquet" --file /tmp/nsi_fl.parquet --force

# 4. 运行 pipeline
python scripts/fast_e2e_from_oracle.py --event beryl_2024 --no-resume --mode impact-only --max-workers 4

# 5. 验证
python scripts/validate_pipeline.py exports/fast_e2e_*/final/predictions_*.csv
```
