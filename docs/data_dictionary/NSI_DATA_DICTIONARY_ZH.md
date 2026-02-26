# NSI Dataset Feature Dictionary (Parquet Version)

本项目使用的 NSI 数据源自 **USACE National Structure Inventory (2022)**，是一个包含全美建筑物位置、结构特征及人口统计信息的综合数据库。数据已转换为 Parquet 格式并按州（State）分区。

## 1. 基础识别与位置信息


| 字段名称              | 含义                | 类型     | 详细说明                                      |
| ----------------- | ----------------- | ------ | ----------------------------------------- |
| **state**         | **州名**            | STRING | **分区字段**。建筑所在的州（如 `Delaware`, `Florida`）。 |
| **bid**           | 建筑唯一 ID           | STRING | NSI 为每个结构分配的唯一标识符。                        |
| **x / longitude** | 经度                | DOUBLE | 建筑物的经度坐标 (WGS84)。`x` 和 `longitude` 通常相同。  |
| **y / latitude**  | 纬度                | DOUBLE | 建筑物的纬度坐标 (WGS84)。`y` 和 `latitude` 通常相同。   |
| **cbfips**        | Census Block FIPS | STRING | 普查区块代码（15 位），用于关联人口普查数据。                  |
| **ftprntid**      | 足迹 ID             | STRING | 关联到微软或 FEMA 建筑足迹数据的 ID。                   |


## 2. 结构特征 (Structural Characteristics)

这些字段描述了建筑物的物理属性，对于评估洪水脆弱性至关重要。


| 字段名称           | 含义       | 类型     | 详细说明                                                        |
| -------------- | -------- | ------ | ----------------------------------------------------------- |
| **occtype**    | **占用类型** | STRING | 核心字段。如 `RES1` (单户住宅), `COM1` (商业), `RES2` (公寓)。             |
| **bldgtype**   | 建筑类型     | STRING | 建筑材料/构造类型（如木质、砖混）。                                          |
| **num_story**  | 楼层数      | INT    | 建筑物的估计层数。                                                   |
| **sqft**       | 面积       | DOUBLE | 建筑面积（平方英尺）。                                                 |
| **found_type** | 地基类型     | STRING | 如 `Slab` (板式), `Crawl` (爬行空间), `Pile` (桩基)。直接影响洪水破坏程度。      |
| **found_ht**   | **地基高度** | DOUBLE | 一楼地板相对于地面的高度（英尺）。**关键字段**：用于计算一楼海拔 (First Floor Elevation)。 |
| **ground_elv** | **地面海拔** | DOUBLE | 建筑物所在地的地面海拔（英尺）。                                            |
| **val_struct** | 结构价值     | DOUBLE | 建筑物的重置成本估值（美元）。                                             |
| **val_cont**   | 内容价值     | DOUBLE | 建筑物内部财产的估值（美元）。                                             |
| **st_damcat**  | 损坏类别     | STRING | `RES` (住宅), `COM` (商业), `IND` (工业), `PUB` (公共)。             |


## 3. 人口与社会脆弱性 (Population & Vulnerability)

这些字段估算了建筑物内的人口分布，这对于红十字会的人道主义救援规划（如疏散、物资分发）至关重要。


| 字段名称           | 含义          | 类型     | 详细说明                   |
| -------------- | ----------- | ------ | ---------------------- |
| **pop2pmo65**  | 晚间人口 (>65岁) | INT    | 晚上在该建筑内的 65 岁以上老年人口估算。 |
| **pop2pmu65**  | 晚间人口 (<65岁) | INT    | 晚上在该建筑内的 65 岁以下人口估算。   |
| **pop2amo65**  | 日间人口 (>65岁) | INT    | 白天在该建筑内的 65 岁以上老年人口估算。 |
| **pop2amu65**  | 日间人口 (<65岁) | INT    | 白天在该建筑内的 65 岁以下人口估算。   |
| **o65disable** | 残障老年人比例     | DOUBLE | 65岁以上人口中有残障的概率/比例。     |
| **u65disable** | 残障非老年人比例    | DOUBLE | 65岁以下人口中有残障的概率/比例。     |


## 4. 关键计算逻辑 (Risk Assessment)

在进行洪水风险分析时：

> **一楼海拔 (First Floor Elevation, FFE) = 地面海拔 (ground_elv) + 地基高度 (found_ht)**

- **淹没判断**：如果 `SLOSH 浪高 (Surge)` > `地面海拔 (ground_elv)`，则此时**院子被淹**。
- **入户判断**：如果 `SLOSH 浪高 (Surge)` > `一楼海拔 (FFE)`，则此时**水进入房屋内部**（造成结构和内容损失）。

## 5. 数据来源

- **source**: 数据来源标识（如 `HIFLD`, `CoreLogic`, `Microsoft`）。
- **firmzone**: FEMA 洪水保险费率图 (FIRM) 分区代码（如 `AE`, `X`）。

