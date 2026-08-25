"""
三大核心测试集全量测评执行脚本。

读取三份测试集（testset_bid_project / testset_company_info / testset_company_penalty，
共 1500 条问答对），逐条调用 AgentGraph.invoke() 运行当前业务系统，
记录耗时、分支、答案与结构化记录，增量写入 test_report/raw_results.jsonl（支持断点续跑）。

用法：
    python scripts/run_three_core_evaluation.py                 # 全量 1500 条
    python scripts/run_three_core_evaluation.py --limit 30      # 冒烟
    python scripts/run_three_core_evaluation.py --no-resume     # 清空重跑
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

from eval_common import extract_result, load_cases, load_done_ids  # noqa: E402

TESTSETS = [
    PROJECT_ROOT / "testset_bid_project.jsonl",
    PROJECT_ROOT / "testset_company_info.jsonl",
    PROJECT_ROOT / "testset_company_penalty.jsonl",
]
OUT_DIR = PROJECT_ROOT / "test_report"
OUT_FILE = OUT_DIR / "raw_results.jsonl"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    cases = load_cases(TESTSETS)
    done_ids = set() if args.no_resume else load_done_ids(OUT_FILE)
    todo = [c for c in cases if c["sample_id"] not in done_ids]
    if args.limit is not None:
        todo = todo[: args.limit]

    print(f"total cases={len(cases)}  already done={len(done_ids)}  todo={len(todo)}", flush=True)

    from agent import AgentGraph

    print("Initializing AgentGraph...", flush=True)
    t0 = time.time()
    agent = AgentGraph()
    print(f"AgentGraph ready in {time.time()-t0:.2f}s", flush=True)

    out_f = open(OUT_FILE, "a", encoding="utf-8")
    n_done = len(done_ids)
    t_run_start = time.time()

    try:
        for i, case in enumerate(todo, 1):
            q = case["question"]
            sid = case["sample_id"]
            t = time.perf_counter()
            error = None
            result = None
            try:
                result = agent.invoke(q, thread_id=f"eval3-{sid}")
            except Exception as e:
                error = f"{type(e).__name__}: {e}"
            elapsed = time.perf_counter() - t

            rec = {"sample_id": sid}
            if result is not None:
                rec.update(extract_result(result, elapsed, error))
            else:
                rec.update({
                    "elapsed_s": round(elapsed, 4),
                    "branch": "exception",
                    "sub_route": None,
                    "query_type": None,
                    "intent": "unknown",
                    "answer": "",
                    "records": None,
                    "total_found": None,
                    "tables": None,
                    "meta": None,
                    "citations": None,
                    "citation_validation": None,
                    "error": error,
                })

            out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out_f.flush()

            n_done += 1
            if i % 25 == 0 or i == len(todo):
                rate = (time.time() - t_run_start) / (i)
                eta = rate * (len(todo) - i) / 60
                print(
                    f"[{i}/{len(todo)}] avg={rate:.2f}s ETA={eta:.1f}min "
                    f"last={elapsed:.2f}s branch={rec.get('branch')}",
                    flush=True,
                )
    except KeyboardInterrupt:
        print("\nInterrupted — progress saved. Re-run without --no-resume to continue.", flush=True)
    finally:
        out_f.close()

    print("DONE. Results in", OUT_FILE, flush=True)


if __name__ == "__main__":
    main()
