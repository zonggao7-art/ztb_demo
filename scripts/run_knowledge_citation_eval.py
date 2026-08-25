"""
知识库引用溯源全量测评脚本。

读取 testset_knowledge.jsonl（法规类专业问题集），逐条经 AgentGraph.invoke()
走完整链路（router → knowledge_qa），验证每条结果：

  1. 规则校验（引用溯源规则 R1-R7，来自 citation_validation）；
  2. 关联校验（association_check）：对每条 citation 用 chunk_id 回表 Milvus，
     比对原文文本一致（无"错误关联"/张冠李戴）、chunk_uid 口径一致；
  3. 拒答正确率：expect_refusal 样本在 knowledge_qa 分支应 is_refusal=true。

结果增量写入 test_report/knowledge_citation_results.jsonl（支持断点续跑），
运行结束后自动生成汇总报告 test_report/knowledge_citation_report.md。

用法：
    python scripts/run_knowledge_citation_eval.py               # 全量
    python scripts/run_knowledge_citation_eval.py --limit 10    # 冒烟
    python scripts/run_knowledge_citation_eval.py --no-resume   # 清空重跑
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from eval_common import load_cases, load_done_ids  # noqa: E402

TESTSET = PROJECT_ROOT / "testset_knowledge.jsonl"
OUT_DIR = PROJECT_ROOT / "test_report"
OUT_FILE = OUT_DIR / "knowledge_citation_results.jsonl"
REPORT_FILE = OUT_DIR / "knowledge_citation_report.md"

REFUSAL_MARKERS = (
    "无法回答", "无法提供", "无法直接回答", "未能找到", "暂无相关", "不能提供",
    "没有相关规定", "未找到", "不包含", "未包含", "不在知识库", "建议查阅",
)


def _is_refusal_answer(answer: str) -> bool:
    """语义拒答识别：检索非空但 LLM 明确表示无法基于资料回答。"""
    return any(m in (answer or "") for m in REFUSAL_MARKERS)


class _AssociationChecker:
    """用 Milvus 回表校验引用与实体的关联正确性。"""

    def __init__(self, collection_name: str) -> None:
        from public_kb.config import Settings
        from pymilvus import MilvusClient

        settings = Settings()
        uri = f"http://{settings.milvus_host}:{settings.milvus_port}"
        self._collection_name = collection_name
        self._client = MilvusClient(uri=uri)
        self._cache: dict[int, dict] = {}

    def _get_entity(self, chunk_id: int):
        if chunk_id not in self._cache:
            try:
                rows = self._client.get(
                    self._collection_name,
                    ids=[chunk_id],
                    output_fields=["*"],
                )
                self._cache[chunk_id] = rows[0] if rows else None
            except Exception as e:  # noqa: BLE001
                print(f"  [warn] 回表查询 chunk_id={chunk_id} 失败: {e}", flush=True)
                self._cache[chunk_id] = None
        return self._cache[chunk_id]

    def check(self, citations: list[dict]) -> dict:
        """逐条回表校验：原文一致 + chunk_uid 口径一致 + 行存在。

        Returns:
            {"checked": n, "passed": n, "failed": [...], "duplicates": [...]}
        """
        from public_kb.chunk_ids import compute_chunk_uid

        failed, seen_uid_groups = [], {}
        checked = 0
        for c in citations:
            checked += 1
            cid = c.get("chunk_id")
            if cid is None:
                failed.append({
                    "context_index": c.get("context_index"),
                    "reason": "missing_chunk_id",
                })
                continue
            entity = self._get_entity(int(cid))
            if entity is None:
                failed.append({
                    "context_index": c.get("context_index"),
                    "chunk_id": cid,
                    "reason": "chunk_not_found",
                })
                continue
            entity_text = entity.get("text", "")
            if entity_text != c.get("text"):
                failed.append({
                    "context_index": c.get("context_index"),
                    "chunk_id": cid,
                    "reason": "text_mismatch",
                })
            # chunk_uid 口径一致：回表实体按同一函数重算
            expect_uid = compute_chunk_uid(entity_text, entity)
            if expect_uid != c.get("chunk_uid"):
                failed.append({
                    "context_index": c.get("context_index"),
                    "chunk_id": cid,
                    "reason": "chunk_uid_mismatch",
                    "expected_uid": expect_uid,
                    "actual_uid": c.get("chunk_uid"),
                })
            seen_uid_groups.setdefault(c.get("chunk_uid"), []).append(cid)

        # 同内容重复行检测（同一 chunk_uid 对应多个 chunk_id → 库内重复数据）
        duplicates = [
            {"chunk_uid": uid, "chunk_ids": ids}
            for uid, ids in seen_uid_groups.items()
            if len(ids) > 1
        ]
        return {
            "checked": checked,
            "passed": checked - len(failed),
            "failed": failed,
            "duplicate_groups": duplicates,
        }


def extract_record(sample: dict, result: dict | None, elapsed_s: float,
                   error: str | None) -> dict:
    biz = (result or {}).get("business_result", {}) or {}
    data = biz.get("data") or {}
    return {
        "sample_id": sample["sample_id"],
        "category": sample.get("category"),
        "difficulty": sample.get("difficulty"),
        "expect_refusal": sample.get("expect_refusal", False),
        "elapsed_s": round(elapsed_s, 4),
        "branch": biz.get("branch", "exception"),
        "intent": result.get("intent", "unknown") if result else "unknown",
        "answer": result.get("answer", "") if result else "",
        "citations": data.get("citations"),
        "citation_validation": data.get("citation_validation"),
        "error": error,
    }


def compute_stats(records: list[dict]) -> dict:
    """对已完成记录计算各维度通过率。"""
    n = len(records)
    stats = {
        "total": n,
        "branches": {},
        "rules": {},
        "all_passed_count": 0,
        "assoc": {"citations_checked": 0, "citations_passed": 0,
                  "failed_records": 0, "duplicate_groups": 0},
        "refusal": {"expected": 0, "hit": 0, "hard_hit": 0,
                    "soft_hit": 0, "miss": 0, "n_a": 0},
        "marker_coverage": 0.0,
    }
    for rec in records:
        branch = rec.get("branch") or "exception"
        stats["branches"][branch] = stats["branches"].get(branch, 0) + 1

        validation = rec.get("citation_validation") or {}
        if validation.get("all_passed"):
            stats["all_passed_count"] += 1
        for rule in validation.get("rules") or []:
            rid = rule.get("rule_id")
            if rid is None:
                continue
            entry = stats["rules"].setdefault(
                rid, {"enabled": 0, "total": 0, "passed": 0}
            )
            if rule.get("enabled"):
                entry["enabled"] += 1
            entry["total"] += 1
            if rule.get("passed"):
                entry["passed"] += 1

        if rec.get("expect_refusal"):
            stats["refusal"]["expected"] += 1
            if branch != "knowledge_qa":
                stats["refusal"]["n_a"] += 1
            elif validation.get("is_refusal"):
                stats["refusal"]["hit"] += 1
                stats["refusal"]["hard_hit"] += 1
            elif _is_refusal_answer(rec.get("answer", "")):
                # 语义拒答：检索非空但 LLM 明确表示无法基于资料回答
                stats["refusal"]["hit"] += 1
                stats["refusal"]["soft_hit"] += 1
            else:
                stats["refusal"]["miss"] += 1

        assoc = rec.get("association_check") or {}
        stats["assoc"]["citations_checked"] += assoc.get("checked", 0)
        stats["assoc"]["citations_passed"] += assoc.get("passed", 0)
        if assoc.get("failed"):
            stats["assoc"]["failed_records"] += 1
        stats["assoc"]["duplicate_groups"] += len(
            assoc.get("duplicate_groups") or []
        )

        cited = len(validation.get("cited_markers") or [])
        ctx = validation.get("context_chunks") or 0
        if ctx:
            stats["marker_coverage"] += cited / ctx
    if n:
        stats["marker_coverage"] = round(stats["marker_coverage"] / n, 4)
    return stats


def write_report(records: list[dict], out_path: Path) -> None:
    stats = compute_stats(records)
    validated = sum(1 for r in records if r.get("citation_validation") is not None)
    lines = [
        "# 知识库引用溯源全量测评报告",
        "",
        "- 生成时间: 自动生成",
        "- 测试集: testset_knowledge.jsonl",
        f"- 总样本数: {stats['total']}",
        "",
        "## 1. 总览",
        "",
        f"- 全部规则通过（all_passed）: **{stats['all_passed_count']}/{validated}**"
        f"（有校验报告的样本，即 knowledge_qa 分支）",
        "- 分支分布: " + ", ".join(
            f"{k}={v}" for k, v in sorted(stats["branches"].items())
        ),
        "",
        "## 2. 引用校验规则通过率（R1-R7）",
        "",
        "| 规则 | 启用次数 | 通过次数 | 通过率 |",
        "|---|---|---|---|",
    ]
    for rid in sorted(stats["rules"]):
        e = stats["rules"][rid]
        enabled = e["enabled"]
        passed = e["passed"]
        rate = f"{passed / enabled:.1%}" if enabled else "N/A（未启用）"
        lines.append(f"| {rid} | {enabled} | {passed} | {rate} |")
    lines += [
        "",
        "## 3. 关联校验（回表 Milvus，防错误关联）",
        "",
        f"- 引用总条数: {stats['assoc']['citations_checked']}",
        f"- 校验通过: {stats['assoc']['citations_passed']}",
        f"- 存在失败记录数: {stats['assoc']['failed_records']}",
        f"- 同内容重复 chunk 组（同一 chunk_uid 多行）: {stats['assoc']['duplicate_groups']}",
        "",
        "## 4. 拒答正确率（负样本）",
        "",
        f"- 期望拒答样本: {stats['refusal']['expected']}",
        f"- 正确拒答: {stats['refusal']['hit']}",
        f"  - 硬拒答（检索为空, is_refusal=true）: {stats['refusal']['hard_hit']}",
        f"  - 语义拒答（检索非空但 LLM 明确拒答，附引用支撑）: {stats['refusal']['soft_hit']}",
        f"- 漏拒答: {stats['refusal']['miss']}",
        f"- 路由分流（未进入 knowledge_qa，router 兜底）: {stats['refusal']['n_a']}",
        "",
        "## 5. 引用覆盖率",
        "",
        f"- 平均内联标记覆盖率（cited_markers / context_chunks）: {stats['marker_coverage']:.1%}",
        "",
    ]
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--no-report", action="store_true",
                        help="仅跑评测，不生成汇总报告")
    args = parser.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    cases = load_cases(TESTSET)
    done_ids = set() if args.no_resume else load_done_ids(OUT_FILE)
    todo = [c for c in cases if c["sample_id"] not in done_ids]
    if args.limit is not None:
        todo = todo[: args.limit]

    print(f"total cases={len(cases)}  already done={len(done_ids)}  "
          f"todo={len(todo)}", flush=True)

    from agent import AgentGraph

    print("Initializing AgentGraph...", flush=True)
    t0 = time.time()
    agent = AgentGraph()
    print(f"AgentGraph ready in {time.time()-t0:.2f}s", flush=True)

    from public_kb.config import Settings
    checker = _AssociationChecker(Settings().collection_name)

    out_f = open(OUT_FILE, "w" if args.no_resume else "a", encoding="utf-8")
    n_done = len(done_ids)
    t_run_start = time.time()

    try:
        for i, case in enumerate(todo, 1):
            q = case["question"]
            sid = case["sample_id"]
            t = time.perf_counter()
            error, result = None, None
            try:
                result = agent.invoke(q, thread_id=f"kb-eval-{sid}")
            except Exception as e:  # noqa: BLE001
                error = f"{type(e).__name__}: {e}"
            elapsed = time.perf_counter() - t

            rec = extract_record(case, result, elapsed, error)
            # 关联校验：仅对 knowledge_qa 分支的非拒答引用回表
            citations = rec.get("citations") or []
            if rec["branch"] == "knowledge_qa" and citations:
                rec["association_check"] = checker.check(citations)
            else:
                rec["association_check"] = None
            out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out_f.flush()

            n_done += 1
            if i % 10 == 0 or i == len(todo):
                rate = (time.time() - t_run_start) / i
                eta = rate * (len(todo) - i) / 60
                print(
                    f"[{i}/{len(todo)}] avg={rate:.2f}s ETA={eta:.1f}min "
                    f"last={elapsed:.2f}s branch={rec.get('branch')} "
                    f"citations={len(citations)}",
                    flush=True,
                )
    except KeyboardInterrupt:
        print("\nInterrupted — progress saved. Re-run without --no-resume to continue.", flush=True)
    finally:
        out_f.close()

    print("DONE. Results in", OUT_FILE, flush=True)

    if not args.no_report:
        records = load_cases(OUT_FILE)
        write_report(records, REPORT_FILE)
        print("Report written to", REPORT_FILE, flush=True)


if __name__ == "__main__":
    main()
