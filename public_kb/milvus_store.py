"""
Milvus 向量存储管理器。

约束：
  - 仅使用 pymilvus 3.x 的 MilvusClient 管理 collection 生命周期
  - 保留 langchain_milvus 包装器，供上层兼容现有 similarity_search 接口
"""

from __future__ import annotations

import logging
from typing import List, Optional

from langchain_core.documents import Document
from langchain_milvus import Milvus as MilvusVectorStore
from langchain_openai import OpenAIEmbeddings
from pymilvus import DataType, MilvusClient

from .chunk_ids import compute_chunk_uid
from .config import Settings

logger = logging.getLogger(__name__)

_URI_FORMAT = "http://{host}:{port}"


class MilvusStoreManager:
    """管理 public_kb 集合的 schema、索引、批量入库与清空。"""

    def __init__(
        self,
        settings: Settings,
        embeddings: OpenAIEmbeddings,
    ) -> None:
        self._settings = settings
        self._embeddings = embeddings
        self._store: Optional[MilvusVectorStore] = None
        self._uri = _URI_FORMAT.format(
            host=settings.milvus_host,
            port=settings.milvus_port,
        )
        self._client = MilvusClient(uri=self._uri)

    def initialize_collection(self, documents: List[Document]) -> None:
        """创建 public_kb 集合并批量导入文档。"""
        logger.info("初始化 public_kb 集合（MilvusClient 模式），待入库 %d 个文档块", len(documents))
        self._drop_if_exists()

        schema = self._client.create_schema(
            auto_id=True,
            enable_dynamic_field=True,
        )
        schema.add_field(
            field_name="id",
            datatype=DataType.INT64,
            is_primary=True,
            auto_id=True,
        )
        schema.add_field(
            field_name="text",
            datatype=DataType.VARCHAR,
            max_length=65535,
        )
        schema.add_field(
            field_name="vector",
            datatype=DataType.FLOAT_VECTOR,
            dim=self._settings.embedding_dim,
        )

        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="vector",
            index_type="IVF_FLAT",
            metric_type="COSINE",
            params={"nlist": 256},
        )
        self._client.create_collection(
            collection_name=self._settings.collection_name,
            schema=schema,
            index_params=index_params,
        )
        self._client.load_collection(self._settings.collection_name)

        self._batch_insert(documents)
        self._client.flush(self._settings.collection_name)
        self._store = self._create_vector_store_wrapper()
        logger.info("public_kb 入库完成，共 %d 条记录", len(documents))

    def _batch_insert(self, documents: List[Document]) -> None:
        """批量向量化并插入 collection。"""
        batch_size = 100
        total = len(documents)
        inserted = 0

        for start in range(0, total, batch_size):
            batch = documents[start:start + batch_size]
            texts = [doc.page_content for doc in batch]
            vectors = self._embeddings.embed_documents(texts)

            data = []
            for doc, vec in zip(batch, vectors):
                # 基础字段
                record = {
                    "text": doc.page_content,
                    "vector": vec,
                    "doc_name": doc.metadata.get("doc_name", ""),
                    "chapter": doc.metadata.get("chapter", ""),
                    "chunk_index": doc.metadata.get("chunk_index", 0),
                    # 内容派生稳定标识（与检索侧 compute_chunk_uid 同口径）
                    "chunk_uid": doc.metadata.get("chunk_uid")
                    or compute_chunk_uid(doc.page_content, doc.metadata),
                }
                # 透传所有额外元数据字段（利用 enable_dynamic_field=True）
                for key, value in doc.metadata.items():
                    if key in ("doc_name", "chapter", "chunk_index", "chunk_uid"):
                        continue  # 已在上方显式设置
                    if key.startswith("_"):
                        continue  # 跳过内部字段
                    if value is None:
                        continue
                    # Milvus VARCHAR 最大 65535，截断过长值
                    if isinstance(value, str) and len(value) > 65535:
                        value = value[:65535]
                    record[key] = value
                data.append(record)

            self._client.insert(self._settings.collection_name, data)
            inserted += len(data)
            logger.debug("入库进度: %d/%d", inserted, total)

        logger.info("入库完成: %d 条记录已写入", inserted)

    def add_documents(self, documents: List[Document]) -> None:
        """增量导入文档。"""
        if not self._has_collection():
            logger.info("集合不存在，转为全量初始化")
            self.initialize_collection(documents)
            return

        if self._store is None:
            self.load_existing()

        logger.info("增量导入 %d 个文档块", len(documents))
        self._batch_insert(documents)
        self._client.flush(self._settings.collection_name)
        logger.info(
            "增量导入完成，当前集合总数约 %d",
            int(self._client.get_collection_stats(self._settings.collection_name).get("row_count", 0) or 0),
        )

    @property
    def collection(self) -> MilvusClient:
        """获取底层 MilvusClient 实例。"""
        if not self._has_collection():
            raise RuntimeError(
                f"集合 {self._settings.collection_name} 未初始化，"
                "请先调用 initialize_collection() 或 load_existing()。"
            )
        return self._client

    def load_existing(self) -> bool:
        """加载已存在的 public_kb 集合。"""
        try:
            if self._has_collection():
                self._client.load_collection(self._settings.collection_name)
                self._store = self._create_vector_store_wrapper()
                logger.info("已加载现有集合: %s", self._settings.collection_name)
                return True
        except Exception as e:
            logger.warning("加载集合失败: %s", e)
        return False

    def clear_collection(self) -> None:
        """清空 public_kb 集合（管理员操作）。"""
        self._drop_if_exists()
        self._store = None
        logger.info("public_kb 集合已清空")

    @property
    def store(self) -> MilvusVectorStore:
        """获取底层 MilvusVectorStore 实例。"""
        if self._store is None:
            raise RuntimeError(
                f"集合 {self._settings.collection_name} 未初始化，"
                "请先调用 initialize_collection() 或 load_existing()。"
            )
        return self._store

    def _create_vector_store_wrapper(self) -> MilvusVectorStore:
        return MilvusVectorStore(
            embedding_function=self._embeddings,
            collection_name=self._settings.collection_name,
            connection_args={
                "host": self._settings.milvus_host,
                "port": self._settings.milvus_port,
            },
            auto_id=True,
            vector_field="vector",
            text_field="text",
            primary_field="id",
        )

    def _has_collection(self) -> bool:
        try:
            return self._client.has_collection(self._settings.collection_name)
        except Exception:
            return False

    def _drop_if_exists(self) -> None:
        try:
            if self._client.has_collection(self._settings.collection_name):
                self._client.drop_collection(self._settings.collection_name)
                logger.info("已删除旧集合: %s", self._settings.collection_name)
        except Exception as e:
            logger.warning("删除集合时出错（可忽略）: %s", e)
