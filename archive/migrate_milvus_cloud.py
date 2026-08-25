"""一次性全量迁移：本地 Milvus -> 云端 Milvus（仅向量数据）。

用法：
    python migrate_milvus_cloud.py            # 全量同步 + 一致性校验
    python migrate_milvus_cloud.py --verify   # 仅校验（不迁移）

范围（本次任务仅 Milvus，不含 Redis/MySQL）：
    public_kb            29,729 条
    mysql_price_semantic 77,597 条
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from cloud_sync.config import CloudSyncConfig
from cloud_sync.milvus_sync import MilvusMigrator
from cloud_sync.verify import ConsistencyVerifier

# 测试阶段曾污染云端产生的集合，迁移前统一清理
STALE_COLLECTIONS = ["_cloud_sync_smoke_dst"]


def main() -> int:
    parser = argparse.ArgumentParser(description="本地 Milvus -> 云端 Milvus 一次性全量迁移")
    parser.add_argument("--verify-only", action="store_true", help="仅校验一致性，不执行迁移")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    config = CloudSyncConfig()
    migrator = MilvusMigrator(config)

    # 清理历史测试污染集合（目标端）
    for stale in STALE_COLLECTIONS:
        if migrator.target.has_collection(stale):
            migrator.target.drop_collection(stale)
            print(f"[cleanup] 已删除云端残留集合: {stale}")

    if args.verify_only:
        print("== 仅执行一致性校验 ==")
    else:
        print("== Milvus 全量同步（本地 -> 云端）==")
        summary = migrator.full_sync()
        print(json.dumps(summary, ensure_ascii=False, indent=2))

    print("== 一致性校验 ==")
    verifier = ConsistencyVerifier(config)
    result = verifier.verify()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("all_passed") else 1


if __name__ == "__main__":
    sys.exit(main())
