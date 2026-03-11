# 红十字会灾害人口影响预测 — 实施方案

> CMU Heinz MSPPM 2026 Capstone | American Red Cross
> 日期: 2026-03-10

## 1. 问题诊断

### 1.1 Gemini 方案评估

| 方面 | Gemini 建议 | 评估 | 我们的决定 |
|------|------------|------|-----------|
| Geo-EVT-VAE 混合架构 | GAN/VAE + GPD 参数网络 | **过度工程**: 108条数据训练 GAN/VAE 必然过拟合 | **拒绝** — 用确定性聚合 + 轻量校准 |
| 目标变量转比率 | Impact Rate = Shelter / Pop | **正确**: 消除量纲差异 | **采纳** |
| LOEO-CV 验证 | Leave-One-Event-Out | **正确**: 唯一合理的验证策略 | **采纳** |
| 物理约束（人口上限+距离衰减） | Loss 函数惩罚项 | **思路正确，实现过重**: 不需要梯度惩罚 | **采纳思路, 改为后处理规则** |
| EVT 尾部估计 | GPD 建模极端事件 | **有价值**: 但应直接拟合，不经过神经网络 | **采纳，简化实现** |
| Colab 部署 | ipywidgets + file upload | **正确**: 零配置，适合应急场景 | **采纳** |
| xlwings | 明确弃用 | **正确** | **同意** |

### 1.2 核心洞察

Gemini 方案最大的问题：**把一个工程聚合问题误判为 ML 生成问题**。

我们已有 350 万条建筑级损害预测（FAST 输出），NSI 提供建筑级人口估算。从 "建筑损害 → 人口流离 → 避难需求" 的链路是 **FEMA 标准方法论**（确定性的），不需要生成模型。

真正需要 ML 的仅仅是：**校准 "流离人口 → 实际避难人数" 的转化率**，因为不是所有流离者都去红十字会避难所。

## 2. 三层架构 (Three-Tier Architecture)

```
Tier 1: 确定性聚合管线  ← 核心交付物（可验证、可解释）
Tier 2: 统计校准模型    ← 用 Ground Truth 学习转化率
Tier 3: EVT 不确定性量化 ← 为极端场景提供置信区间
```

### Tier 1: 确定性聚合管线

**数据流:**
```
FAST predictions (3.5M buildings)
    ↓ Spatial Join
County boundaries (Athena)
    ↓ Aggregation
County-level damage summary
    ↓ Join NSI pop fields
County-level displaced population estimate
    ↓ FEMA displacement function
Estimated Population Impacted
```

**FEMA 流离判定标准:**
- 住宅建筑（RES*）且 `depth_in_struc > 0` (水进入结构) → 居民流离
- 或 `bldgdmgpct > 15%` → 功能性流离（结构受损不宜居住）
- 流离人口 = 受损建筑的 NSI 人口字段之和

**关键公式:**
```
displaced_pop(county, event) = SUM(
    pop_night(building)
    FOR building IN county
    WHERE bldgdmgpct > displacement_threshold  # 15%
    AND occtype LIKE 'RES%'
)
```

### Tier 2: 统计校准模型

**目标**: 学习 `shelter_rate = actual_shelter / displaced_pop`

因为：
- 并非所有流离者都去红十字会避难所（很多人投亲靠友、住酒店）
- 不同地区的社会脆弱性影响避难率
- 灾害强度影响避难率

**特征矩阵 (每行 = 一个 event-county 对):**

| 特征 | 来源 | 意义 |
|------|------|------|
| `displaced_pop` | Tier 1 | FEMA 标准流离人口估算 |
| `total_bldg_loss_usd` | FAST 聚合 | 县级总经济损失 |
| `avg_damage_pct` | FAST 聚合 | 平均建筑损害率 |
| `max_depth_ft` | FAST 聚合 | 最大淹没深度 |
| `pct_severe` | FAST 聚合 | >50% 损害建筑比例 |
| `county_pop` | Census API | 县总人口 |
| `svi_score` | CDC SVI | 社会脆弱性指数 |
| `pct_elderly` | NSI 聚合 | 老年人口比例 |
| `pct_disabled` | NSI 聚合 | 残障人口比例 |
| `coastal_proximity` | 计算 | 到海岸线距离 |

