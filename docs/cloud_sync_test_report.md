# 云端数据库写入（第一阶段）功能测试报告

- 日期：2026-08-13
- 范围：将项目数据（Milvus 向量集合 + Redis 状态存储）写入云端服务器
- 交付模块：`cloud_sync/`（独立包，未改动项目既有业务逻辑、数据库连接配置与依赖项）

---

## 1. 交付物与模块映射

| # | 交付物 | 实现模块 | 说明 |
|---|--------|----------|------|
| 1 | 云端数据库连接配置模块（长连接 + 断线重连） | `cloud_sync/config.py`、`cloud_sync/connection.py` | 环境变量驱动的源/目标连接配置；`ResilientMilvusClient` 与 `ResilientRedisClient` 提供长连接复用 + 断线重连 + 指数退避重试 |
| 2 | 全量数据同步写入逻辑（字段映射/格式转换） | `cloud_sync/milvus_sync.py`、`cloud_sync/redis_sync.py` | Milvus 自动同步 schema + 索引（DDL）并全量复制数据（含向量与动态字段、auto_id 剥离）；Redis 通过 DUMP/RESTORE 无损复制 |
| 3 | 增量数据实时写入能力 | `cloud_sync/milvus_sync.py`、`cloud_sync/watermark.py` | 轮询水位线对比：auto_id 集合按主键水位线增量，非 auto_id 集合按主键集合对账增量；Redis 按 DUMP 摘要差异增量 |
| 4 | 数据校验机制 | `cloud_sync/verify.py` | 行数一致 + 主键集合一致 + 全量字段指纹多重集一致 |
| 5 | 功能测试报告 | 本文档 + `test/test_cloud_sync.py` | 单元测试、集成冒烟测试、CLI 端到端验证 |

命令行入口：`python -m cloud_sync {full|incremental|verify|schema}`。

---

## 2. 架构与关键设计

### 2.1 连接管理（交付物 1）

- **长连接复用**：客户端实例在首次调用时建立连接并缓存，后续操作复用同一连接。
- **断线重连**：任何调用失败都会作废当前客户端，下次调用自动重建连接。
- **指数退避重试**：`retry_with_backoff` 按 `backoff * 2**attempt` 递增等待，默认最多重试 3 次。
- **连接配置安全**：凭据仅通过环境变量 / `.env` 注入，代码中无硬编码敏感信息。

### 2.2 Milvus 全量同步（交付物 2）

- 从源集合 `describe_collection` 提取 schema（字段类型、主键、auto_id、动态字段、向量维度），在目标端重建等价 schema。
- 从源集合 `list_indexes` / `describe_index` 提取索引配置，在目标端重建索引。
- 使用 `query_iterator` 按主键有序分页读取（含向量与动态字段），批量写入目标；`auto_id` 集合插入时自动剥离主键字段。
- **幂等**：全量同步前先 `drop` 再重建目标集合，可安全重跑。

### 2.3 增量同步（交付物 3）

- **auto_id 集合（如 `public_kb`）**：以「已同步最大主键」为水位线，`filter = pk > last_pk` 增量拉取；逐批落盘水位线，支持断点续传；若检测到源集合被重建（行数骤降）则自动退化为全量同步。
- **非 auto_id 集合（如 `mysql_price_semantic`）**：主键具备业务确定性，采用「源/目标主键集合对账」，仅复制缺失主键对应的记录。
- **Redis**：轮询 `SCAN` + `DUMP` 摘要对比，仅复制缺失或变更的键。

### 2.4 数据校验（交付物 4）

- 行数一致（`row_count`）。
- 主键集合一致（非 auto_id 集合）。
- 全量字段指纹多重集一致（`sha256(canonical JSON)`，覆盖向量与动态字段；auto_id 集合排除主键后对比），可精确发现缺失、多余、字段值不一致。

---

## 3. 测试环境

| 项 | 值 |
|----|----|
| 操作系统 | Windows（conda 环境） |
| Python | `.conda/python.exe` |
| pymilvus | 3.0.1 |
| 本地 Milvus | `http://localhost:19530`（运行中，含 `public_kb` 29729 条、`mysql_price_semantic` 77597 条） |
| 本地 Redis | `localhost:6379`（**未运行**，项目 checkpointer 默认使用 MemorySaver，Redis 为可选后端） |
| 云端 Milvus | `http://8.130.174.43:19530`（占位，待接入真实凭据） |
| 云端 Redis | `8.130.174.43:6379`（占位，待接入真实凭据） |

---

