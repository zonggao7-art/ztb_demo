"""执行 price_inquiry 的 P1 回归验证。"""

from __future__ import annotations

import json
import logging
import os
import sys
from types import SimpleNamespace

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from agent.nodes.price_inquiry import (  # noqa: E402
    _SUB_ROUTE_MAP,
    _build_llm,
    _get_query_fn,
    _parse_unified_intent,
    _safe_parse_intent,
    _semantic_recall_candidates,
    node_price_inquiry,
)


def _format_records(
    records: list[dict],
    tables: list[str],
    *,
    max_field_chars: int = 40,
    max_display_fields: int = 4,
) -> str:
    """格式化查询记录为可读文本（自 price_inquiry 下沉的调试辅助）。

    Args:
        records: 待格式化记录列表
        tables: 查询涉及的表名
        max_field_chars: 单字段最大显示字符数（penalty_check 等长文本场景应调大）
        max_display_fields: 每条记录最多显示字段数
    """
    lines: list[str] = []
    for i, rec in enumerate(records[:10], 1):
        source = rec.get("_source_db", "")
        table = rec.get("_source_table", "")
        display_items = [(k, v) for k, v in rec.items()
                         if v and k not in {"_source_db", "_source_table", "_id_",
                                             "_score_", "_hybrid_score_"}]
        fields = " | ".join(
            f"{k}: {str(v)[:max_field_chars]}" for k, v in display_items[:max_display_fields]
        )
        lines.append(f"  [{i}] [{source}.{table}] {fields}")
    if len(records) > 10:
        lines.append(f"  ... 共 {len(records)} 条记录")
    return "\n".join(lines)


TEST_QUESTIONS = [
    "最近有没有关于保温材料方面的中标项目啊",
    "安徽软件信息行业中型及以上企业有哪些？",
    "河源市赞爷餐饮管理服务有限公司有没有不良记录？",
    "找几个防水涂料的供应商，要价格便宜的",
    "福建师范大学招标过什么项目？",
    "福州怡富电梯有限公司2024年中标金额最大的项目是哪个？",
]


def run_case(question: str) -> dict:
    llm = _build_llm()
    intent = _safe_parse_intent(_parse_unified_intent(question, llm))
    route_config = _SUB_ROUTE_MAP.get(intent.sub_route, _SUB_ROUTE_MAP["all"])
    query_fn = _get_query_fn(route_config["query_fn"])
    raw = query_fn(intent)
    semantic_hits = _semantic_recall_candidates(intent, route_config["tables"])
    node_result = node_price_inquiry(
        {"messages": [SimpleNamespace(content=question)]}
    )["business_result"]

    return {
        "question": question,
        "sub_route": intent.sub_route,
        "query_type": intent.query_type,
        "semantic_keywords": intent.semantic_keywords,
        "total_found": raw.get("total_found", 0),
        "sql_count": raw.get("sql_count", 0),
        "total_sql_time": round(raw.get("total_sql_time", 0.0), 3),
        "semantic_hits": {table: len(ids) for table, ids in semantic_hits.items()},
        "semantic_hit_total": sum(len(ids) for ids in semantic_hits.values()),
        "node_total_found": node_result.get("data", {}).get("total_found", 0),
        "node_sub_route": node_result.get("sub_route"),
        "preview": _format_records(raw.get("records", [])[:3], raw.get("queried_tables", [])),
    }


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    results = [run_case(question) for question in TEST_QUESTIONS]
    print(json.dumps(results, ensure_ascii=False, indent=2))

    failures = [
        item for item in results
        if item["total_found"] <= 0 or item["node_total_found"] <= 0
    ]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
