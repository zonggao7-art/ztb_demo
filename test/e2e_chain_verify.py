"""端到端链路验证 — 验证主程序检索链路实际走了哪些技术。

验证目标：
  1. knowledge_qa 分支：混合检索（dense+sparse+RRF）/ Reranker 精排 / 动态阈值
     是否真实生效，还是静默降级到纯稠密检索。
  2. price_inquiry 分支：Milvus 语义召回 / FULLTEXT / 混合重排序 是否生效。
  3. 云端 public_kb 集合 schema 是否含 sparse_vector 字段。

用法：
    python test/e2e_chain_verify.py
"""
from __future__ import annotations

import logging
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(_ROOT, ".env"))

# 日志全量打开，捕获检索路径关键日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)

# 收集关键日志行，便于最后汇总
_KEY_MARKERS = [
    "混合检索", "RRF", "Reranker", "精排", "threshold", "阈值",
    "稠密", "稀疏", "sparse", "dense", "降级", "回退",
    "RECALL_CHAIN", "SEMANTIC_RECALL", "SEMANTIC_BOOTSTRAP",
    "FULLTEXT", "LIKE", "FULL_SCAN", "hybrid_score",
    "Schema", "schema",
]
_collected: list[str] = []


class _MarkerFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if any(m in msg for m in _KEY_MARKERS):
            _collected.append(f"[{record.levelname}] {record.name}: {msg}")
        return True


logging.getLogger().addFilter(_MarkerFilter())


def check_milvus_schema() -> None:
    print("\n" + "=" * 70)
    print("  [0] 云端 public_kb 集合 schema 检查")
    print("=" * 70)
    from pymilvus import MilvusClient

    host = os.getenv("MILVUS_HOST", "")
    port = os.getenv("MILVUS_PORT", "19530")
    client = MilvusClient(uri=f"http://{host}:{port}")
    info = client.describe_collection("public_kb")
    fields = [f.get("name") for f in info.get("fields", [])]
    print(f"  字段列表: {fields}")
    has_sparse = "sparse_vector" in fields
    print(f"  sparse_vector 字段: {'存在' if has_sparse else '不存在'}")
    if not has_sparse:
        print("  → 结论: 混合检索的稀疏 BM25 路 + RRF 融合将被跳过，")
        print("    实际走 '稠密+Reranker' 降级路径（qa_chain._dense_only_retrieve 或稠密+Reranker 模式）。")
    return


def run_knowledge_qa() -> None:
    print("\n" + "=" * 70)
    print("  [1] knowledge_qa 端到端: '招标方式有哪些？'")
    print("=" * 70)
    from agent.graph import AgentGraph

    agent = AgentGraph()
    result = agent.invoke("招标方式有哪些？")
    biz = result.get("business_result", {})
    data = biz.get("data", {})
    print("\n  ── 路由意图:", result.get("intent"))
    print("  ── 回答（前 300 字）:")
    print("     " + (biz.get("answer", "")[:300]).replace("\n", "\n     "))
    citations = data.get("citations", [])
    print(f"  ── 引用数: {len(citations)}")
    for c in citations[:3]:
        print(f"     - [{c.get('doc_name')}] {c.get('chapter')} score={c.get('score')}")
    cv = data.get("citation_validation", {})
    print(f"  ── 引用校验 all_passed: {cv.get('all_passed')}")


def run_price_inquiry() -> None:
    print("\n" + "=" * 70)
    print("  [2] price_inquiry 端到端: '查询安徽海纳信息科技有限公司的中标历史'")
    print("=" * 70)
    from agent.graph import AgentGraph

    agent = AgentGraph()
    result = agent.invoke("查询安徽海纳信息科技有限公司的中标历史")
    biz = result.get("business_result", {})
    print("\n  ── 路由意图:", result.get("intent"))
    print("  ── sub_route:", biz.get("sub_route"), "| query_type:", biz.get("query_type"))
    print("  ── 回答（前 300 字）:")
    print("     " + (biz.get("answer", "")[:300]).replace("\n", "\n     "))
    data = biz.get("data", {})
    meta = data.get("meta", {})
    print(f"  ── 命中记录数: {data.get('total_found')}")
    print(f"  ── 查询表: {data.get('tables')}")
    print(f"  ── SQL 次数: {meta.get('sql_count')}, SQL 总耗时: {meta.get('total_sql_time')}s, 节点耗时: {meta.get('node_elapsed')}s")
    records = data.get("records", [])
    for r in records[:3]:
        keys = [k for k in r.keys() if not k.startswith("_")][:6]
        print(f"     - 记录字段示例: {keys}")


def main() -> int:
    check_milvus_schema()

    run_knowledge_qa()
    run_price_inquiry()

    print("\n" + "=" * 70)
    print("  [3] 关键检索日志汇总（证明实际走了哪些技术）")
    print("=" * 70)
    if not _collected:
        print("  （未捕获到任何关键日志）")
    for line in _collected:
        print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
