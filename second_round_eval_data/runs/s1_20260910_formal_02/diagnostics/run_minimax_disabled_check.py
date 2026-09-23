import asyncio
import json
from pathlib import Path
import sys
sys.path.insert(0, r'D:\DEMO\zhaotoubiao_demo_copy')
from evaluation.rag.scoring import score_results

root = Path(r"D:\DEMO\zhaotoubiao_demo_copy\second_round_eval_data\runs\s1_20260910_formal_02\diagnostics")
scores = root / "minimax_m3_thinking_disabled_scores_4.jsonl"
usage = root / "minimax_m3_thinking_disabled_usage_4.json"
for path in (scores, usage):
    if path.exists():
        path.unlink()
rows = asyncio.run(score_results(
    root / "minimax_m3_thinking_disabled_input_4.jsonl",
    scores,
    usage,
    metric_timeout_s=600.0,
    show_progress=True,
    concurrency=4,
))
print(json.dumps({
    "rows": len(rows),
    "failures": {
        row["sample_id"]: [name for name, value in row["metrics"].items() if value["status"] != "success"]
        for row in rows
    },
}, ensure_ascii=False, indent=2))

