# raw_tables 结构化数据入库工作总结报告

> **执行日期**：2026-09-04
> **所属方案**：[raw_tables结构化数据入库方案_20260904.md](../design_docs/raw_tables结构化数据入库方案_20260904.md)
> **状态**：✅ 全部完成（3 表干净重建 + P0-11 索引 + 全量验收通过）
> **决策口径**：3 张表（product_info 退役）/ 干净重建 / `mysql_price_semantic` 不重建（代码保留缺席运行）

---

## 一、执行结果总览

| 阶段 | 内容 | 结果 |
|------|------|------|
| Phase 0 | CSV 预检 | ✅ 3 表行数/列名与历史基线完全一致；清洗后 credit_code 零真实重复 |
| Phase 1 | 备份旧卷 + 容器初始化 | ✅ `ztb_mysql` healthy；MySQL 8.0.46；`ngram_token_size=2`；init 自动建 3 表 |
| Phase 2 | 数据导入 | ✅ 38,911 / 1,805 / 17,742 行，零合并；注册资本解析率 98.0%（与 2026-08-07 基线一致） |
| Phase 3 | FULLTEXT 索引（P0-11） | ✅ 3 个 ngram FULLTEXT 全部建成，列集合与 `_HARDCODED_SCHEMA` 严格一致 |
| Phase 4 | 验收 | ✅ EXPLAIN 全命中 + 冒烟查询真实取数正确 |
| Phase 5 | 语义集合确认 | ✅ 集合缺席 + 自举开关默认关 + 退化路径安全 |
| Phase 6 | 文档同步 | ✅ CLAUDE.md 已修正；本报告 |

---

## 二、关键执行记录

### Phase 0：CSV 预检

- DictReader（`utf-8-sig`）真实行数：company_info 38,911 / company_penalty 1,805 / bid_project 17,742，与历史基线（2026-08-07 执行报告）完全一致；列名与 DDL 逐一对齐
- 空值特征：`credit_rating` 99.9% 空（历史已知）、`city` 14.1%、`credit_code` 7.1%（含 '-'/'N/A' 脏值）
- **去重口径澄清**：原始值层面 apparent 重复 2,753 个，经标准清洗（''/'N/A'/'-'/'NULL'/'null' → NULL）后 credit_code **零真实重复**；UNIQUE 键下最终行数 = CSV 行数（2,755 行 NULL credit_code 依 MySQL 语义允许多行共存）
- `bid_project.project_number` 17,742 全非空零重复

### Phase 1：环境重建

- 旧数据卷（893MB，2026-08-17 状态）整目录改名备份：`docker/mysql/mysql_data_backup_20260904/`，未启动旧容器，物理零风险；已加入 `.gitignore`
- **镜像拉取问题与解决**：本机网络直连 Docker Hub 不通（registry-1.docker.io 超时 000），daemon 无 mirror 配置；改经 `docker.m.daocloud.io` 拉取 mysql:8.0.46 后 `docker tag` 回官方名（未改 daemon.json、未重启 Docker，Milvus 容器不受影响）
- init 自动执行 `01-schema.sql`：仅建 3 表（product_info 不再建表），仅 BTREE + UNIQUE，无 FULLTEXT（后置）

### Phase 2：导入

`scripts/csv_to_mysql.py`（自 archive 恢复 + 参数化）流式导入，约 8 秒完成：

| 表 | imported | DB rows | 差异 |
|----|---------|---------|------|
| company_info | 38,911 | 38,911 | 0（无 UNIQUE 合并，与预检一致） |
| company_penalty | 1,805 | 1,805 | 0 |
| bid_project | 17,742 | 17,742 | 0 |

注册资本数值化：38,150 已解析（98.0%）/ 761 NULL（2.0%，全部源于原始空值）——与历史基线完全一致。

### Phase 3：FULLTEXT 索引（P0-11 规格）

