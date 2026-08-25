"""
Run the full text2sql evaluation against the live agent.

Reads every case from text2sql_dataset.jsonl, runs it through AgentGraph.invoke(),
measures wall-clock time, and persists one JSONL line per case to
test_report/raw_results.jsonl (incremental append + resume support).

Usage:
    python scripts/run_evaluation.py                 # all 1000 cases, resume if interrupted
    python scripts/run_evaluation.py --limit 20      # smoke run
    python scripts/run_evaluation.py --no-resume     # start over
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DATASET = PROJECT_ROOT / "text2sql_dataset.jsonl"
OUT_DIR = PROJECT_ROOT / "test_report"
OUT_FILE = OUT_DIR / "raw_results.jsonl"


def load_cases(path: Path) -> list[dict]:
    cases = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            cases.append(json.loads(line))
    return cases


def load_done_ids(out_file: Path) -> set[str]:
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
    biz = result.get("business_result", {}) or {}
    data = biz.get("data") or {}
    return {
        "elapsed_s": round(elapsed_s, 4),
        "branch": biz.get("branch", "unknown"),
        "intent": result.get("intent", "unknown"),
        "answer": result.get("answer", ""),
        "records": data.get("records"),
        "total_found": data.get("total_found"),
        "tables": data.get("tables"),
        "meta": data.get("meta"),
        "error": error,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="run only first N cases")
    parser.add_argument("--no-resume", action="store_true", help="ignore existing results")
    args = parser.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    cases = load_cases(DATASET)
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
                result = agent.invoke(q, thread_id=f"eval-{sid}")
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
                    "intent": "unknown",
                    "answer": "",
                    "records": None,
                    "total_found": None,
                    "tables": None,
                    "meta": None,
                    "error": error,
                })

            out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out_f.flush()

            n_done += 1
            if i % 10 == 0 or i == len(todo):
                rate = (time.time() - t_run_start) / n_done if n_done else 0
                eta = rate * (len(cases) - n_done)
                print(
                    f"[{n_done}/{len(cases)}] elapsed_avg={rate:.2f}s "
                    f"ETA={eta/60:.1f}min  last={elapsed:.2f}s  branch={rec.get('branch')}",
                    flush=True,
                )
    except KeyboardInterrupt:
        print("\nInterrupted — progress saved. Re-run without --no-resume to continue.", flush=True)
    finally:
        out_f.close()

    print("DONE. Results in", OUT_FILE, flush=True)


if __name__ == "__main__":
    main()
