# 第三轮统一主 Agent 测评题集

> 数据集编号：`third_round_unified_agent_20260912_reviewed_01`  
> 当前状态：**测评前准备已完成并冻结，等待单独授权正式运行；正式测评尚未执行**  
> 测评边界：当前默认 `unified` 统一主 Agent；每轮独立，不从历史对话提取业务参数

## 一、题集组成

正式主集共 280 题，另有 24 个不计入普通题均值的可靠性场景。

| 主集类别 | 数量 | 主要目的 |
|---|---:|---|
| RAG 回归锚点 | 70 | 检查接入统一 Agent 后的 RAG 质量与精确 Chunk Recall |
| SQL 单工具 | 40 | 核对五个 SQL 工具的选择、参数、记录和空结果 |
| 独立多动作／多主体 | 35 | 检查漏调用、重复调用、主体错配和过早结束 |
| 结果依赖 | 35 | 检查项目中标供应商向企业查询的受控传值 |
| SQL＋RAG | 40 | 检查跨能力任务完整性、证据合并和成本 |
| 澄清／不支持／对抗 | 60 | 检查零外部 I/O、正确停止和发布边界 |
| **合计** | **280** | 不合成单一总分 |

可靠性场景包括：跨轮隔离 8 个、故障恢复 4 个、取消清理 4 个、并发隔离 4 个、依赖与预算故障 4 个。

## 二、文件说明

| 文件 | 用途 |
|---|---|
| `main_cases.jsonl` | 280 道正式主集；每题保存动作依赖图、参数来源、允许结束状态和适用指标 |
| `rag_gold.jsonl` | 110 个 RAG 评分单元；70 个用于 RAG 锚点，40 个用于 SQL＋RAG 复合题 |
| `sql_truth_snapshot.jsonl` | 204 个去重后的 SQL 精确查询真值，保存公开字段、源行 ID、记录哈希和空结果状态 |
| `reliability_scenarios.jsonl` | 24 个跨轮、故障、取消和并发场景，不混入普通题均值 |
| `dataset_schema.json` | 数据结构说明；最终约束以离线验证器为准 |
| `manifest.json` | 题量、工具动作数、数据库身份、来源哈希和产物哈希 |
| `verification_report.json` | 最近一次离线结构校验结果 |
| `build_third_round_dataset.py` | 可复现构建脚本；只读 MySQL，不调用 LLM、Milvus 或付费接口 |
| `verify_dataset.py` | 离线一致性、引用和哈希校验 |
| `人工审核清单.md` | 逐题审核入口和发布门禁 |
| `human_review_record.json` | 审核人、日期、覆盖ID和被审核内容哈希 |
| `formal_thresholds.json` | 已冻结的系统、RAG、SQL与可靠性正式阈值 |
| `formal_run_config.json` | 正式题量、重复次数、并发、启动间隔、超时与容量预算 |
| `formal_authorization_record.json` | 最终人工授权状态；准备完成时保持 `pending` |
| `record_formal_authorization.py` | 后续收到明确授权时同步记录授权，不会自动启动测评 |
| `formal_readiness_20260912_01.json` | 测评前证据和相关源文件的最终哈希快照 |
| `live_data_preflight_20260912_01.json` | 当前SQL与Milvus身份只读核对记录 |
| `judge_variance/judge_variance_s4plus1_20260912_01/` | 冻结S4同110题的裁判重复评分与波动报告 |
| `record_human_review.py` | 将人工审核结论绑定到当前题集内容并刷新清单哈希 |
| `../scripts/run_third_round_eval.py` | 统一入口；分离离线预检、真实采集、离线评分、付费Ragas评分、可靠性场景和报告生成 |
| `../evaluation/unified/` | 第三轮数据加载、可恢复采集、确定性评分、可靠性故障注入和报告实现 |
| `executor_preflight.json` | 最近一次离线执行器预检结果；不代表真实服务兼容性通过 |

## 三、主集字段口径

