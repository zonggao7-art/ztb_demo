"""POC 小批量取样入库: DATA/raw_data -> public_kb_hybrid_poc_v1（启用 BM25）。

用法: python scripts/poc_ingest_sample.py
不改 .env；Milvus 指向 v2.6 POC 栈 (http://localhost:19531)。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from public_kb.config import Settings
from public_kb.ingestion.transforms.chunker import SemanticChunker
from public_kb.services.embeddings import create_embeddings
from public_kb.services.milvus_store import MilvusStoreManager

RAW_DIR = ROOT / "DATA" / "raw_data"
SAMPLE_SIZE = 50
URI = "http://localhost:19531"
COLL = "public_kb_hybrid_poc_v1"


def main() -> int:
    settings = Settings(milvus_uri=URI, collection_name=COLL, enable_bm25=True)
    chunker = SemanticChunker(
        max_chars=settings.chunk_max_chars,
        overlap_chars=settings.chunk_overlap_chars,
    )
    docs = []
    md_files = sorted(RAW_DIR.glob("*.md"))
    if not md_files:
        raise FileNotFoundError(f"未找到源文件: {RAW_DIR}")
    for md in md_files:
        text = md.read_text(encoding="utf-8", errors="ignore")
        if not text.strip():
            continue
        docs.extend(chunker.chunk(text, md.name))
        if len(docs) >= SAMPLE_SIZE:
            break
    docs = docs[:SAMPLE_SIZE]
    print(f"取样 {len(docs)} 条 chunks")

    embeddings = create_embeddings(settings)
    manager = MilvusStoreManager(settings, embeddings)
    manager.initialize_collection(docs, recreate=True)

    client = manager.collection
    stats = client.get_collection_stats(COLL)
    row_count = int(stats.get("row_count") or 0)
    info = client.describe_collection(COLL)
    fields = sorted(f.get("name") for f in info.get("fields", []))
    functions = [f.get("name") for f in (info.get("functions") or [])]
    indexes = set(client.list_indexes(COLL))
    print("row_count:", row_count)
    print("fields:", fields)
    print("functions:", functions)
    print("indexes:", sorted(indexes))

    passed = (
        row_count == len(docs)
        and "sparse_vector" in fields
        and "text_bm25_emb" in functions
        and "sparse_vector" in indexes
    )
    print("C2-CHECK:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
