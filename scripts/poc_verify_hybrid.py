"""POC C3: 分层混合检索验证（8 用例，结果落盘 test_report/）。

用法: python scripts/poc_verify_hybrid.py
前置: C2 已完成（public_kb_hybrid_poc_v1 就绪于 v2.6 栈）。
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pymilvus import AnnSearchRequest, RRFRanker

from public_kb.config import Settings
from public_kb.embedding_service import create_embeddings
from public_kb.llm_factory import create_llm
from public_kb.milvus_store import MilvusStoreManager
from public_kb.qa_chain import build_qa_chain

URI = "http://localhost:19531"
COLL = "public_kb_hybrid_poc_v1"
QUESTION = "招标方式有哪些？"
IRRELEVANT = "今天天气怎么样"
REPORT = ROOT / "test_report" / "hybrid_poc_c3_results.json"

results = []


def record(case: int, name: str, passed: bool, detail: dict) -> None:
    results.append(
        {"case": case, "name": name, "passed": passed, "detail": detail}
    )
    print(f"case{case} {name}: {'PASS' if passed else 'FAIL'}")


def hit_doc(hit) -> str:
    ent = getattr(hit, "entity", {})
    if isinstance(ent, dict) and isinstance(ent.get("entity"), dict):
        ent = ent["entity"]
    if isinstance(ent, dict):
        return str(ent.get("doc_name") or "")
    return str(getattr(ent, "doc_name", "") or "")


def main() -> int:
    base = Settings(milvus_uri=URI, collection_name=COLL, enable_bm25=True)
    embeddings = create_embeddings(base)
    manager = MilvusStoreManager(base, embeddings)
    if not manager.load_existing():
        print("FATAL: cannot load", COLL)
        return 2
    client = manager.collection
    vector_store = manager.store
    llm = create_llm(base)
    dense_vec = embeddings.embed_query(QUESTION)

    # -- case 1: dense only --
    try:
        hits = client.search(
            COLL, data=[dense_vec], anns_field="vector",
            search_params={"metric_type": "COSINE", "params": {"nprobe": base.nprobe}},
            limit=5, output_fields=["doc_name"],
        )[0]
        top = float(hits[0].score) if hits else 0.0
        record(1, "dense-only", bool(hits) and top > 0.45,
               {"hits": len(hits), "top_score": round(top, 4)})
        dense_hits_n = len(hits)
    except Exception as exc:
        dense_hits_n = 0
        record(1, "dense-only", False, {"error": repr(exc)})

    # -- case 2: BM25 only (raw text -> server-side function) --
    try:
        hits = client.search(
            COLL, data=[QUESTION], anns_field="sparse_vector",
            search_params={"metric_type": "BM25"},
            limit=5, output_fields=["doc_name"],
        )[0]
        detail = {"hits": len(hits)}
        if hits:
            detail["top_score"] = round(float(hits[0].score), 6)
            detail["top_doc"] = hit_doc(hits[0])
        record(2, "bm25-only", bool(hits), detail)
        sparse_hits_n = len(hits)
    except Exception as exc:
        sparse_hits_n = 0
        record(2, "bm25-only", False, {"error": repr(exc)})

    # -- case 3: hybrid + RRF (raw) --
    try:
        dense_req = AnnSearchRequest(
            data=[dense_vec], anns_field="vector",
            param={"metric_type": "COSINE", "params": {"nprobe": base.nprobe}},
            limit=10,
        )
        sparse_req = AnnSearchRequest(
            data=[QUESTION], anns_field="sparse_vector",
            param={"metric_type": "BM25", "params": {}},
            limit=10,
        )
        hits = client.hybrid_search(
            COLL, reqs=[dense_req, sparse_req],
            ranker=RRFRanker(k=base.rrf_k),
            limit=10, output_fields=["doc_name"],
        )[0]
        record(3, "hybrid-rrf(raw)", len(hits) >= max(dense_hits_n, sparse_hits_n, 1),
               {"fusion_hits": len(hits), "dense_hits": dense_hits_n,
                "sparse_hits": sparse_hits_n})
    except Exception as exc:
        record(3, "hybrid-rrf(raw)", False, {"error": repr(exc)})

    # -- case 4: full chain with real reranker --
    chain = build_qa_chain(
        vector_store=vector_store, llm=llm, settings=base,
        collection=client, embeddings=embeddings,
    )
    try:
        res4 = chain.invoke(QUESTION)
        diag = res4.get("retrieval_diagnostics", {})
        mode = diag.get("retrieval_mode")
        passed = (
            mode == "hybrid_rerank"
            and bool(res4.get("sources"))
            and bool(res4.get("citations"))
        )
        record(4, "full-chain(reranker real)", passed,
               {"mode": mode, "reranker_status": diag.get("reranker_status"),
                "dense": diag.get("dense_count"), "sparse": diag.get("sparse_count"),
                "fusion": diag.get("fusion_count"),
                "sources": len(res4.get("sources", [])),
                "answer_head": res4.get("answer", "")[:80]})
        res4_for_citation = res4
    except Exception as exc:
        res4_for_citation = {}
        record(4, "full-chain(reranker real)", False, {"error": repr(exc)})

    # -- case 5: reranker failure -> keep RRF order --
    bad_rerank = Settings(
        milvus_uri=URI, collection_name=COLL, enable_bm25=True,
        embedding_base_url="http://127.0.0.1:9",
    )
    chain_bad = build_qa_chain(
        vector_store=vector_store, llm=llm, settings=bad_rerank,
        collection=client, embeddings=embeddings,
    )
    try:
        res5 = chain_bad.invoke(QUESTION)
        diag = res5.get("retrieval_diagnostics", {})
        passed = (
            diag.get("retrieval_mode") == "hybrid_rrf"
            and diag.get("reranker_status") == "failed"
            and diag.get("fallback_reason") == "reranker_failed"
            and bool(res5.get("sources"))
        )
        record(5, "reranker-failure fallback", passed,
               {"mode": diag.get("retrieval_mode"),
                "reranker_status": diag.get("reranker_status"),
                "fallback_reason": diag.get("fallback_reason"),
                "sources": len(res5.get("sources", []))})
    except Exception as exc:
        record(5, "reranker-failure fallback", False, {"error": repr(exc)})

    # -- case 6: irrelevant question -> refusal --
    try:
        res6 = chain.invoke(IRRELEVANT)
        diag = res6.get("retrieval_diagnostics", {})
        mode = diag.get("retrieval_mode", "")
        answer = res6.get("answer", "")
        refused = (
            len(res6.get("citations", [])) == 0
            and ("无法" in answer or "无法" in answer or "抱歉" in answer)
            and mode.startswith("hybrid")
        )
        record(6, "irrelevant -> refusal", refused,
               {"mode": mode, "citations": len(res6.get("citations", [])),
                "answer_head": answer[:60]})
    except Exception as exc:
        record(6, "irrelevant -> refusal", False, {"error": repr(exc)})

    # -- case 7: citation validation (R1-R7) from case4 --
    try:
        cv = (res4_for_citation or {}).get("citation_validation", {})
        record(7, "citation R1-R7", cv.get("all_passed") is True,
               {"all_passed": cv.get("all_passed"),
                "failed_rules": cv.get("failed_rules") or cv.get("errors")})
    except Exception as exc:
        record(7, "citation R1-R7", False, {"error": repr(exc)})

    # -- case 8: strict mode end-to-end (no silent fallback) --
    strict = Settings(
        milvus_uri=URI, collection_name=COLL, enable_bm25=True,
        strict_hybrid_validation=True,
    )
    chain_strict = build_qa_chain(
        vector_store=vector_store, llm=llm, settings=strict,
        collection=client, embeddings=embeddings,
    )
    try:
        res8 = chain_strict.invoke(QUESTION)
        diag = res8.get("retrieval_diagnostics", {})
        mode = diag.get("retrieval_mode", "")
        record(8, "strict-mode e2e", mode.startswith("hybrid"),
               {"mode": mode,
                "note": "no exception under strict = no silent fallback"})
    except Exception as exc:
        record(8, "strict-mode e2e", False, {"error": repr(exc)})

    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(
        json.dumps(
            {"uri": URI, "collection": COLL, "question": QUESTION,
             "ran_at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "results": results},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    all_passed = all(r["passed"] for r in results)
    print("OVERALL:", "PASS" if all_passed else "FAIL")
    print("report ->", REPORT)
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
