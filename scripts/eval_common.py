"""
评测执行共用工具 — 三大核心/知识库引用评测的用例加载、断点续跑与结果提取。

被 run_three_core_evaluation.py / run_knowledge_citation_eval.py 共用，
避免各评测脚本各自维护一份 load_cases / load_done_ids / extract_result。
"""

from __future__ import annotations

import json
from pathlib import Path


def load_cases(paths) -> list[dict]:
    """加载一个或多个 JSONL 测试集文件。

    Args:
        paths: 单个 Path/str，或 Path/str 列表。

    Returns:
        全部用例（保留文件顺序）。
    """
    if isinstance(paths, (str, Path)):
        paths = [paths]
    cases = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    cases.append(json.loads(line))
    return cases


def load_done_ids(out_file: Path) -> set[str]:
    """读取已完成的 sample_id 集合（断点续跑）。"""
    if not out_file.exists():
        return set()
    done = set()
    with open(out_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                done.add(json.loads(line)["sample_id"])
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def extract_result(result: dict, elapsed_s: float, error: str | None) -> dict:
    """从 AgentGraph.invoke 结果提取测评记录（超集版：含子路由与引用溯源字段）。

    知识问答链路无 citations 键时保持 None，价格链路行同样兼容。
    """
    biz = result.get("business_result", {}) or {}
    data = biz.get("data") or {}
    return {
        "elapsed_s": round(elapsed_s, 4),
        "branch": biz.get("branch", "unknown"),
        "sub_route": biz.get("sub_route"),
        "query_type": biz.get("query_type"),
        "intent": result.get("intent", "unknown"),
        "answer": result.get("answer", ""),
        "records": data.get("records"),
        "total_found": data.get("total_found"),
        "tables": data.get("tables"),
        "meta": data.get("meta"),
        # knowledge_qa 引用溯源标准化字段（价格链路行无此键，保持 None）
        "citations": data.get("citations"),
        "citation_validation": data.get("citation_validation"),
        "error": error,
    }