| 表 | 索引名 | 列 | 验证 |
|----|--------|-----|------|
| company_info | `ft_company_info` | company_name, business_scope, industry, address | EXPLAIN fulltext ✓ |
| company_penalty | `ft_penalty` | company_name, illegal_behavior, penalty_result | EXPLAIN fulltext ✓ |
| bid_project | `ft_semantic` | **恰好 purchaser, successful_bidder（2 列）** | EXPLAIN fulltext ✓ |

### Phase 4：验收明细

| 验证项 | 结果 |
|--------|------|
| 行数对账 | 3 表 = CSV 行数，零差异 ✓ |
| BTREE `province='广东省'` | key=idx_province ✓ |
| 注册资本 range（1M~1亿宽区间） | key=NULL 全表扫——**优化器合理决策**（区间覆盖绝大多数行）；窄区间复核 key=idx_registered_capital_amount（range + Using index）✓ |
| UNIQUE credit_code / project_number（真实值） | type=const 双双命中 ✓ |
| FULLTEXT 三表 | type=fulltext 三路全命中 ✓ |
| 冒烟：'安防监控' 企业检索 | 返回 3 条中文正常 ✓ |
| 冒烟：'串通投标' 处罚检索 | 返回 3 条 ✓ |
| 冒烟：'福建师范大学' 采购人检索 | 返回 3 条结果公告 ✓ |
| 冒烟：credit_code 联查 penalty↔company_info | JOIN 有数 ✓ |
| 冒烟：注册资本 ≥10 亿 | 5,002 家 ✓ |

### Phase 5：语义集合缺席确认

1. Milvus 当前集合仅 `public_kb`，`mysql_price_semantic` 不存在 ✓
2. `.env` 未设置 `ENABLE_AUTO_SEMANTIC_BOOTSTRAP`（默认 false），agent 启动不会隐式重建 ✓
3. 退化路径（`_semantic_recall_candidates` 空候选 → 零 SQL）为代码支持的安全状态 ✓

---

## 三、文件改动清单

| 文件 | 改动 |
|------|------|
| `scripts/csv_to_mysql.py` | **恢复**（自 `scripts/archive/`）：默认 3 表、`--tables` 可覆盖、连接配置改读 `.env`（保留 4 表能力） |
| `docker/mysql/init/01-schema.sql` | **重写为唯一权威源**：3 表；移除 product_info 建表；移除原无 ngram 的 `ft_penalty_semantic`；P0-11 FULLTEXT 规格以注释记录 |
| `scripts/schema.sql` | 改为指向 01-schema.sql 的废弃存根（双源合一） |
| `scripts/deploy_ztb_clean.ps1` | FULLTEXT DDL 更新为 P0-11 规格（3 表、去反引号规避 PowerShell here-string 转义歧义）；csv_to_mysql.py 路径随恢复自动修复 |
| `CLAUDE.md` | ztb_clean 表清单（3 表）与 Milvus 集合状态（语义集合退役）描述修正 |
| `.gitignore` | 增加 `docker/mysql/mysql_data_backup_*/` |
| `design_docs/raw_tables结构化数据入库方案_20260904.md` | 决策定稿 + 本报告 |

**未改动**：`.env`（本就指向本地 Docker）、`agent/` 全部代码（零改动）。

---

## 四、遗留说明

1. **回滚资产**：`product_info.csv` 留档于 `raw_tables/`；如需恢复产品查询功能线，建表 DDL 在方案文档 §1.1 引用的历史 schema 中，导入用 `python scripts/csv_to_mysql.py --tables product_info`（需先按旧 DDL 建表）。
2. **旧数据卷**：`docker/mysql/mysql_data_backup_20260904/`（893MB）保留在原地，确认新库稳定运行一段时间后可手动删除。
3. **镜像拉取**：本次经 daocloud 镜像源拉取；如后续仍需频繁拉取官方镜像，可考虑在 daemon.json 配置 registry mirrors（需重启 Docker，影响所有容器，未擅自操作）。
4. **语义召回**：如未来需要恢复，单独执行 `python scripts/rebuild_mysql_semantic_collection.py`（期望行数 ≈ 58,458），属于新决策（见方案 §Q3）。