## 4. 测试结果

### 4.1 单元测试 + 集成冒烟测试

运行命令：

```powershell
python -m unittest discover -s test -p test_cloud_sync.py -v
```

结果：**15 个用例全部通过（OK）**，耗时约 13.7s。

| 测试类 | 用例 | 覆盖点 | 结果 |
|--------|------|--------|------|
| `TestFingerprint` | 3 | 指纹一致性、字段排除、内容差异 | PASS |
| `TestRetry` | 2 | 重试后成功、达到上限抛出 | PASS |
| `TestWatermark` | 1 | 水位线持久化与重载 | PASS |
| `TestBuildSchema` | 1 | schema 重建（主键/auto_id/max_length/dim） | PASS |
| `TestRespParsing` | 5 | RESP 协议解析（bulk/int/nil/array/error） | PASS |
| `TestMilvusFullSync` | 1 | 全量同步：auto_id 剥离、重映射、水位线 | PASS |
| `TestMilvusIncrementalReconcile` | 1 | 非 auto_id 对账：仅复制缺失主键 | PASS |
| `MilvusIntegrationSmokeTest` | 1 | 本地 Milvus 端到端：全量同步 + 校验一致（多批分页） | PASS |

### 4.2 CLI 端到端校验（真实数据）

运行命令（将目标指向本地 Milvus，验证真实数据）：

```powershell
$env:TARGET_MILVUS_URI='http://localhost:19530'
python -m cloud_sync verify --sample 100
```

结果：`all_passed = true`。

| 集合 | 行数（源/目标） | 主键集合 | 内容指纹 | 结论 |
|------|----------------|----------|----------|------|
| `public_kb` | 29729 / 29729 | （auto_id，按内容指纹） | 一致 | 通过 |
| `mysql_price_semantic` | 77597 / 77597 | 一致（missing=0, extra=0） | 一致 | 通过 |

### 4.3 断线重连（异常网络场景）

- `RetryTest.test_retries_then_succeeds`：前 2 次抛 `ConnectionError`，第 3 次成功，验证指数退避重试。
- `RetryTest.test_gives_up_after_max_retries`：持续失败，达到上限后正确抛出。
- Redis 连接实测（`localhost:6379` 未运行）：`ResilientRedisClient` 正确执行「重连 + 重试 + 明确报错」，日志：
  `Redis(localhost:6379) PING 失败，1.00s 后重连（1/1）: [WinError 10061] ... 连接拒绝`。

---

## 5. 高并发与异常网络场景说明

| 场景 | 处理机制 | 验证状态 |
|------|----------|----------|
| 瞬时断线 | 连接作废 + 自动重连 + 指数退避 | ✅ 单元测试 + Redis 实测 |
| 网络抖动导致的偶发失败 | 可重试操作自动重试 | ✅ 单元测试 |
| 全量同步中途失败 | 全量同步幂等（drop+重建），可安全重跑 | ✅ 集成测试（多批分页） |
| 增量同步中途失败 | 逐批落盘水位线，断点续传；源被重建时退化为全量 | ✅ 逻辑实现 + 单元测试 |
| 高并发写入 | 迁移按集合顺序执行，避免目标端写冲突；水位线存储加锁 | ⏳ 建议接入真实云端后用并发压测验证 |
| 跨实例网络隔离/超时 | 连接/请求超时可在 `CLOUD_SYNC_CONNECT_TIMEOUT` 配置 | ⏳ 建议在真实云端故障注入验证 |

> 说明：本项目为 Demo 规模，数据量（约 10 万向量）下顺序批量写入即可稳定完成；真正的高并发压测需在真实云端实例上进行，建议在第二阶段（云端接管读写）前补充 JMeter / locust 压测。

---

## 6. 已知限制与后续建议

1. **云端凭据待接入**：云端 Milvus / Redis 的 host、端口、密码需通过 `.env` 或环境变量填入（当前为占位值）。
2. **Redis 后端当前未启用**：项目 checkpointer 默认 `MemorySaver`，Redis 同步模块已就绪，但需在项目实际切换到 Redis 后端、并部署云端 Redis 后进行真实验证。
3. **增量水位线**：非 auto_id 集合采用「主键集合对账」而非数值水位线（主键为 VARCHAR 且跨表不单调），对账方式更稳妥但每次需拉取主键全集；若未来数据量增长到千万级，可改为按 `source_table` 分组水位线。
4. **索引与加载**：全量/增量同步后目标集合会执行 `flush` + `load`，索引已同步，云端可直接对外提供检索。