每一题的 `expected.action_graph` 是金标准动作依赖图，而不是强制的线性工具顺序：

- 没有依赖的动作可以按任意顺序执行；
- `depends_on` 声明必须先完成的动作；
- `current_user` 参数必须逐字存在于本轮 `query`；
- `prior_verified_result` 只允许引用同一次请求内已经核验的上游字段；
- 主集全部 `history=[]`，跨轮只在独立可靠性场景中测试；
- 负向题的动作图为空，`external_io.max_calls=0`。

RAG 的参考答案和标准 Chunk 不重复塞入每道复合题，而是由 `rag_gold_ref` 指向 `rag_gold.jsonl`。SQL 真值同理由 `sql_truth_ref` 指向冻结快照。

## 四、RAG 锚点选择

RAG 锚点从已审核的 S4 200 题中选取，单跳 35 题、多跳 35 题。优先纳入：

- S4 精确 Chunk 完全漏检题；
- S4 只命中部分标准 Chunk 的题；
- 动态过滤实际改变引用集合的题；
- S4 长尾耗时前 20 题；
- 为保持单跳／多跳各 35 题而加入的分层补样。

SQL＋RAG 另外使用 40 个不与70个锚点重复的 S4 评分单元，单跳和多跳各20个。Macro Recall 与 Micro Recall 都按 `ground_truth_chunk_ids` 计算，漏调用 RAG 的正向题记0。

## 五、SQL 真值边界

SQL 真值于构建时对当前 `ztb_clean` 执行精确等值、只读查询并冻结：

- 工具输出允许的公开字段与当前严格 SQL 适配器一致；
- 运行时记录顺序不作为正确性要求，按记录内容或哈希进行无序核对；
- 默认快照上限为20条，最终发布仍必须说明有限样本，不能声称穷尽完整数据库；
- “无匹配”代表查询成功但结果为空，不能与数据库失败混淆；
- 数据库发生变化后，不得继续把旧快照当作当前真值，应重建并重新审核受影响题。

数据库地址、账号和密码没有写入题集。`manifest.json` 仅记录库名、表行数、最大主键和最大创建时间等非凭据身份信息。

## 六、当前验证状态

已完成的离线机械检查：

- 主集恰好280题，六类配额正确；
- 280个问题无完全重复；
- 六个授权工具全部出现；
- 所有本轮参数均能回到当前问题或合法上游结果；
- 110个RAG引用和204个SQL真值引用均无孤儿、无缺失；
- 主集不存在非空历史；
- 24个可靠性场景配额正确；
- 全部冻结产物哈希匹配。
- 280道主集、110条RAG Gold、204条SQL真值和24个可靠性场景由 `zz` 于2026-09-12审核通过；
- 正式指标阈值已经冻结，A8通过线为95%，D1～D6首次运行只报告、不否决。
- 当前SQL三表身份与冻结快照一致；Milvus共920条，118个唯一标准Chunk逐字匹配；
- Ragas 0.4.3专用环境、正式并发／节流、可恢复断点和容量预算已经冻结。

执行器已完成离线实现与测试，包括可恢复采集、动作依赖图核对、SQL真值核对、Exact Chunk Macro／Micro Recall、Ragas输入生成、可靠性故障注入和分层报告。D5组件级Token／费用当前统一运行时没有完整暴露，因此执行器明确记录为 `unavailable`，不使用估算值冒充实测值。

### 真实小样本结果

2026-09-12执行 `third_round_smoke_20260912_01`，选择六类各1题，覆盖六个授权工具和8个必要动作。6题均获得终态（5个 `complete`、1个预期内 `clarify`），真实模型、MySQL、Milvus和六工具在该有限样本上的链路兼容性通过。

