# ARC Capstone Agent Quickstart

面向需要快速接手本仓库的 agent。目标是用最短时间建立正确心智模型，避免走偏。

## 1. 先读这 4 个文件（权威优先级）

1. `AGENTS.md`
2. `CLAUDE.md`
3. `README.md`
4. `FAST-main/README.md`

如果这 4 份内容有冲突，优先按 `AGENTS.md` 执行。

---

## 2. 项目目标（不要偏航）

本仓库的生产主路径只有三步：

1. 用 NSI processed parquet 生成 FAST 可用的 building CSV。
2. 用 SLOSH processed parquet 生成 flood depth raster（GeoTIFF）。
3. 用 FAST headless 模式运行并产出预测 CSV。

不要默认扩展到数据库重构、云架构大改、UI 功能等无关范围。

---

## 3. 主链路架构

```text
NSI parquet (Oracle) -> clean/filter/map -> FAST input CSV -> FAST engine -> predictions CSV
SLOSH parquet        -> rasterize(depth)  -> GeoTIFF -----------^
```

生产运行默认是无界面（headless），不是 GUI。

---

## 4. FAST 输入契约（必须遵守）

### 4.1 FAST CSV 必填列

- `FltyId`
- `Occ`
- `Cost`
- `Area`
- `NumStories`
- `FoundationType`
- `FirstFloorHt`
- `Latitude`
- `Longitude`

### 4.2 可选列

- `ContentCost`
- `BDDF_ID`
- `CDDF_ID`
- `IDDF_ID`
- `InvCost`
- `SOID`

### 4.3 运行时必需参数（非列）

- `flC`: `Riverine` / `CoastalA` / `CoastalV`
- `raster`: `.tif` 路径

### 4.4 NSI -> FAST Canonical Mapping

- `bid -> FltyId`
- `occtype -> Occ`
- `val_struct -> Cost`
- `sqft -> Area`
- `num_story -> NumStories`
- `found_type -> FoundationType`（需归一化成 FAST 数值编码）
- `found_ht -> FirstFloorHt`
- `latitude -> Latitude`
- `longitude -> Longitude`
- `val_cont -> ContentCost`（可选）

### 4.5 SLOSH -> Raster

- geometry: `geometry_wkt`
- surge: `cN_mean` / `cN_high`（`N=0..5`）
- terrain: `topography`
- 输出：单位为英尺的 GeoTIFF。

---

## 5. 关键代码入口（先从这里读）

### 5.1 生产主编排

- `scripts/fast_e2e_from_oracle.py`
  - Oracle 对象发现与下载
  - NSI 清洗、校验、分桶
  - 并行调用 FAST
  - 合并预测结果
  - 产出 run/report manifest

### 5.2 FAST 运行入口（headless）

- `FAST-main/Python_env/run_fast.py`
  - CLI/API 包装层
  - 接收 `--inventory --mapping-json --flc --rasters`

### 5.3 FAST 核心计算引擎

- `FAST-main/Python_env/hazus_notinuse.py`
  - 文件名虽然写着 `notinuse`，但当前是实际执行引擎。
  - `local_with_options(...)` 是核心入口。

### 5.4 重要辅助脚本

- `scripts/slosh_to_raster.py`: SLOSH parquet -> GeoTIFF
- `scripts/nsi_raw_to_parquet.py`: raw GPKG/GeoJSON -> processed parquet
- `scripts/duckdb_fast_pipeline.py`: DuckDB 快速版 CSV 构建
- `scripts/h3_spatial_index.py`: H3 预筛
- `scripts/validate_pipeline.py`: 输出校验
- `scripts/match_county_coverage_cloud.py`: Ground Truth vs Athena 统计匹配（独立分析链路）

---

## 6. 配置与事件映射

- `configs/fast_e2e.yaml`
  - 批量参数、重试、firmzone 分类、foundation 映射。
- `configs/event_state_map.yaml`
  - `--event` 到州列表映射（用于快速限定 state scope）。

---

## 7. 常用命令

### 7.1 跑主流程（单州）

```bash
python scripts/fast_e2e_from_oracle.py \
  --state-scope Florida \
  --raster-name auto \
  --mode impact-only \
  --config configs/fast_e2e.yaml
```

### 7.2 按事件跑（自动州列表）

```bash
python scripts/fast_e2e_from_oracle.py \
  --event beryl_2024 \
  --raster-name auto \
  --mode impact-only
```

### 7.3 SLOSH 转 raster

```bash
python scripts/slosh_to_raster.py \
  --parquet <slosh.parquet> \
  --output <out.tif> \
  --category 3 \
  --scenario high
```

### 7.4 校验预测结果

```bash
python scripts/validate_pipeline.py exports/fast_e2e_*/final/predictions_*.csv
```

---

## 8. 输出目录约定

主流程每次运行会生成：

```text
exports/fast_e2e_<run_id>/
  input/
  fast_output/
  final/predictions_<raster>_<run_id>.csv
  reports/
    run_manifest.json
    download_manifest.json
    data_quality_report.json
    flc_assignment_report.json
    fast_execution_report.json
    raster_bbox.json
```

`run_manifest.json` 是追溯入口；先看它再定位其他报告。

---

## 9. 当前仓库状态（接手时要知道）

1. 主流程已具备端到端执行能力，并有多次 `exports/fast_e2e_*` 运行产物。
2. 历史文档中存在“空间错配导致大量 zero-loss”的复盘；阅读时要区分“历史问题”与“当前代码状态”。
3. `FAST-main/Python_env/manage.py` 是 Windows GUI/conda 管理路径，不用于当前 macOS/Linux 生产流程。
4. 本仓库可能处于 dirty worktree；默认不要回滚非本次任务改动。

---

## 10. Agent 执行默认规则（简版）

1. 默认走 headless：`FAST-main/Python_env/run_fast.py`。
2. SLOSH parquet 不能直接喂 FAST，必须先转 `.tif`。
3. `flC` 是运行级参数，不是逐行列。
4. 未明确指定时，storm-surge 任务默认 `CoastalA`。
5. 只在“缺少不可恢复输入”时提问；能从仓库/工具自查就先自查。

---

## 11. 10 分钟接手清单

1. 读 `AGENTS.md` + 本文档。
2. 看 `scripts/fast_e2e_from_oracle.py` 的 `main()` 与 `clean_state_to_fast_csv(...)`。
3. 跑 `python scripts/fast_e2e_from_oracle.py --help`。
4. 确认 `configs/event_state_map.yaml` 里目标事件存在。
5. 先做小范围验证（单州或单事件），再跑全量。

