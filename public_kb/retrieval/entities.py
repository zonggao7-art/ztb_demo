# 功能：把 Milvus 命中实体规范化为带完整 metadata 的 Document。
"""Normalization helpers for Milvus retrieval entities."""

from __future__ import annotations

from typing import Any, Dict

from langchain_core.documents import Document

from ..chunk_ids import compute_chunk_uid


_EXCLUDED_META_KEYS = ("text", "vector", "sparse_vector", "id", "distance", "entity")


def normalize_hit_entity(entity: Any) -> Dict[str, Any]:
    """归一化 pymilvus 3.x 检索命中实体。

    MilvusClient.search/hybrid_search 的 Hit.entity 为嵌套结构:
        {"id": int, "distance": float, "entity": {实际标量字段...}}
    实际字段在内层 entity 中；get() 查询返回的行则是平铺结构。
    本函数统一为平铺 dict（内层字段 + distance 兜底）。
    """
    if isinstance(entity, dict) and isinstance(entity.get("entity"), dict):
        merged = dict(entity["entity"])
        merged.setdefault("distance", entity.get("distance"))
        merged.setdefault("id", entity.get("id"))
        return merged
    return dict(entity) if isinstance(entity, dict) else {}


def entity_to_doc(entity: Any, score: float) -> Document:
    """将 Milvus 检索命中实体转换为携带完整溯源元数据的 Document。

    元数据写入约定：
      - chunk_id   : Milvus 主键 id（行级唯一，回表验证"错误关联"用）
      - chunk_uid  : 内容派生稳定标识（存量数据无此字段时即时计算，与入库侧同口径）
      - doc_name / chapter / chunk_index : 数据源位置
      - 其余动态字段（source_file / source_url / publish_date 等）原样透传

    Args:
        entity: Milvus 命中实体（pymilvus 3.x Hit.entity 嵌套结构或平铺 dict）。
        score: 检索相关度分数。

    Returns:
        带完整溯源元数据的 Document。
    """
    entity = normalize_hit_entity(entity)
    text = str(entity.get("text", "") or "")

    meta: Dict[str, Any] = {}
    for key, value in entity.items():
        if key in _EXCLUDED_META_KEYS:
            continue
        if value is None:
            continue
        meta[key] = value

    meta["chunk_id"] = entity.get("id")
    meta.setdefault("doc_name", "未知文档")
    meta.setdefault("chapter", "未知章节")
    meta.setdefault("chunk_index", -1)

    if not meta.get("chunk_uid"):
        meta["chunk_uid"] = compute_chunk_uid(text, meta)

    return Document(page_content=text, metadata=meta)
