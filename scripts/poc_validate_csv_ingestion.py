"""Validate CSV ingestion and row-level metadata on the local Milvus POC."""

from __future__ import annotations

import csv
import argparse
import json
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from pymilvus import MilvusClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from public_kb.config import Settings
from public_kb.embedding_service import create_embeddings
from public_kb.ingestion.cli import run_csv_ingestion
from public_kb.milvus_store import MilvusStoreManager


URI = "http://localhost:19531"
COLLECTION = "public_kb_hybrid_poc_ingest_v1"
SOURCE_FILE = "pipeline_validation_sample.csv"
REPORT_PATH = ROOT / "test_report" / "csv_ingestion_validation_results.json"
REQUIRED_METADATA = (
    "title",
    "publish_date",
    "source_url",
    "source_file",
    "doc_name",
    "chapter",
    "chunk_index",
    "chunk_uid",
)


def _create_sample_csv(csv_dir: Path) -> Path:
    csv_path = csv_dir / SOURCE_FILE
    rows = []
    for index in range(1, 4):
        rows.append(
            {
                "title": f"验证政策{index}",
                "content": (
                    f"第一章 总则\n"
                    f"第一条 本办法适用于验证政策{index}的实施和管理。\n"
                    f"第二条 相关单位应当按照本办法履行职责，并及时报送实施情况。"
                ),
                "publish_date": f"2026-08-0{index}",
                "source_url": f"https://example.com/policy/{index}",
            }
        )

    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=("title", "content", "publish_date", "source_url"),
        )
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


def _check_metadata(rows: list[dict]) -> list[str]:
    missing = []
    for index, row in enumerate(rows):
        for key in REQUIRED_METADATA:
            value = row.get(key)
            if value is None or (key != "chunk_index" and not str(value).strip()):
                missing.append(f"{index}:{key}")
            elif key == "chunk_index" and not isinstance(value, int):
                missing.append(f"{index}:{key}")
    return missing


def main(*, refresh: bool = False) -> int:
    settings = Settings(
        milvus_uri=URI,
        collection_name=COLLECTION,
        enable_bm25=True,
    )
    if refresh:
        bootstrap_client = MilvusClient(uri=URI)
        if bootstrap_client.has_collection(COLLECTION):
            if not COLLECTION.startswith(settings.milvus_experiment_prefix):
                raise RuntimeError(
                    f"拒绝刷新非实验集合 {COLLECTION!r}；允许前缀为 "
                    f"{settings.milvus_experiment_prefix!r}"
                )
            bootstrap_client.drop_collection(COLLECTION)

    embeddings = create_embeddings(settings)
    started_at = time.perf_counter()
    with TemporaryDirectory() as temp_dir:
        csv_path = _create_sample_csv(Path(temp_dir))
        result = run_csv_ingestion(
            str(csv_path),
            settings,
            embeddings=embeddings,
            mode="initialize",
        )
    elapsed_seconds = time.perf_counter() - started_at

    manager = MilvusStoreManager(settings, embeddings)
    client = manager.collection
    collection_info = client.describe_collection(COLLECTION)
    schema_fields = sorted(field.get("name", "") for field in collection_info.get("fields", []))
    functions = sorted(
        function.get("name", "") for function in (collection_info.get("functions") or [])
    )
    indexes = sorted(client.list_indexes(COLLECTION))
    stats = client.get_collection_stats(COLLECTION)
    row_count = int(stats.get("row_count") or 0)
    queried_rows = client.query(
        COLLECTION,
        filter=f'source_file == "{SOURCE_FILE}"',
        output_fields=list(REQUIRED_METADATA),
        limit=100,
    )
    metadata_missing = _check_metadata(list(queried_rows))

    passed = (
        result.status == "completed"
        and result.chunk_count == result.inserted_count
        and row_count == result.inserted_count
        and len(queried_rows) == result.chunk_count
        and not metadata_missing
        and "sparse_vector" in schema_fields
        and "text_bm25_emb" in functions
        and "sparse_vector" in indexes
    )
    report = {
        "passed": passed,
        "uri": URI,
        "collection": COLLECTION,
        "status": result.status,
        "chunk_count": result.chunk_count,
        "inserted_count": result.inserted_count,
        "row_count": row_count,
        "queried_count": len(queried_rows),
        "metadata_missing": metadata_missing,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "stage_results": [
            {
                "name": stage.name,
                "output_count": stage.output_count,
                "elapsed_ms": round(stage.elapsed_ms, 3),
            }
            for stage in result.stage_results
        ],
        "schema_fields": schema_fields,
        "functions": functions,
        "indexes": indexes,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("CSV-INGESTION-CHECK:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Validate CSV ingestion on the local Milvus POC collection."
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Drop the experimental validation collection before initializing it again.",
    )
    arguments = parser.parse_args()
    sys.exit(main(refresh=arguments.refresh))
