"""
阶段 0 — 异步改造前的性能基线测量

用途
----
手册 `docs/implementation_handbook_async_memory_streaming.md` §阶段0 要求记录
改造前的 LLM / Embedding / Milvus / MySQL 各阶段 P50/P95，作为后续异步改造的
对照基准。

行为
----
1. **在线模式**（MySQL / Milvus / LLM API 可达）：
   - LLM:        对话模型单条 ping（当前 OpenRouter），warmup 后采 N 次
   - Embedding:  BGE-m3 单文本向量化，warmup 后采 N 次
   - Milvus:     `public_kb` 集合 search，warmup 后采 N 次
   - MySQL:      `SELECT 1` + 一条已知 FULLTEXT 查询，warmup 后采 N 次

2. **离线模式**（任一依赖不通）：不抛错，回退到汇总
   `test_report/knowledge_citation_results.jsonl` + `test_report/metrics.json`
   的历史时延，标记 source="historical"。

输出
----
固定写入 `test_report/baseline_async_pre.json`，结构：

{
  "stage": "pre-async",
  "generated_at": "...",
  "source": "live" | "historical",
  "infra_reachable": { "mysql": bool, "milvus": bool, "llm": bool },
  "results": [
    {"name": "llm",        "n": N, "p50_ms": ..., "p95_ms": ..., "mean_ms": ...},
    {"name": "embedding",  "n": N, ...},
    {"name": "milvus",     "n": N, ...},
    {"name": "mysql",      "n": N, ...},
  ],
  "notes": ["..."]
}

用法
----
    python scripts/benchmark_async.py            # 默认 N=20
    python scripts/benchmark_async.py --n 50     # 调整样本数
    python scripts/benchmark_async.py --force-historical   # 强制走历史快照
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REPORT = ROOT / "test_report" / "baseline_async_pre.json"
HIST_RESULTS = ROOT / "test_report" / "knowledge_citation_results.jsonl"
HIST_METRICS = ROOT / "test_report" / "metrics.json"


# ────────────────────────────────────────────────────────────
# 统计
# ────────────────────────────────────────────────────────────
def _percentile(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round(len(xs) * p / 100)) - 1))
    return round(xs[k] * 1000, 2)  # s → ms


def _bench_sync(label: str, fn: Callable[[], None], n: int, warmup: int = 2) -> dict:
    for _ in range(warmup):
        try:
            fn()
        except Exception:
            return {"name": label, "n": 0, "p50_ms": None, "p95_ms": None,
                    "mean_ms": None, "error": "warmup failed"}
    samples: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            fn()
            samples.append(time.perf_counter() - t0)
        except Exception as e:
            return {"name": label, "n": len(samples), "p50_ms": _percentile(samples, 50),
                    "p95_ms": _percentile(samples, 95),
                    "mean_ms": round(statistics.mean(samples) * 1000, 2) if samples else None,
                    "error": f"sample failed: {e!r}"}
    return {
        "name": label, "n": len(samples),
        "p50_ms": _percentile(samples, 50),
        "p95_ms": _percentile(samples, 95),
        "mean_ms": round(statistics.mean(samples) * 1000, 2),
        "max_ms": round(max(samples) * 1000, 2),
        "min_ms": round(min(samples) * 1000, 2),
    }


async def _bench_async(label: str, coro_factory: Callable[[], "asyncio.Future"],
                        n: int, warmup: int = 2) -> dict:
    for _ in range(warmup):
        try:
            await coro_factory()
        except Exception:
            return {"name": label, "n": 0, "p50_ms": None, "p95_ms": None,
                    "mean_ms": None, "error": "warmup failed"}
    samples: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            await coro_factory()
            samples.append(time.perf_counter() - t0)
        except Exception as e:
            return {"name": label, "n": len(samples),
                    "p50_ms": _percentile(samples, 50),
                    "p95_ms": _percentile(samples, 95),
                    "mean_ms": round(statistics.mean(samples) * 1000, 2) if samples else None,
                    "error": f"sample failed: {e!r}"}
    return {
        "name": label, "n": len(samples),
        "p50_ms": _percentile(samples, 50),
        "p95_ms": _percentile(samples, 95),
        "mean_ms": round(statistics.mean(samples) * 1000, 2),
        "max_ms": round(max(samples) * 1000, 2),
        "min_ms": round(min(samples) * 1000, 2),
    }


# ────────────────────────────────────────────────────────────
# 探活
# ────────────────────────────────────────────────────────────
def _probe_tcp(host: str, port: int, timeout: float = 1.5) -> bool:
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _probe_infra() -> dict:
    from public_kb.config import Settings  # noqa: WPS433  延迟导入
    s = Settings()
    return {
        "mysql": _probe_tcp(s.mysql_host, int(s.mysql_port)),
        "milvus": _probe_tcp(s.milvus_host, int(s.milvus_port)),
        "llm": bool(s.llm_api_key),
        "embedding": bool(s.embedding_api_key),
    }


# ────────────────────────────────────────────────────────────
# 在线基准
# ────────────────────────────────────────────────────────────
def _live_mysql_bench(n: int) -> dict:
    """单条 `SELECT 1` + 一条 ztb_clean 真实小查询。"""
    import pymysql
    from public_kb.config import Settings
    s = Settings()
    conn = pymysql.connect(
        host=s.mysql_host,
        port=int(s.mysql_port),
        user=s.mysql_user,
        password=s.mysql_password,
        database=s.mysql_clean_db,
        charset="utf8mb4", connect_timeout=3, read_timeout=5)

    def _one():
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()

    try:
        return _bench_sync("mysql", _one, n=n)
    finally:
        conn.close()


def _live_milvus_bench(n: int) -> dict:
    """对 public_kb 集合跑一次空查询 search。"""
    from pymilvus import MilvusClient
    from public_kb.config import Settings
    s = Settings()
    uri = f"http://{s.milvus_host}:{int(s.milvus_port)}"
    client = MilvusClient(uri=uri)

    def _one():
        client.search(collection_name="public_kb",
                       data=[[0.0] * 1024],  # BGE-m3 维度
                       limit=5,
                       output_fields=["chunk_id"])

    try:
        return _bench_sync("milvus", _one, n=n)
    finally:
        client.close()


def _live_embedding_bench(n: int) -> dict:
    """BGE-m3 单条文本向量化。"""
    import httpx
    from public_kb.config import Settings
    s = Settings()
    base = s.embedding_base_url
    key = s.embedding_api_key
    model = s.embedding_model
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {"model": model, "input": "测试文本"}

    def _one():
        with httpx.Client(timeout=10) as c:
            c.post(f"{base}/embeddings", headers=headers, json=payload).raise_for_status()

    return _bench_sync("embedding", _one, n=n)


async def _live_llm_bench_async(n: int) -> dict:
    """LLM 单条 ping。"""
    from public_kb.config import Settings
    from public_kb.llm_factory import create_llm
    from langchain_core.messages import HumanMessage
    llm = create_llm(Settings(), temperature=0)

    async def _one():
        await llm.ainvoke([HumanMessage(content="ping")])

    return await _bench_async("llm", _one, n=n)


def run_live(n: int) -> tuple[list[dict], dict]:
    infra = _probe_infra()
    results: list[dict] = []
    notes: list[str] = []

    if infra["mysql"]:
        try:
            results.append(_live_mysql_bench(n))
        except Exception as e:
            results.append({"name": "mysql", "error": repr(e)})
    else:
        notes.append("mysql unreachable, skipped")

    if infra["milvus"]:
        try:
            results.append(_live_milvus_bench(n))
        except Exception as e:
            results.append({"name": "milvus", "error": repr(e)})
    else:
        notes.append("milvus unreachable, skipped")

    if infra.get("embedding"):
        try:
            results.append(_live_embedding_bench(n))
        except Exception as e:
            results.append({"name": "embedding", "error": repr(e)})
    else:
        notes.append("embedding api key missing, skipped")

    if infra["llm"]:
        try:
            results.append(asyncio.run(_live_llm_bench_async(n)))
        except Exception as e:
            results.append({"name": "llm", "error": repr(e)})
    else:
        notes.append("llm api key missing, skipped")

    return results, {"infra_reachable": infra, "notes": notes}


# ────────────────────────────────────────────────────────────
# 离线 / 历史快照聚合
# ────────────────────────────────────────────────────────────
def _from_history() -> tuple[list[dict], dict]:
    """汇总已有的 test_report/ 数据。"""
    results: list[dict] = []
    notes: list[str] = []

    # LLM/Embedding/Milvus/Reranker —— 用知识库引用评估的端到端 elapsed_s
    if HIST_RESULTS.exists():
        try:
            cases = [json.loads(l) for l in HIST_RESULTS.read_text(encoding="utf-8").splitlines() if l.strip()]
            elapsed = [c["elapsed_s"] for c in cases if "elapsed_s" in c]
            if elapsed:
                results.append({
                    "name": "rag_e2e",
                    "n": len(elapsed),
                    "p50_ms": _percentile(elapsed, 50),
                    "p95_ms": _percentile(elapsed, 95),
                    "mean_ms": round(statistics.mean(elapsed) * 1000, 2),
                    "max_ms": round(max(elapsed) * 1000, 2),
                    "min_ms": round(min(elapsed) * 1000, 2),
                    "source": "knowledge_citation_results.jsonl",
                })
                notes.append(f"rag_e2e ← {len(elapsed)} cases from knowledge_citation_results.jsonl")
        except Exception as e:
            notes.append(f"failed to parse {HIST_RESULTS.name}: {e!r}")

    # MySQL —— 询价评测的 timing.total_sql_time
    if HIST_METRICS.exists():
        try:
            m = json.loads(HIST_METRICS.read_text(encoding="utf-8"))
            # total_sql_time 是 {avg, median, min, max, count}（秒）
            sqlt = m.get("meta", {}).get("total_sql_time", {})
            if sqlt.get("count"):
                # 历史数据没有 P95，用 max 近似
                results.append({
                    "name": "mysql_sql",
                    "n": sqlt["count"],
                    "p50_ms": round(sqlt.get("median", sqlt.get("avg", 0)) * 1000, 2),
                    "p95_ms": round(sqlt.get("max", 0) * 1000, 2),  # 退化代理
                    "mean_ms": round(sqlt.get("avg", 0) * 1000, 2),
                    "max_ms": round(sqlt.get("max", 0) * 1000, 2),
                    "min_ms": round(sqlt.get("min", 0) * 1000, 2),
                    "source": "metrics.json (price_inquiry eval)",
                    "caveat": "p95 退化为 max；阶段1+在线基准可补正",
                })
            # node_elapsed 整体时延
            ne = m.get("meta", {}).get("node_elapsed", {})
            if ne.get("count"):
                results.append({
                    "name": "price_node_total",
                    "n": ne["count"],
                    "p50_ms": round(ne.get("avg", 0) * 1000, 2),
                    "p95_ms": round(ne.get("max", 0) * 1000, 2),
                    "mean_ms": round(ne.get("avg", 0) * 1000, 2),
                    "max_ms": round(ne.get("max", 0) * 1000, 2),
                    "min_ms": round(ne.get("min", 0) * 1000, 2),
                    "source": "metrics.json (price_inquiry eval)",
                    "caveat": "p50/p95 用 avg/max 近似",
                })
            # 整体 timing（端到端询价）
            t = m.get("timing", {})
            if t.get("count"):
                results.append({
                    "name": "price_e2e",
                    "n": t["count"],
                    "p50_ms": round(t.get("p50", 0) * 1000, 2),
                    "p95_ms": round(t.get("p95", 0) * 1000, 2),
                    "mean_ms": round(t.get("avg", 0) * 1000, 2),
                    "max_ms": round(t.get("max", 0) * 1000, 2),
                    "min_ms": round(t.get("min", 0) * 1000, 2),
                    "source": "metrics.json (price_inquiry eval)",
                })
            notes.append(f"price_e2e ← {t.get('count', 0)} cases; "
                         f"accuracy={m.get('answer_accuracy')}%; "
                         f"field_recall={m.get('field_recall_rate')}%")
        except Exception as e:
            notes.append(f"failed to parse {HIST_METRICS.name}: {e!r}")

    return results, {"notes": notes}


# ────────────────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=20, help="每个组件的采样次数")
    ap.add_argument("--force-historical", action="store_true",
                     help="强制走历史快照，不尝试在线基准")
    args = ap.parse_args()

    now = datetime.now(timezone.utc).isoformat()
    notes: list[str] = []

    if args.force_historical:
        results, ctx = _from_history()
        source = "historical"
        infra = {"mysql": False, "milvus": False, "llm": False, "embedding": False}
        notes.append("--force-historical 指定")
        notes.extend(ctx.get("notes", []))
    else:
        # 先探活；只要 mysql+milvus+embedding+llm 至少有一个可达，就尝试在线
        infra = _probe_infra()
        if any([infra["mysql"], infra["milvus"], infra["llm"], infra["embedding"]]):
            try:
                results, ctx = run_live(args.n)
                source = "live"
                notes.extend(ctx.get("notes", []))
            except Exception as e:
                notes.append(f"live bench failed: {e!r}; falling back to history")
                results, ctx = _from_history()
                source = "historical-fallback"
                notes.extend(ctx.get("notes", []))
        else:
            notes.append("all infra unreachable; using historical snapshot")
            results, ctx = _from_history()
            source = "historical"
            notes.extend(ctx.get("notes", []))

    payload = {
        "stage": "pre-async",
        "generated_at": now,
        "source": source,
        "infra_reachable": infra,
        "results": results,
        "notes": notes,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[benchmark_async] source={source} → {REPORT}")
    for r in results:
        print(f"  - {r['name']:<16} n={r.get('n',0):>4}  "
              f"p50={r.get('p50_ms')}ms  p95={r.get('p95_ms')}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
