"""重建 ztb_clean 结构化语义集合并做基础验收。"""

from __future__ import annotations

import logging
import os
import sys

from pymilvus import MilvusClient

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from agent.nodes.price_inquiry import (  # noqa: E402
    _MYSQL_SEMANTIC_COLLECTION,
    _get_expected_semantic_row_count,
    _get_milvus_uri,
    _get_settings,
    _rebuild_mysql_semantic_collection,
)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    settings = _get_settings()
    expected = _get_expected_semantic_row_count()
    print("=" * 72)
    print("重建 MySQL 结构化语义集合")
    print(f"Milvus: {_get_milvus_uri(settings)}")
    print(f"Collection: {_MYSQL_SEMANTIC_COLLECTION}")
    print(f"Expected rows: {expected}")
    print("=" * 72)

    ok = _rebuild_mysql_semantic_collection()
    client = MilvusClient(uri=_get_milvus_uri(settings))
    actual = 0
    if client.has_collection(_MYSQL_SEMANTIC_COLLECTION):
        actual = int(
            client.get_collection_stats(_MYSQL_SEMANTIC_COLLECTION).get("row_count", 0) or 0
        )

    print(f"Rebuild success: {ok}")
    print(f"Actual rows: {actual}")
    print("=" * 72)

    if not ok:
        return 1
    if expected is not None and actual < expected:
        print("验收失败：集合行数未达到预期。")
        return 2
    print("验收通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