- A1红线事件：0；A3、A5～A10以及C1～C5在适用样本上均为100%；
- 两个RAG单元的Exact Chunk Macro Recall和Micro Recall均为100%；
- A2首次为14/16，即87.5%。原因是 `TR-CROSS-005` 首轮同时提出两个工具调用，规则层拒绝两次后，模型改为逐个调用并完成任务；
- B1～B4尚未执行付费Ragas评分，因此本次主集判定为 `incomplete`，不能当作正式通过；
- 采集后修复了负向题A3分子误计问题，并使用带独立哈希的新评分器重新评分。报告明确标记评分器与采集计划不一致；正式运行禁止这种情况；
- 完整请求D1 P50为2.883秒、P95为7.500秒；样本仅6题，不作为稳定性能基线。

收紧提示后，`third_round_smoke_a2_retest_20260912_01` 对同一高风险题连续复测3次，每次A2均为3/3（100%），且没有放宽规则层或计分口径。`third_round_reliability_smoke_20260912_01` 覆盖五类可靠性场景各1个，5/5通过。冻结S4同110题的Ragas重复评分、完整准备测试和代码／证据哈希记录见正式就绪文件。

裁判稳定性两次总体值分别为：B1 `0.969394 / 0.980628`、B2 `0.871010 / 0.864571`、B3 `0.817819 / 0.837570`、B4 `0.961130 / 0.972612`，两次均过冻结阈值。总体过线不代表逐题无波动，正式报告仍须保留逐题分数。新增一次110题评分实耗1,532次API调用和4,036,398 Token，正式330个RAG单元据此按约4,596次调用、12,109,194 Token规划，并冻结5,500次调用／15,000,000 Token容量上限。

尚未完成的只有正式测评本身。`formal_run_authorized` 仍为 `false`；这不是技术准备缺失，而是刻意保留的最终人工门禁。

## 七、离线命令

从项目根目录执行：

```powershell
# 重建：读取S4冻结材料和当前MySQL真值；不调用付费接口
python third_round_eval_data\build_third_round_dataset.py

# 校验：不访问数据库和外部接口
python third_round_eval_data\verify_dataset.py

# 离线预检：不访问数据库、Milvus或模型
python scripts\run_third_round_eval.py preflight --run-kind smoke

# 真实小样本预演模板：必须显式允许真实调用，且结果不得计入正式成绩
python scripts\run_third_round_eval.py collect --run-id <smoke_run_id> --run-kind smoke --limit 4 --allow-live
python scripts\run_third_round_eval.py score --run-id <smoke_run_id>

# 付费Ragas评分与可靠性场景有各自独立的显式开关
python scripts\run_third_round_eval.py ragas --run-id <smoke_run_id> --allow-paid-scoring
python scripts\run_third_round_eval.py reliability --run-id <smoke_run_id> --run-kind smoke --limit 1 --allow-live

# 正式运行模板（授权前会被程序拒绝；必须使用专用评测环境）
.venv-eval\Scripts\python.exe scripts\run_third_round_eval.py preflight --run-kind formal --require-live-configuration --verify-live-data
.venv-eval\Scripts\python.exe scripts\run_third_round_eval.py collect --run-id third_round_formal_20260912_01 --run-kind formal --repeats 3 --minimum-start-interval 10 --allow-live
.venv-eval\Scripts\python.exe scripts\run_third_round_eval.py score --run-id third_round_formal_20260912_01
.venv-eval\Scripts\python.exe scripts\run_third_round_eval.py ragas --run-id third_round_formal_20260912_01 --concurrency 1 --metric-timeout 600 --allow-paid-scoring
.venv-eval\Scripts\python.exe scripts\run_third_round_eval.py reliability --run-id third_round_reliability_formal_20260912_01 --run-kind formal --repeats 3 --allow-live
```

真实采集和付费评分不会由预检命令隐式触发。正式采集还会检查 `formal_run_authorized=true`、完整280题和连续3次运行；当前授权仍为 `false`。

重建会刷新 SQL 真值与所有产物哈希，并将数据集恢复为待审核状态，因此只应在明确需要更新数据库基线时执行。`human_review_record.json` 使用排除审核注记后的内容哈希绑定本次审核对象；任何问题、金标准或可靠性场景变化都必须重新审核。
