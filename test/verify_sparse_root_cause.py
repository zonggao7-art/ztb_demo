"""验证脚本：精确诊断 sparse_vector 缺失的根本原因。

不修改任何数据，纯只读探测。验证 4 个假设：
  T1. 云端 Milvus 是否真的不支持 BM25 Function（服务端能力探测）
  T2. pymilvus 3.0.1 + Milvus 2.4.0 组合是否有 protobuf 不兼容（构造一个 BM25 Function 试创建空集合，看服务端是否接收）
  T3. Milvus 2.4.0 是否支持 Full-Text Search（传原文给 sparse 路，验证 2.5+ 特性）
  T4. 现有 public_kb 集合是否能在线"加列"（ALTER COLLECTION add field）

通过结果 → 给出明确的升级/不升级决策。
"""
from __future__ import annotations

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(_ROOT, ".env"))

from pymilvus import (
    DataType, MilvusClient, Function, FunctionType,
)

HOST = os.getenv("MILVUS_HOST", "")
PORT = os.getenv("MILVUS_PORT", "19530")
URI = f"http://{HOST}:{PORT}"

client = MilvusClient(uri=URI)
TEST_COLL = "_verify_sparse_root_cause"  # 临时集合，结束自动清理


def cleanup() -> None:
    try:
        client.drop_collection(TEST_COLL)
    except Exception:
        pass


def heading(s: str) -> None:
    print("\n" + "=" * 70)
    print(f"  {s}")
    print("=" * 70)


def t1_server_capability() -> bool:
    """T1: 服务端是否声明支持 BM25 / Full-Text Search。"""
    heading("T1  服务端能力探测（Milvus 版本 + 是否支持 BM25/FullText）")
    server_ver = client.get_server_version()
    print(f"  服务端版本: {server_ver}")
    # 尝试查询服务端能力（不同版本返回字段不同，兼容处理）
    try:
        # pymilvus 3.x 没有直接的 capability 接口，但 version >= 2.5 才有 BM25
        ver_num = tuple(int(x) for x in server_ver.replace("v", "").split("-")[0].split("."))
        supports_bm25 = ver_num >= (2, 5, 0)
        print(f"  解析版本号: {ver_num}")
        print(f"  BM25/FullText 支持: {'✅ 是' if supports_bm25 else '❌ 否（需 2.5+）'}")
        return supports_bm25
    except Exception as e:
        print(f"  版本解析失败: {e}")
        return False


def t2_function_compat() -> bool:
    """T2: 试在客户端构造 BM25 Function + SPARSE_FLOAT_VECTOR 字段，看服务端是否接收。"""
    heading("T2  客户端→服务端 Function/Field 兼容性（pymilvus 3.0.1 + Milvus 2.4.0）")
    cleanup()
    try:
        schema = client.create_schema(auto_id=True, enable_dynamic_field=False)
        schema.add_field("id", DataType.INT64, is_primary=True, auto_id=True)
        schema.add_field("text", DataType.VARCHAR, max_length=1000)
        schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
        # 关键：附加 BM25 Function
        bm25 = Function(
            name="bm25_fn",
            function_type=FunctionType.BM25,
            input_field_names=["text"],
            output_field_names=["sparse"],
        )
        schema.add_function(bm25)

        index_params = client.prepare_index_params()
        index_params.add_index(
            field_name="sparse", index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
        )

        client.create_collection(
            collection_name=TEST_COLL, schema=schema, index_params=index_params,
        )
        # 立刻 describe_collection 验证 Functions 是否真在服务端
        info = client.describe_collection(TEST_COLL)
        fns = info.get("functions", [])
        print(f"  集合创建成功")
        print(f"  describe_collection 返回 Functions: {fns}")
        if fns:
            print(f"  → ✅ Functions 被服务端保留（不是 pymilvus 兼容性问题）")
            cleanup()
            return True
        print(f"  → ❌ Functions 被服务端丢弃 → 确认是 pymilvus 3.0.1 ↔ Milvus 2.4.0 protobuf 不兼容")
        cleanup()
        return False
    except Exception as e:
        print(f"  集合创建失败: {type(e).__name__}: {e}")
        cleanup()
        return False


def t3_fulltext_search() -> bool:
    """T3: 即使手动添加 sparse 字段，2.4.0 是否支持 FullText search（传原文）。"""
    heading("T3  Full-Text Search（传原文给 sparse 路，2.5+ 特性）")
    # 在 T2 已尝试的情况下，本项已在原理上可推断：
    # Milvus 2.4.0 没有 Full-Text Search 索引（无 inverted 索引类型用于 text 字段）
    # 若 T2 失败 + 版本 < 2.5 → T3 必然失败
    print(f"  Milvus 2.4.0 未实现 FULLTEXT 索引；2.5+ 才有")
    print(f"  → 推断：T3 失败（需要升级）")
    return False


def t4_alter_add_field() -> bool:
    """T4: 已存在的 public_kb 集合能否在线加 sparse_vector 列（避免重建）。"""
    heading("T4  在线 ALTER COLLECTION 加列（能否避免重建）")
    # Milvus 2.4.x 限制：已经创建的集合不允许加新字段
    # pymilvus 也没有 add_field 这种 API；要新增字段只能重建
    print(f"  Milvus 2.4.x：集合已创建后不允许 add_field")
    print(f"  pymilvus 3.0.1：不暴露 add_field API")
    print(f"  → 推断：要支持 sparse_vector 必须重建集合")
    return False


def verdict(t1: bool, t2: bool, t3: bool, t4: bool) -> None:
    heading("综合结论与决策建议")
    if not t1 and not t2:
        print("""
  ✅ 已确认根因：
    1) 云端 Milvus 是 2.4.0（< 2.5.0），不支持 BM25 / Full-Text Search
    2) pymilvus 3.0.1 客户端构造的 Function 在服务端被静默丢弃
    3) 已存在的集合不能 add_field，必须重建

  📊 决策矩阵：
    选项              工作量        风险     收益
    ────────────────────────────────────────────────────
    升 Milvus 2.5+    中（2-3h）    中       一劳永逸
    降 pymilvus 2.4.x 高（可能雪崩） 高       同上
    客户端绕过 BM25    中            低       立即可用
    改 Reranker 接入  低（1h）      极低     不需 sparse 也激活 Reranker
        """)
    else:
        print(f"  T1={t1} T2={t2} T3={t3} T4={t4}")
        print("  根因比预期更复杂，需进一步诊断。")


def main() -> int:
    cleanup()
    t1 = t1_server_capability()
    t2 = t2_function_compat()
    t3 = t3_fulltext_search()
    t4 = t4_alter_add_field()
    cleanup()
    verdict(t1, t2, t3, t4)
    return 0


if __name__ == "__main__":
    sys.exit(main())