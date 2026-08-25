"""Minimal smoke test: run 2 sample questions through the live agent."""
import sys, time, traceback
sys.path.insert(0, "D:/DEMO/zhaotoubiao_demo")

questions = [
    "查询项目编号为2441451000000263856的项目名称和中标金额分别是多少？",
    "查询项目编号为AUTO_GEN_000083的采购单位和中标单位分别是什么？",
]

def main():
    from agent import AgentGraph
    print("Initializing AgentGraph...", flush=True)
    t0 = time.time()
    try:
        agent = AgentGraph()
        print(f"Init OK in {time.time()-t0:.2f}s", flush=True)
    except Exception:
        traceback.print_exc()
        sys.exit(1)

    for q in questions:
        print("\n" + "="*70, flush=True)
        print("Q:", q, flush=True)
        t = time.time()
        try:
            r = agent.invoke(q)
            dt = time.time() - t
            print(f"TIME: {dt:.2f}s", flush=True)
            print("INTENT:", r.get("intent"), flush=True)
            print("ANSWER:\n" + str(r.get("answer")), flush=True)
            br = r.get("business_result", {})
            print("BRANCH:", br.get("branch"), flush=True)
            data = br.get("data")
            print("DATA type:", type(data).__name__, flush=True)
            if isinstance(data, dict):
                print("DATA keys:", list(data.keys()), flush=True)
                for k in ("records", "sql", "query_type", "sub_route", "recall_stage"):
                    if k in data:
                        v = data[k]
                        if isinstance(v, list):
                            print(f"  data[{k}] len={len(v)}, first={v[:1]}", flush=True)
                        else:
                            print(f"  data[{k}] = {v}", flush=True)
            else:
                print("DATA:", str(data)[:500], flush=True)
        except Exception:
            traceback.print_exc()
            print(f"FAILED after {time.time()-t:.2f}s", flush=True)

if __name__ == "__main__":
    main()