**模型选择:**
- **基线**: `shelter = displaced_pop × calibration_factor`（全局常数）
- **进阶**: XGBoost 回归, LOEO-CV 验证
- **约束**: 预测值 ∈ [0, county_pop × 0.15]

### Tier 3: EVT 不确定性量化

**目的**: 为红十字会提供 "最坏情况" 估计

**方法:**
1. 计算 `shelter_rate = shelter / displaced_pop` 的分布
2. 对超过 90 分位数的值拟合 GPD
3. 输出：点估计 + 90/95/99 分位数预测区间

**直接拟合 GPD（非通过神经网络）:**
```python
from scipy.stats import genpareto
excess = shelter_rates[shelter_rates > threshold] - threshold
xi, loc, sigma = genpareto.fit(excess, floc=0)
```

## 3. 实施步骤

### Phase 1: 数据工程 (Athena SQL + Python)

| 步骤 | 脚本 | 输入 | 输出 |
|------|------|------|------|
| 1a | `01_county_damage_agg.sql` | `arc_storm_surge.predictions` + `arc_analysis.us_county_boundaries` | 县级损害聚合表 |
| 1b | `02_nsi_pop_by_county.sql` | `red_cross_hurricane.nsi_data` | 县级人口聚合 |
| 1c | `03_fetch_census_svi.py` | Census API + CDC SVI CSV | 县级人口/SVI 数据 |
| 1d | `04_build_feature_matrix.py` | 上述三者 + Ground Truth | 完整特征矩阵 CSV |

### Phase 2: 模型开发

| 步骤 | 脚本 | 说明 |
|------|------|------|
| 2a | `05_displacement_model.py` | Tier 1 确定性流离估算 |
| 2b | `06_shelter_calibration.py` | Tier 2 校准模型 (LOEO-CV) |
| 2c | `07_evt_confidence.py` | Tier 3 EVT 尾部分析 |
| 2d | `08_validate.py` | 综合验证 + 压力测试 |

### Phase 3: 部署

| 步骤 | 产出 | 说明 |
|------|------|------|
| 3a | `arc_shelter_predictor.ipynb` | Colab 零配置部署笔记本 |
| 3b | `ARC_Impact_Template.xlsx` | Excel 模板（含查找公式） |
| 3c | `model_card.md` | 模型文档（精度、局限、使用说明） |

## 4. 验证策略

### 4.1 LOEO-CV (Leave-One-Event-Out Cross-Validation)

```
for event in [BERYL, DEBBY, FLORENCE, HELENE, IAN, IDALIA, IDA, MICHAEL, MILTON]:
    train = all_data[all_data.event != event]
    test  = all_data[all_data.event == event]
    model.fit(train)
    predictions[event] = model.predict(test)
```

### 4.2 物理约束检查（后处理规则，非 Loss 惩罚）

```python
# 硬约束
assert predicted_shelter <= county_population * MAX_SHELTER_RATE  # 15%
assert predicted_shelter >= 0

# 软约束（告警但不拒绝）
if svi_score < 0.2 and shelter_rate > 0.95_percentile:
    flag_for_review("High shelter in low-vulnerability county")
if surge_depth < 0.5 and predicted_shelter > 500:
    flag_for_review("High shelter with minimal surge")
```

### 4.3 压力测试

- 合成输入: Category 5 飓风直击人口密集区
- 验证: 预测不超过人口上限, 距离衰减合理
- 边界: 零涌浪 → 零避难

## 5. 交付物清单

| 交付物 | 格式 | 受众 |
|--------|------|------|
| 县级人口影响预测 (9 事件) | CSV/Parquet | 技术团队 |
| LOEO-CV 验证报告 | Markdown + 图表 | Capstone 评审 |
| Colab 部署笔记本 | .ipynb | 红十字会应急人员 |
| Excel 模板 | .xlsx | 红十字会应急人员 |
| 模型文档 (Model Card) | Markdown | 所有利益相关者 |
| 技术方案对比 (Gemini vs 实际) | Markdown | Capstone 评审 |

## 6. 计算资源预算

| 资源 | 估算成本 | 用途 |
|------|---------|------|
| AWS Athena | ~$2-5 (按扫描量计费) | 数据聚合查询 |
| GCP Compute | ~$5-10 | Colab Pro 验证 + 模型训练 |
| Census API | 免费 | 人口数据获取 |
| CDC SVI | 免费 | 脆弱性数据下载 |
| **总计** | **< $15** | 远低于 $20 预算 |

