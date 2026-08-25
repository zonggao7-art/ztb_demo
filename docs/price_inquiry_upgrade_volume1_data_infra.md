# 上册：数据准备与基础设施搭建导航报告

> **所属方案**：[price_inquiry_sub_route_upgrade_plan.md](./price_inquiry_sub_route_upgrade_plan.md)  
> **阶段编号**：阶段一（共两阶段）  
> **依赖关系**：本阶段无上游依赖，为整个升级改造的起点  
> **衔接点**：本阶段产出物（`ztb_clean` 数据库及其 4 张已填充索引的业务表）是下册"检索逻辑改造与测试上线"的**强制前置条件**——下册所有检索代码均依赖此数据库连接与表结构。下册开始前，必须确认本阶段全部产出物已就绪并通过验证。

---

## 1. 总体设计思想与关键决策

### 1.1 设计思想

本阶段的目标是将 `raw_tables/` 目录下 4 个 CSV 文件（公司信息、企业处罚、产品信息、招标项目）导入一个**全新的、纯净的** MySQL 数据库 `ztb_clean`，建立索引体系，为下册的检索逻辑改造提供唯一的结构化数据源。

**核心原则**：
- **纯净独立**：`ztb_clean` 与任何旧数据库零依赖，数据仅来源于 4 个 CSV 文件，架构清晰、无历史包袱。
- **单库多表**：所有数据存入同一数据库，按业务路由分表存储，单连接复用，支持跨表关联查询。
- **索引后置**：数据全部写入后再统一建 FULLTEXT 索引，避免逐行写入期间的索引维护开销。

### 1.2 关键决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 数据库组织方式 | 单库多表 `ztb_clean` | 跨业务关联查询天然支持；运维简单（一套备份、一条连接）；数据量可控（CSV 总大小约 33MB） |
| 字符集 | `utf8mb4` + `utf8mb4_unicode_ci` | 覆盖中文生僻字；排序准确性优于 `general_ci` |
| 全文索引引擎 | ngram parser（`WITH PARSER ngram`） | 原生支持中文分词，无需额外插件 |
| 索引构建顺序 | 先 BTREE + UNIQUE KEY，数据写入后统一建 FULLTEXT | 避免 INSERT 期间的全文索引维护开销 |
| 数据去重策略 | `company_info` 按 `credit_code`（UNIQUE KEY）去重；`bid_project` 按 `project_number` 去重 | CSV 数据可能存在重复行 |
| 导入方式 | Python 流式读取 CSV → 批量 INSERT（5000 条/批） | 控制内存占用，利用 MySQL 批量写入性能 |

---

## 2. 改造流程与步骤清单

按执行顺序，每步完成后进行验证再进入下一步。

### 步骤 1：CSV 数据质量预检

**目标**：确认 4 个 CSV 文件可读、编码正确、字段完整。

**操作**：
1. 用 Python `csv.DictReader` 或 `pandas.read_csv` 快速读取每个文件的前 100 行
2. 检查：编码是否为 UTF-8、列名是否与 DDL 定义一致、空值率、特殊字符（如千分位逗号在金额列）

**验证**：4 个 CSV 文件均可正常读取，无编码异常。记录发现的脏数据模式，供步骤 4 清洗逻辑参考。

### 步骤 2：创建数据库并执行 DDL

**目标**：在 MySQL `192.168.10.120:3306` 上创建 `ztb_clean` 数据库及 4 张表结构（**先不建 FULLTEXT 索引**）。

