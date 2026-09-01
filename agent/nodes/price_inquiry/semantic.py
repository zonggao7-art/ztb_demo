"""MySQL 结构化语义检索 — Milvus 集合的 bootstrap、状态检查与语义召回。"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import Any, Optional

import pymysql
from langchain_openai import OpenAIEmbeddings
from pymilvus import DataType, MilvusClient

from public_kb.config import Settings
from public_kb.embedding_service import create_embeddings

from .db import _CLEAN_DB, _get_connection, _get_settings, _release_connection
from .models import SearchIntent
from .schema import _HARDCODED_SCHEMA, _semantic_columns

logger = logging.getLogger(__name__)

# 询价语义检索参数 — 统一由 Settings 提供（.env 可覆盖，见 public_kb.config），
# 保持模块级常量名以兼容包内 re-export。
_MYSQL_SEMANTIC_COLLECTION = _get_settings().mysql_semantic_collection

_MYSQL_SEMANTIC_BATCH_SIZE = _get_settings().mysql_semantic_batch_size

_MYSQL_SEMANTIC_TOP_K = _get_settings().mysql_semantic_top_k

_MYSQL_SEMANTIC_PER_TABLE_LIMIT = _get_settings().mysql_semantic_per_table_limit

_MYSQL_SEMANTIC_TEXT_TRUNCATE = _get_settings().mysql_semantic_text_truncate

_MYSQL_SEMANTIC_THRESHOLD = _get_settings().mysql_semantic_threshold

_EMBEDDINGS_CACHE: Optional[OpenAIEmbeddings] = None

_SEMANTIC_MILVUS_CLIENT: Optional[MilvusClient] = None

_SEMANTIC_EXPECTED_ROW_COUNT: Optional[int] = None

_SEMANTIC_BOOTSTRAP_ATTEMPTED = False

_SEMANTIC_BOOTSTRAP_IN_PROGRESS = False

def _get_embeddings() -> OpenAIEmbeddings:
    """懒加载 Embedding 客户端，供 P1-3 语义召回复用。"""
    global _EMBEDDINGS_CACHE
    if _EMBEDDINGS_CACHE is None:
        _EMBEDDINGS_CACHE = create_embeddings(_get_settings())
    return _EMBEDDINGS_CACHE

def _get_milvus_uri(settings: Settings) -> str:
    return f"http://{settings.milvus_host}:{settings.milvus_port}"

def _get_semantic_milvus_client(settings: Settings) -> Optional[MilvusClient]:
    global _SEMANTIC_MILVUS_CLIENT
    if _SEMANTIC_MILVUS_CLIENT is None:
        try:
            _SEMANTIC_MILVUS_CLIENT = MilvusClient(uri=_get_milvus_uri(settings))
        except Exception as e:
            logger.warning("连接 MySQL 语义 Milvus 失败: %s", e)
            return None
    return _SEMANTIC_MILVUS_CLIENT

def _build_semantic_document_text(
    table: str,
    row: dict[str, Any],
    classification: dict[str, list[str]],
) -> str:
    parts = [f"source_table:{table}"]
    for col in _semantic_columns(classification):
        value = row.get(col)
        if value in (None, ""):
            continue
        parts.append(f"{col}:{value}")
    return " | ".join(parts)

def _build_semantic_select_fields(
    classification: dict[str, list[str]],
) -> list[str]:
    cols = _semantic_columns(classification)
    if not cols:
        return []

    select_fields = []
    for col in cols:
        if col in classification.get("text", []) or col in classification.get("semantic", []):
            select_fields.append(
                f"LEFT(`{col}`, {_MYSQL_SEMANTIC_TEXT_TRUNCATE}) AS `{col}`"
            )
        else:
            select_fields.append(f"`{col}`")
    return select_fields

def _count_semantic_source_rows(
    conn: pymysql.Connection,
    table: str,
) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM `{table}`")
        row = cur.fetchone()
        return int(row[0] if row else 0)

def _get_expected_semantic_row_count() -> Optional[int]:
    global _SEMANTIC_EXPECTED_ROW_COUNT
    if _SEMANTIC_EXPECTED_ROW_COUNT is not None:
        return _SEMANTIC_EXPECTED_ROW_COUNT

    conn = _get_connection(_CLEAN_DB)
    if conn is None:
        return None

    try:
        total = 0
        for table in _HARDCODED_SCHEMA:
            total += _count_semantic_source_rows(conn, table)
        _SEMANTIC_EXPECTED_ROW_COUNT = total
        return total
    except Exception as e:
        logger.warning("统计 MySQL 语义源数据行数失败: %s", e)
        return None
    finally:
        _release_connection(conn)

def _iter_semantic_source_rows(
    conn: pymysql.Connection,
    table: str,
    classification: dict[str, list[str]],
    *,
    fetch_size: int = 500,
) -> Any:
    select_fields = _build_semantic_select_fields(classification)
    if not select_fields:
        return

    id_col = classification.get("id", ["id"])[0]
    sql = f"SELECT {', '.join(select_fields)} FROM `{table}` ORDER BY `{id_col}` ASC"
    with conn.cursor(pymysql.cursors.SSDictCursor) as cur:
        cur.execute(sql)
        while True:
            batch = cur.fetchmany(fetch_size)
            if not batch:
                break
            yield batch

def _create_mysql_semantic_collection(
    client: MilvusClient,
    settings: Settings,
) -> None:
    schema = client.create_schema(
        auto_id=False,
        enable_dynamic_field=False,
    )
    schema.add_field(
        field_name="pk",
        datatype=DataType.VARCHAR,
        max_length=128,
        is_primary=True,
    )
    schema.add_field(
        field_name="source_table",
        datatype=DataType.VARCHAR,
        max_length=64,
    )
    schema.add_field(
        field_name="source_id",
        datatype=DataType.INT64,
    )
    schema.add_field(
        field_name="text",
        datatype=DataType.VARCHAR,
        max_length=65535,
    )
    schema.add_field(
        field_name="vector",
        datatype=DataType.FLOAT_VECTOR,
        dim=settings.embedding_dim,
    )

    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name="vector",
        index_type="IVF_FLAT",
        metric_type="COSINE",
        params={"nlist": 256},
    )
    client.create_collection(
        collection_name=_MYSQL_SEMANTIC_COLLECTION,
        schema=schema,
        index_params=index_params,
    )
    client.load_collection(_MYSQL_SEMANTIC_COLLECTION)

def _semantic_collection_row_count(client: MilvusClient) -> int:
    stats = client.get_collection_stats(_MYSQL_SEMANTIC_COLLECTION)
    return int(stats.get("row_count", 0) or 0)

def _is_mysql_semantic_collection_ready(client: MilvusClient) -> bool:
    if not client.has_collection(_MYSQL_SEMANTIC_COLLECTION):
        return False

    row_count = _semantic_collection_row_count(client)
    expected = _get_expected_semantic_row_count()
    if expected is None:
        return row_count > 0
    return row_count >= expected

def _embed_semantic_batch(
    embeddings: OpenAIEmbeddings,
    texts: list[str],
    *,
    max_attempts: int = 6,
) -> list[list[float]]:
    for attempt in range(1, max_attempts + 1):
        try:
            return embeddings.embed_documents(texts)
        except Exception as e:
            message = str(e).lower()
            is_rate_limited = "429" in message or "rate limit" in message or "tpm limit" in message
            if not is_rate_limited or attempt >= max_attempts:
                raise

            sleep_seconds = min(90, 15 * attempt)
            logger.warning(
                "[SEMANTIC_BOOTSTRAP] Embedding 限流，第 %d/%d 次重试，%ds 后继续",
                attempt,
                max_attempts,
                sleep_seconds,
            )
            time.sleep(sleep_seconds)

    raise RuntimeError("语义批量向量化重试耗尽")

def _rebuild_mysql_semantic_collection() -> bool:
    global _SEMANTIC_BOOTSTRAP_IN_PROGRESS
    settings = _get_settings()
    client = _get_semantic_milvus_client(settings)
    if client is None:
        _SEMANTIC_BOOTSTRAP_IN_PROGRESS = False
        return False

    conn = _get_connection(_CLEAN_DB)
    if conn is None:
        logger.warning("MySQL 语义集合构建失败：无法连接 %s", _CLEAN_DB)
        _SEMANTIC_BOOTSTRAP_IN_PROGRESS = False
        return False

    try:
        expected_total = _get_expected_semantic_row_count()
        if client.has_collection(_MYSQL_SEMANTIC_COLLECTION):
            client.drop_collection(_MYSQL_SEMANTIC_COLLECTION)

        _create_mysql_semantic_collection(client, settings)
        embeddings = _get_embeddings()
        inserted = 0

        for table, classification in _HARDCODED_SCHEMA.items():
            total_rows = _count_semantic_source_rows(conn, table)
            id_col = classification.get("id", ["id"])[0]
            table_inserted = 0
            table_processed = 0
            batch_index = 0

            logger.info(
                "[SEMANTIC_BOOTSTRAP] 开始构建表 %s，源记录数=%d",
                table,
                total_rows,
            )
            for batch_rows in _iter_semantic_source_rows(
                conn,
                table,
                classification,
                fetch_size=_MYSQL_SEMANTIC_BATCH_SIZE,
            ):
                batch_index += 1
                docs: list[dict[str, Any]] = []
                batch_texts: list[str] = []
                for row in batch_rows:
                    table_processed += 1
                    text = _build_semantic_document_text(table, row, classification)
                    if not text.strip():
                        continue
                    try:
                        source_id = int(row.get(id_col) or 0)
                    except Exception:
                        continue
                    docs.append(
                        {
                            "pk": f"{table}:{source_id}",
                            "source_table": table,
                            "source_id": source_id,
                            "text": text,
                        }
                    )
                    batch_texts.append(text)

                if not docs:
                    continue

                batch_vectors = _embed_semantic_batch(embeddings, batch_texts)
                payload = []
                for doc, vec in zip(docs, batch_vectors):
                    payload.append(
                        {
                            "pk": doc["pk"],
                            "source_table": doc["source_table"],
                            "source_id": doc["source_id"],
                            "text": doc["text"],
                            "vector": vec,
                        }
                    )
                client.insert(_MYSQL_SEMANTIC_COLLECTION, payload)
                inserted += len(payload)
                table_inserted += len(payload)

                if batch_index % 20 == 0 or table_processed >= total_rows:
                    logger.info(
                        "[SEMANTIC_BOOTSTRAP] 表 %s 进度 %d/%d，已入库=%d",
                        table,
                        table_processed,
                        total_rows,
                        table_inserted,
                    )

            client.flush(_MYSQL_SEMANTIC_COLLECTION)
            logger.info(
                "[SEMANTIC_BOOTSTRAP] 表 %s 构建完成，入库=%d",
                table,
                table_inserted,
            )

        client.flush(_MYSQL_SEMANTIC_COLLECTION)
        client.load_collection(_MYSQL_SEMANTIC_COLLECTION)
        final_count = _semantic_collection_row_count(client)
        if expected_total is not None and final_count < expected_total:
            logger.warning(
                "[SEMANTIC_BOOTSTRAP] MySQL 语义集合构建未完成，期望=%d，实际=%d",
                expected_total,
                final_count,
            )
            return False
        logger.info(
            "[SEMANTIC_BOOTSTRAP] MySQL 语义集合构建完成，记录数=%d",
            final_count,
        )
        return True
    except Exception as e:
        logger.warning("构建 MySQL 语义集合失败: %s", e)
        return False
    finally:
        _SEMANTIC_BOOTSTRAP_IN_PROGRESS = False
        _release_connection(conn)

def _bootstrap_mysql_semantic_collection_async() -> None:
    global _SEMANTIC_BOOTSTRAP_IN_PROGRESS
    if _SEMANTIC_BOOTSTRAP_IN_PROGRESS:
        return
    _SEMANTIC_BOOTSTRAP_IN_PROGRESS = True
    thread = threading.Thread(
        target=_rebuild_mysql_semantic_collection,
        name="mysql-semantic-bootstrap",
        daemon=True,
    )
    thread.start()

def _ensure_mysql_semantic_collection() -> bool:
    global _SEMANTIC_BOOTSTRAP_ATTEMPTED

    settings = _get_settings()
    client = _get_semantic_milvus_client(settings)
    if client is None:
        return False

    try:
        if _is_mysql_semantic_collection_ready(client):
            client.load_collection(_MYSQL_SEMANTIC_COLLECTION)
            return True
        if client.has_collection(_MYSQL_SEMANTIC_COLLECTION):
            logger.warning(
                "[SEMANTIC_BOOTSTRAP] 语义集合存在但未完成，当前记录数=%d，期望记录数=%s",
                _semantic_collection_row_count(client),
                _get_expected_semantic_row_count(),
            )
    except Exception as e:
        logger.warning("检查 MySQL 语义集合状态失败: %s", e)

    if _SEMANTIC_BOOTSTRAP_ATTEMPTED or not _get_settings().enable_auto_semantic_bootstrap:
        return False

    _SEMANTIC_BOOTSTRAP_ATTEMPTED = True
    logger.info("[SEMANTIC_BOOTSTRAP] 语义集合不存在，已启动后台自举任务")
    _bootstrap_mysql_semantic_collection_async()
    return False

def _build_semantic_query_text(intent: SearchIntent) -> str:
    """P1-1：优先使用实体词（exact_tokens + semantic_keywords）构造查询向量。

    疑问句式（“中标过什么项目？”）会稀释核心实体语义，
    因此仅在无实体词时回退到原始问题。
    """
    entity_parts = [token for token in intent.exact_tokens if token]
    entity_parts += [kw for kw in intent.semantic_keywords if kw]
    if entity_parts:
        return " ".join(entity_parts)
    return intent.original_question

def _semantic_recall_candidates(
    intent: SearchIntent,
    tables: list[str],
) -> dict[str, dict[str, float]]:
    """P1-3：从 Milvus 召回语义相近的主键，供 MySQL 回表。"""
    if not tables:
        return {}
    if not _ensure_mysql_semantic_collection():
        return {}

    settings = _get_settings()
    try:
        client = _get_semantic_milvus_client(settings)
        if client is None:
            return {}
        query_vector = _get_embeddings().embed_query(_build_semantic_query_text(intent))
        expr = "source_table in [" + ", ".join(f'"{table}"' for table in tables) + "]"
        raw_hits = client.search(
            _MYSQL_SEMANTIC_COLLECTION,
            data=[query_vector],
            anns_field="vector",
            search_params={
                "metric_type": "COSINE",
                "params": {"nprobe": settings.nprobe},
            },
            limit=_MYSQL_SEMANTIC_TOP_K,
            filter=expr,
            output_fields=["source_table", "source_id", "text"],
        )[0]

        result: dict[str, dict[str, float]] = defaultdict(dict)
        per_table_counter: dict[str, int] = defaultdict(int)
        for hit in raw_hits:
            score = float(hit.score or 0.0)
            if score < _MYSQL_SEMANTIC_THRESHOLD:
                continue
            entity = hit.entity
            source_table = str(entity.get("source_table") or "")
            source_id = str(entity.get("source_id") or "")
            if not source_table or not source_id:
                continue
            if (
                per_table_counter[source_table] >= _MYSQL_SEMANTIC_PER_TABLE_LIMIT
                and source_id not in result[source_table]
            ):
                continue
            result[source_table][source_id] = max(
                result[source_table].get(source_id, 0.0), score
            )
            per_table_counter[source_table] = len(result[source_table])

        if result:
            logger.info(
                "[SEMANTIC_RECALL] tables=%s 命中=%s",
                tables,
                {table: len(ids) for table, ids in result.items()},
            )
        return dict(result)
    except Exception as e:
        logger.warning("Milvus 语义召回失败: %s", e)
        return {}
