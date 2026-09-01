# L6 单书链路验证报告（2026-09-01）

> 验证对象：book1《招标投标法律解读与风险防范实务（白如银）》，774 页，6.3MB。
> 验证脚本：`scripts/pdf_l6_single_book_verify.py`（可重跑，Tier C 命中缓存）。
> 计划基线：`pdf_legal_tiered_routing_execution_plan_20260831.md` §8（单书缩减版）。
> 结论：**PASS ✅**（修复 2 个 L6 验收暴露的 bug 后三门全绿）。

---

## 一、验证目标

用户核心诉求：**本地快路径处理简单页 + 云端 MinerU 处理复杂页 + 按页序拼回一个完整文档**。本轮用一个真实整本书验证这条链路端到端成立，并核验计划 §8.2 的核心质量门槛。

## 二、环境与前置

- 本地运行环境：项目 `.venv`（langchain-core 1.5.4 / pymupdf 1.28.2），uv 管理。
- 三档开关：`.env` 中 `PDF_TIERED_ROUTING_ENABLED=true`（运行时开启；代码默认值仍为 `false`，待验收后调整）。
- 网络：**阿里云安全组未放行 8002**（22 通，8002/6335 被过滤），本地经 SSH 隧道 `127.0.0.1:18002 → 服务器 127.0.0.1:8002` 访问；客户端 `NO_PROXY=127.0.0.1,localhost` 绕开 Windows 系统代理（`127.0.0.1:7890`，曾导致 httpx 走代理返回 502）。

## 三、结果（两轮对比）

### 第一轮（修复前，34 次远程 MinerU 调用，1137s）

| 项 | 值 |
| --- | --- |
| 路由分布 | tier: `{'': 34, 'A': 723}`；label: `{'': 34, 'text': 722, 'two_col_text': 1}` |
| 复杂范围 | 34 个，共 51 页 |
| 页标记 | 757 个，**重复 33**，预期 757 |
| 章节标题保留率 | 100%（9/9）✓ |
| 拼接产物 | 534,510 字符 |
| 门禁 | **FAIL**：G1_integrity=False（重复标记 + manifest 空 tier 行） |

### 修复后（全缓存命中，0 次远程调用，25.6s）

| 项 | 值 |
| --- | --- |
| 路由分布 | tier: `{'C': 34, 'A': 653}`；label: `{'tier_c_range': 34, 'text': 653}` |
| 复杂范围 | 34 个，共 51 页（同第一轮） |
| 页标记 | 687 个，有序，**重复 0** ✓ |
| 章节标题保留率 | **100%（9/9）** ✓（≥98% 达标） |
| Tier C 落位 | 34/34 范围 marker 存在 + 段落非空 ✓ |
| 页眉页脚残留 | 0 条 ✓ |
| 警告汇总 | `cache_hit: 34`（幂等重跑验证通过） |
| 拼接产物 | 493,772 字符（去重后 −7.6%） |
| 门禁 | **PASS ✅**：G1 ✓ G2 ✓ G3 ✓ |

## 四、验收暴露并修复的 bug（L6 的价值）

### Bug 1（严重）：边界扩展页被快路径重复解析 → 文档内容重复
- **现象**：`aggregate_complex_ranges` 把相邻 Tier A 页扩进 Tier C 范围（如范围 001 = 页[13,14,15]，13/15 是 A 类边界页），但 `pdf_router._dispatch_all` 派发 Tier A 时**未排除这些页** → 同一页内容在最终文档出现两份（快路径版 + MinerU 版），页标记重复 33 处。
- **违反设计**：计划 §6 T3「范围内页统一使用 MinerU 产物，避免同一页同时出现快路径和 MinerU 内容」。
- **修复**（`public_kb/ingestion/transforms/pdf_router.py`）：派发 Tier A 前剔除 `tier_c_page_set`（所有范围页）；补填范围产物的 `route`（`MinerURouter` 返回 `route=None`，注释写明由编排器填入，此前从未执行）。
- **回归测试**：`test/test_pdf_router.py::test_tier_a_excludes_range_covered_pages_and_fills_route`（23/23 通过）。

### Bug 2（轻微）：manifest 范围行 tier 为空
- 修复同上（编排器补填 `PageRouteDecision(tier=C, label=tier_c_range, reason=pages=...)`），manifest 每页可溯源目标达成。

## 五、已知观察（非阻塞，记录待办）

1. **路由偏保守（校准项）**：book1 实际 653/774 页（84%）走本地快路径；34 范围覆盖 51 页（6.6%）走 MinerU。L0 画像 book1 = text 93.4% / 双栏 6.3% / 扫描 0.3%，双栏页大多被判 `uncertain→C`（双栏置信度阈值 0.80 对 book1 多数双栏页不达标）——正是 L1 已记录的「偏保守」倾向在整本上的量化。计划 §11 明确「高风险页宁可多送 MinerU」，本轮不调阈值，留作 golden set 校准项。
2. **条款连续性指标失效（测量方法局限）**：报告项「条款连续性」返回 0，因检测脚本只匹配行首 `第X条`，而产物中条款多为 `#### 第X条` 标题行。非数据缺陷，需改为标题感知匹配后才有意义。
3. **范围 005（页 86–100，15 页）→ 9,615 字符**，占全部 Tier C 内容约 1/4，建议人工抽检该范围。
4. **冗余缓存层**：每个范围同时被 `MinerURouter._LocalCache` 与 `MinerUApiParser._write_cache` 各写一份（本轮 68 个 `.md`）；后者 key 用子 PDF 字节哈希（含 pymupdf 时间戳）恒 miss，属冗余死层，建议后续清理 `MinerUApiParser` 内部缓存。
5. **服务器网络与安全**：安全组未放行 8002（本地需隧道）；同机 19530/9091（他组 Milvus）公网可达，建议收敛；`/tmp` 残留 `smoke*.pdf`、`api_parse.json`、`container_e2e.py` 等上会话测试文件（不确定归属，未删）。

## 六、结论与下一轮

- 本地快路径 + 云端 MinerU + 拼接回完整文档的链路在真实整本书上**成立且达标**（章节保留 100%、无重复、可溯源）。
- 缓存幂等（25.6s 重跑）与 fail-fast 语义已验证。
- **下一轮**：① 三个质量报告项修正（标题感知条款连续性）后并入默认验收；② Milvus 入库 + 106 题检索/引用回归（对比全量 MinerU 基线），需本地起 Milvus + embeddings；③ 人工抽检范围 005 与双栏样例页；④ 全绿后再把 `pdf_tiered_routing_enabled` 代码默认值改 `true`。