**操作**：
1. 连接 MySQL 执行 `CREATE DATABASE IF NOT EXISTS ztb_clean DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;`
2. 执行 4 张表的 DDL（建表语句见 [price_inquiry_sub_route_upgrade_plan.md §6.1](./price_inquiry_sub_route_upgrade_plan.md#61-mysql-表结构设计)），**仅包含 BTREE 索引和 UNIQUE KEY，不包含 FULLTEXT 索引**。
   - **注意**：`company_info` 表在原方案 DDL 基础上新增衍生列 `registered_capital_amount_cny DECIMAL(20,2) DEFAULT NULL COMMENT '注册资本数值(元)'`，并为其建立 BTREE 索引 `INDEX idx_registered_capital_amount (registered_capital_amount_cny)`。该列由步骤 3 的清洗逻辑自动填充，用于下册按注册资本数值范围过滤与排序。
3. 确认 MySQL `ngram_token_size=2`（查询 `SHOW VARIABLES LIKE 'ngram_token_size';`）

**验证**：
```sql
SHOW TABLES IN ztb_clean;
-- 预期输出：company_info, company_penalty, product_info, bid_project
SHOW CREATE TABLE ztb_clean.company_info;
-- 确认含 uk_credit_code、registered_capital_amount_cny 列及 idx_registered_capital_amount 和 BTREE 索引，不含 FULLTEXT
```

### 步骤 3：编写 CSV 导入脚本

**目标**：编写 `scripts/csv_to_mysql.py`，支持流式读取 CSV 并批量写入 MySQL。

**脚本核心功能**：
- 流式读取 CSV（`csv.DictReader`），每 5000 条批量 INSERT
- 字段清洗：空字符串 → `None`；金额列去除千分位逗号后转 `float`
- **注册资本数值化**：对 `registered_capital` 原始字符串进行解析，提取数值并统一换算为人民币"元"单位，写入衍生列 `registered_capital_amount_cny`（详见下方清洗规则）
- 支持 `INSERT ... ON DUPLICATE KEY UPDATE`（处理 `credit_code` / `project_number` 重复）
- 导入完成后打印每张表的行数统计

**清洗规则要点**：
| CSV 列 | 清洗逻辑 |
|--------|---------|
| `price`, `budget_amount`, `winning_amount`, `min_order_qty` | 移除 `,`（千分位）后转数值 |
| `registered_capital` → `registered_capital_amount_cny` | **注册资本数值化转换**：① 正则提取数值部分（如 `"1000万人民币"` → `1000`）；② 识别中文单位并换算为"元"：`万`→×10000，`亿`→×100000000，`万元`→×10000，`亿元`→×100000000；③ 纯数字视为"元"直接转换；④ 标注为外币（如"美元"）或无法识别格式的，记录 WARNING 日志，`registered_capital_amount_cny` 置为 NULL（后续人工修正）。原始字符串仍保留在 `registered_capital` VARCHAR 列中 |
| 所有字符串列 | `.strip()` 去除首尾空白 |
| 空字符串 / `"N/A"` / `"-"` | 转为 `None`（NULL） |
| 日期列 | 保持字符串格式，MySQL 自动转换 |

### 步骤 4：执行数据导入

**目标**：将 4 个 CSV 文件的数据导入 `ztb_clean`。

**导入顺序**（无强制依赖，但建议按此顺序）：
1. `company_info`（~20MB，主表，含 `uk_credit_code` 去重）
2. `company_penalty`（~338KB，依赖 `company_info` 的 `credit_code` 做外联）
3. `product_info`（~6.6MB）
4. `bid_project`（~6.4MB，含 `uk_project_number` 去重）

**操作**：
```bash
cd d:\DEMO\zhaotoubiao_demo
python scripts/csv_to_mysql.py
```

**验证**：
```sql
SELECT COUNT(*) FROM ztb_clean.company_info;
SELECT COUNT(*) FROM ztb_clean.company_penalty;
SELECT COUNT(*) FROM ztb_clean.product_info;
SELECT COUNT(*) FROM ztb_clean.bid_project;
-- 对比 CSV 行数（减去 header），确认数据完整
```

### 步骤 5：构建 FULLTEXT 索引

**目标**：数据写入完成后，统一添加 FULLTEXT 索引。

**操作**：
```sql
ALTER TABLE ztb_clean.company_info
  ADD FULLTEXT INDEX `ft_company_info` (`company_name`, `business_scope`, `industry`, `address`) WITH PARSER ngram;

ALTER TABLE ztb_clean.company_penalty
  ADD FULLTEXT INDEX `ft_penalty` (`company_name`, `illegal_behavior`, `penalty_result`) WITH PARSER ngram;

ALTER TABLE ztb_clean.product_info
  ADD FULLTEXT INDEX `ft_product` (`product_name`, `supplier_name`, `product_parameters`, `category`) WITH PARSER ngram;

ALTER TABLE ztb_clean.bid_project
  ADD FULLTEXT INDEX `ft_bid_project` (`project_name`, `purchaser`, `successful_bidder`, `subject_matter`) WITH PARSER ngram;
```

**注意**：FULLTEXT 索引构建是 I/O 密集型操作，4 张表的数据量预计总耗时 <5 分钟。建议分批串行执行，每建完一个索引后验证再建下一个。

### 步骤 5b：确认注册资本数值列索引就绪

**目标**：确认 `registered_capital_amount_cny` 的 BTREE 索引已生效（若步骤 2 的 DDL 中已包含 `idx_registered_capital_amount`，则跳过此步；若未包含，则在此处补建）。

**操作**（仅在 DDL 未包含时执行）：
```sql
ALTER TABLE ztb_clean.company_info
  ADD INDEX `idx_registered_capital_amount` (`registered_capital_amount_cny`);
```

**验证**：
```sql
SHOW INDEX FROM ztb_clean.company_info WHERE Key_name = 'idx_registered_capital_amount';
-- 确认 Index_type = BTREE，Column_name = registered_capital_amount_cny
```

### 步骤 6：索引验证

**目标**：通过 `EXPLAIN` 确认 BTREE 和 FULLTEXT 索引均被 MySQL 优化器选用。

**验证 SQL 示例**：
```sql
-- BTREE 索引验证
EXPLAIN SELECT * FROM ztb_clean.company_info WHERE province = '广东';
-- 预期：key = idx_province

-- 注册资本数值索引验证
EXPLAIN SELECT * FROM ztb_clean.company_info
WHERE registered_capital_amount_cny BETWEEN 1000000 AND 100000000;
-- 预期：key = idx_registered_capital_amount，type = range

-- FULLTEXT 索引验证
EXPLAIN SELECT * FROM ztb_clean.product_info
WHERE MATCH(product_name, supplier_name) AGAINST('防水涂料' IN BOOLEAN MODE);
-- 预期：key = ft_product，type = fulltext

-- UNIQUE KEY 验证
EXPLAIN SELECT * FROM ztb_clean.company_info WHERE credit_code = '91330100MA2...';
-- 预期：key = uk_credit_code
```

### 步骤 7：更新环境配置

**目标**：在 `.env` 中新增 `ztb_clean` 数据库连接配置。

**操作**：在 `.env` 中添加：
```
MYSQL_CLEAN_DB=ztb_clean
```
（连接 host、port、user、password 复用现有 MySQL 配置项）

---

## 3. 参与改造的代码文件及改动要点

| 文件 | 性质 | 改动要点 |
|------|------|---------|
| `scripts/schema.sql` | **新建** | 4 张表的完整 DDL：`company_info`、`company_penalty`、`product_info`、`bid_project`。第一阶段仅建 BTREE 和 UNIQUE KEY，FULLTEXT 索引语句以注释形式保留（步骤 5 手动执行）。`company_info` 表在原方案基础上新增衍生列 `registered_capital_amount_cny DECIMAL(20,2)` 及对应的 BTREE 索引 `idx_registered_capital_amount` |
| `scripts/csv_to_mysql.py` | **新建** | CSV → MySQL 导入脚本：流式读取 → 字段清洗 → 批量 INSERT，支持 `ON DUPLICATE KEY UPDATE`。含 `registered_capital` 数值化转换函数（正则提取 + 中文单位换算），导入结束输出行数统计及注册资本解析失败 WARNING 计数 |
| `.env` | **修改** | 新增 `MYSQL_CLEAN_DB=ztb_clean` 配置项 |

**不涉及的文件**：`agent/` 目录下所有文件在本阶段零改动。

---

## 4. 关键命令与脚本说明

### 4.1 DDL 执行

```bash
# 方式一：MySQL 客户端
mysql -h 192.168.10.120 -u root -p < scripts/schema.sql

# 方式二：Python pymysql 执行
python -c "
import pymysql, os
from dotenv import load_dotenv
load_dotenv()
conn = pymysql.connect(host=os.getenv('MYSQL_HOST'), user=os.getenv('MYSQL_USER'),
                        password=os.getenv('MYSQL_PASSWORD'), port=int(os.getenv('MYSQL_PORT', 3306)))
with open('scripts/schema.sql', 'r', encoding='utf-8') as f:
    for stmt in f.read().split(';'):
        if stmt.strip():
            conn.cursor().execute(stmt)
conn.commit()
conn.close()
"
```

### 4.2 数据导入

```bash
cd d:\DEMO\zhaotoubiao_demo
python scripts/csv_to_mysql.py
```

脚本参数（可选）：
- `--csv-dir`：CSV 文件目录，默认 `raw_tables/`
- `--batch-size`：批量 INSERT 行数，默认 5000
- `--truncate`：导入前先 TRUNCATE 目标表（**谨慎使用**）

### 4.3 索引构建

在 MySQL 客户端中逐条执行 §2 步骤 5 的 `ALTER TABLE ... ADD FULLTEXT INDEX` 语句。

### 4.4 快速验证清单

```sql
-- ① 数据库存在
SHOW DATABASES LIKE 'ztb_clean';

-- ② 4 张表存在
SELECT TABLE_NAME, TABLE_ROWS, DATA_LENGTH, INDEX_LENGTH
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'ztb_clean';

-- ③ 索引完整
SELECT TABLE_NAME, INDEX_NAME, INDEX_TYPE
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = 'ztb_clean'
ORDER BY TABLE_NAME, INDEX_NAME;

-- ④ FULLTEXT 可用
SELECT * FROM ztb_clean.company_info
WHERE MATCH(company_name) AGAINST('测试' IN BOOLEAN MODE) LIMIT 1;
```

---

## 5. 前置条件、输入物与产出物

### 前置条件

| 条件 | 说明 |
|------|------|
| MySQL 服务可用 | `192.168.10.120:3306`，具备 CREATE DATABASE / CREATE TABLE 权限 |
| Python 环境可用 | Anaconda Python 3.12，已安装 `pymysql`、`python-dotenv` |
| `.env` 已配置 | 含 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD` |
| CSV 文件就绪 | `raw_tables/` 下 4 个 CSV 文件存在且编码为 UTF-8 |
| MySQL ngram 配置 | `ngram_token_size=2`（如不是，需修改 `my.cnf` 并重启 MySQL） |

### 输入物

| 输入 | 路径 | 说明 |
|------|------|------|
| 企业信息 CSV | `raw_tables/company_info.csv` | ~20MB，含 16 个字段 |
| 企业处罚 CSV | `raw_tables/company_penalty.csv` | ~338KB，含 7 个字段 |
| 产品信息 CSV | `raw_tables/product_info.csv` | ~6.6MB，含 15 个字段 |
| 招标项目 CSV | `raw_tables/bid_project.csv` | ~6.4MB，含 17 个字段 |
| 升级方案文档 | `docs/price_inquiry_sub_route_upgrade_plan.md` | §6 数据导入与索引策略 |

### 产出物

| 产出 | 说明 | 验收标准 |
|------|------|---------|
| `ztb_clean` 数据库 | 全新纯净 MySQL 数据库 | `SHOW DATABASES` 可见 |
| `company_info` 表 | 企业工商信息表，含 FULLTEXT + BTREE 索引（含 `idx_registered_capital_amount`），新增衍生数值列 `registered_capital_amount_cny` | `SELECT COUNT(*)` 与 CSV 行数一致；`EXPLAIN` 确认全部索引命中（含注册资本数值范围查询） |
| `company_penalty` 表 | 企业处罚信息表，含 FULLTEXT + BTREE 索引 | 同上 |
| `product_info` 表 | 产品市场行情表，含 FULLTEXT + BTREE 索引 | 同上 |
| `bid_project` 表 | 招标项目交易记录表，含 FULLTEXT + BTREE 索引 | 同上 |
| `scripts/schema.sql` | 4 张表 DDL 脚本 | 可重复执行（含 `IF NOT EXISTS`） |
| `scripts/csv_to_mysql.py` | CSV 导入脚本 | 可重复执行，支持增量去重 |
| `.env` | 环境变量更新 | 含 `MYSQL_CLEAN_DB=ztb_clean` |

---

> **下一阶段**：确认本阶段全部产出物就绪后，进入 **[下册：检索逻辑改造与测试上线导航报告](./price_inquiry_upgrade_volume2_retrieval_test.md)**。