## 7. 时间线

| 阶段 | 预计耗时 | 关键产出 |
|------|---------|---------|
| Phase 1: 数据工程 | 2-3 小时 | 完整特征矩阵 |
| Phase 2: 模型开发 | 2-3 小时 | 校准模型 + EVT |
| Phase 3: 验证 | 1-2 小时 | LOEO-CV 报告 |
| Phase 4: 部署 | 1-2 小时 | Colab + Excel |

## 8. 实验结果总结 (2026-03-10)

### 8.1 数据匹配结果

| 数据源 | 行数 | 说明 |
|--------|------|------|
| FAST 建筑级预测 | ~3.5M | 存储在 Athena |
| 县级聚合 (Athena 空间连接) | 386 行 | 9个事件, 跨 ~100 个县 |
| Ground Truth | 131 行 | 9个事件, 131 个县 |
| **内连接结果** | **56 行** | **5个事件, 56 个县** |
| Census 人口匹配 | 56/56 | 100% 匹配 |

**4 个事件零匹配** (DEBBY, IAN, IDALIA, MICHAEL): FAST 预测覆盖的县域 与 红十字会实际运营避难所的县域不重叠。例如 Ian 的 FAST 预测覆盖佛罗里达东北部, 但 GT 避难所在西南部（实际登陆区域）。

### 8.2 模型结果

| 层级 | 指标 | 值 | 解读 |
|------|------|-----|------|
| **Tier 1** | 中位避难率 | 0.73% | 约 1/137 流离者去红十字会避难所 |
| **Tier 1** | RMSE | 546.6 | 简单但可解释 |
| **Tier 2** | RMSE (LOEO-CV) | 407.6 | 略优于 Tier 1 |
| **Tier 2** | R² | -0.308 | 低于均值预测, ML 校准未能显著改进 |
| **Tier 2** | logRMSE | 1.88 | 乘性误差约 6.5 倍 |
| **Tier 3** | GPD 形状参数 ξ | 1.35 | 重尾分布, 尾部估计不稳定 |

### 8.3 关键发现

1. **避难率跨事件差异极大** (0.005% ~ 242%): 内陆洪水事件 (如 Florence) 的避难率远高于风暴潮事件 (如 Beryl)
2. **建筑损害代理指标的局限**: `n_res_displaced × 2.53` 仅捕捉风暴潮损害, 无法覆盖降雨/内陆洪水带来的额外流离
3. **56 行数据不足以训练有效的 ML 模型**: R² < 0 说明 XGBoost/RF/Ridge 集成无法从当前特征集学到有用模式
4. **确定性方法 (Tier 1) + 情景乘数是最可靠的交付物**: 简单、可解释、适合应急规划

### 8.4 交付物清单 (已完成)

| 文件 | 路径 | 说明 |
|------|------|------|
| 验证报告 | `outputs/validation_report.md` | 完整 LOEO-CV 结果 + 架构对比 |
| 预测结果 | `outputs/predictions.csv` | 56 个县级预测 vs Ground Truth |
| Excel 规划模板 | `outputs/arc_shelter_planning_template.xlsx` | 4 个工作表, 含新事件模板 |
| Colab 笔记本 | `notebooks/deploy_population_impact.ipynb` | 零配置部署, 含可视化 |
| 完整数据集 | `outputs/full_results.csv` | 所有特征 + 预测 + GT |
| 模型指标 | `outputs/metrics.json` | 机器可读的评估指标 |
| 实施方案 (中文) | `PLAN_ZH.md` | 本文件 |
| 实施方案 (英文) | `PLAN_EN.md` | 英文版 |

### 8.5 后续建议

1. **优先**: 为 Ian/Debby/Idalia/Michael 重新生成 FAST 预测, 覆盖实际登陆区域
2. **高价值**: 集成 NWS 降雨数据, 覆盖内陆洪水位移 (解决 Florence 类事件的高避难率)
3. **中等**: 恢复 NSI 建筑级人口字段 (pop2pmo65, pop2pmu65), 替代建筑计数代理
4. **改进**: 按事件类型 (风暴潮 vs 降雨) 分层校准避难率
