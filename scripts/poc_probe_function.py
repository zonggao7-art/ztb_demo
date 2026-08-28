"""POC 探测: 验证目标 Milvus 服务端真实承接 BM25 Function（可重复运行）。

用法: python scripts/poc_probe_function.py [uri]
默认 uri: http://localhost:19531（v2.6 POC 栈）
"""
import sys

from pymilvus import DataType, Function, FunctionType, MilvusClient

URI = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:19531"
COLL = "_probe_bm25_function"

client = MilvusClient(uri=URI)
print(f"探测目标: {URI} | 服务端版本: {client.get_server_version()}")
try:
    if client.has_collection(COLL):
        client.drop_collection(COLL)

    schema = client.create_schema(auto_id=True, enable_dynamic_field=False)
    schema.add_field("id", DataType.INT64, is_primary=True, auto_id=True)
    schema.add_field("text", DataType.VARCHAR, max_length=1000, enable_analyzer=True)
    schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
    schema.add_function(Function(
        name="text_bm25_emb",
        input_field_names=["text"],
        output_field_names=["sparse_vector"],
        function_type=FunctionType.BM25,
    ))
    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name="sparse_vector",
        index_type="SPARSE_INVERTED_INDEX",
        metric_type="BM25",
    )
    client.create_collection(COLL, schema=schema, index_params=index_params)

    info = client.describe_collection(COLL)
    fields = [f.get("name") for f in info.get("fields", [])]
    functions = info.get("functions") or []
    function_names = [f.get("name") for f in functions]
    print("fields:", fields)
    print("functions:", function_names)

    passed = (
        "sparse_vector" in fields
        and "text_bm25_emb" in function_names
    )
    print("PROBE:", "PASS" if passed else "FAIL")
    sys.exit(0 if passed else 1)
finally:
    try:
        if client.has_collection(COLL):
            client.drop_collection(COLL)
            print("临时集合已清理")
    except Exception as exc:
        print(f"清理失败（可手动 drop {COLL}）: {exc}")
