"""
Milvus 向量存储管理器。

约束：
  - 仅使用 pymilvus 3.x 的 MilvusClient 管理 collection 生命周期
  - 保留 langchain_milvus 包装器，供上层兼容现有 similarity_search 接口
  - 主键 = chunk_uid（内容派生稳定标识，auto_id=False）：同 uid 重复写入
    经 upsert 幂等覆盖，存储层杜绝重复行；doc_name 参与uid 派生，
    跨文档同文（合法重复）不会被误合并
"""

from __future__ import annotations

import logging
from typing import List, Optional

from langchain_core.documents import Document
from langchain_milvus import Milvus as MilvusVectorStore
from langchain_openai import OpenAIEmbeddings
from pymilvus import DataType, Function, FunctionType, MilvusClient

from .chunk_ids import compute_chunk_uid
from .config import Settings

logger = logging.getLogger(__name__)

_URI_FORMAT = "http://{host}:{port}"

# 主键字段：取值为 chunk_uid（ck-<32hex>，35 字符，64 上限留余量）
_PRIMARY_FIELD = "chunk_uid"
_PRIMARY_MAX_LENGTH = 64


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
        """全量重建：删除旧集合后按混合 schema 重建并写入全部文档。

        Schema 契约（主键化改造后）：
          - chunk_uid（VARCHAR 主键，auto_id=False，取值 = 内容派生 chunk_uid）
          - text / vector（稠密 FLOAT_VECTOR，IVF_FLAT + COSINE）
          - sparse_vector（SPARSE_FLOAT_VECTOR）— 由服务端 BM25 Function 依据
            text 字段自动生成，客户端禁止写入该字段
          - text 启用 jieba 中文分词（analyzer），供 BM25 Function tokenize
          - 建库后 describe 自检：sparse_vector 字段与 BM25 Function 必须同时
            在位，缺失即抛错（防止再次发生 Function 被静默丢弃 → 静默降级）

        适用场景：切分参数/schema 变更后的整体重建；日常重复写入请用
        upsert_documents（幂等，不 drop）。
        """
        logger.info(
            "全量重建 public_kb 混合检索集合（MilvusClient 模式），待入库 %d 个文档块",
            len(documents),
        )
        self._drop_if_exists()
        self._create_collection()
        self._upsert_batch(documents)
        self._client.flush(self._settings.collection_name)
        self._store = self._create_vector_store_wrapper()
        logger.info("public_kb 入库完成，共 %d 条记录", len(documents))

    def upsert_documents(
        self,
        documents: List[Document],
        replace_doc: Optional[str] = None,
    ) -> None:
        """幂等写入：集合不存在则先建；可选先删除指定 doc_name 的旧行。

        - 默认（replace_doc=None）：按 chunk_uid 主键 upsert，重复写入幂等覆盖，
          绝不产生重复行；
        - replace_doc 给定：先删除该文档名下的全部旧行再写入——用于"同一文档
          更新版本"的文档级替换（避免新旧版本并存）。
        """
        if not documents:
            logger.warning("upsert_documents 收到空文档列表，跳过")
            return
        if not self._has_collection():
            logger.info("集合不存在，先按混合 schema 创建")
            self._create_collection()
        if replace_doc:
            self._delete_by_doc_name(replace_doc)
        self._upsert_batch(documents)
        self._client.flush(self._settings.collection_name)
        self._store = self._create_vector_store_wrapper()
        logger.info(
            "public_kb upsert 完成，共写入 %d 条，当前总数 %d",
            len(documents),
            self.row_count(),
        )

    def _create_collection(self) -> None:
        """按混合 schema 建集合 + 双索引 + 加载 + describe 自检。"""
        schema = self._client.create_schema(
            auto_id=False,
            enable_dynamic_field=True,
        )
        schema.add_field(
            field_name=_PRIMARY_FIELD,
            datatype=DataType.VARCHAR,
            max_length=_PRIMARY_MAX_LENGTH,
            is_primary=True,
        )
        schema.add_field(
            field_name="text",
            datatype=DataType.VARCHAR,
            max_length=65535,
            enable_analyzer=True,
            analyzer_params={"type": "chinese"},
        )
        schema.add_field(
            field_name="vector",
            datatype=DataType.FLOAT_VECTOR,
            dim=self._settings.embedding_dim,
        )
        schema.add_field(
            field_name="sparse_vector",
            datatype=DataType.SPARSE_FLOAT_VECTOR,
        )
        # BM25 Function：服务端对 text 分词并生成稀疏向量（Milvus 2.5+ 能力）
        schema.add_function(Function(
            name="text_bm25",
            input_field_names=["text"],
            output_field_names=["sparse_vector"],
            function_type=FunctionType.BM25,
        ))

        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="vector",
            index_type="IVF_FLAT",
            metric_type="COSINE",
            params={"nlist": 256},
        )
        index_params.add_index(
            field_name="sparse_vector",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
        )
        self._client.create_collection(
            collection_name=self._settings.collection_name,
            schema=schema,
            index_params=index_params,
        )
        self._client.load_collection(self._settings.collection_name)

        # 建库自检：Function/稀疏字段必须在服务端真实生效（当年 2.4.0 服务端
        # 曾静默丢弃 Function，导致后续检索长期静默降级——此处强制显性失败）
        self._verify_mixed_schema()

    def _verify_mixed_schema(self) -> None:
        """建库后校验混合 schema 已真实落盘（sparse 字段 + BM25 Function + 双索引）。"""
        info = self._client.describe_collection(self._settings.collection_name)
        field_names = [f.get("name") for f in info.get("fields", [])]
        function_names = [fn.get("name") for fn in info.get("functions", [])]
        missing = []
        if "sparse_vector" not in field_names:
            missing.append("sparse_vector 字段")
        if "text_bm25" not in function_names:
            missing.append("text_bm25 BM25 Function")
        if _PRIMARY_FIELD not in field_names:
            missing.append(f"{_PRIMARY_FIELD} 主键字段")
        if missing:
            raise RuntimeError(
                f"Milvus 集合 '{self._settings.collection_name}' 混合 schema 校验失败，"
                f"服务端缺少：{'、'.join(missing)}。请确认 Milvus 服务端版本 ≥ 2.5"
                "（BM25 Function 为 2.5+ 能力，2.4 服务端会静默丢弃 Function），"
                "并检查 docker-compose 镜像版本。"
            )
        logger.info(
            "混合 schema 自检通过: 集合=%s, 主键=%s, sparse 字段=%s, function=%s",
            self._settings.collection_name, _PRIMARY_FIELD, field_names, function_names,
        )

    def _upsert_batch(self, documents: List[Document]) -> None:
        """批量向量化并按 chunk_uid 主键 upsert（含元数据守卫）。"""
        batch_size = max(1, int(self._settings.milvus_insert_batch))
        total = len(documents)
        written = 0
        seen_uids: set[str] = set()

        for start in range(0, total, batch_size):
            batch = documents[start:start + batch_size]
            texts = [doc.page_content for doc in batch]
            vectors = self._embeddings.embed_documents(texts)

            data = []
            for pos, (doc, vec) in enumerate(zip(batch, vectors)):
                # 守卫 1：doc_name 必填——缺失时 uid 会退化为纯文本哈希，
                # 导致跨文档同文被误合并（合法重复被覆盖删除）
                doc_name = str(doc.metadata.get("doc_name", "") or "").strip()
                if not doc_name:
                    raise ValueError(
                        "入库数据缺少 doc_name（位置 "
                        f"{start + pos}，text 前 30 字：{doc.page_content[:30]!r}）。"
                        "doc_name 参与 chunk_uid 派生，缺失将破坏唯一标识语义，拒绝入库。"
                    )
                # 守卫 2：chapter / chunk_index 缺省补齐，保证 uid 前像完整
                chapter = str(doc.metadata.get("chapter", "") or "")
                chunk_index = doc.metadata.get("chunk_index")
                if chunk_index is None:
                    chunk_index = start + pos

                uid = str(doc.metadata.get("chunk_uid") or "") or compute_chunk_uid(
                    doc.page_content,
                    {"doc_name": doc_name, "chapter": chapter, "chunk_index": chunk_index},
                )
                # 守卫 3：同一次调用内 uid 重复 = 输入数据存在重复行（数据缺陷），
                # fail-fast；跨调用重复由主键 upsert 幂等吸收
                if uid in seen_uids:
                    raise ValueError(
                        f"同一次入库调用中出现重复 chunk_uid={uid}（doc_name={doc_name!r}），"
                        "输入数据疑似包含重复块，拒绝写入。"
                    )
                seen_uids.add(uid)

                record = {
                    _PRIMARY_FIELD: uid,
                    "text": doc.page_content,
                    "vector": vec,
                    "doc_name": doc_name,
                    "chapter": chapter,
                    "chunk_index": int(chunk_index),
                }
                # 透传所有额外元数据字段（利用 enable_dynamic_field=True）
                for key, value in doc.metadata.items():
                    if key in (_PRIMARY_FIELD, "doc_name", "chapter", "chunk_index"):
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

            self._client.upsert(self._settings.collection_name, data)
            written += len(data)
            logger.debug("入库进度: %d/%d", written, total)

        logger.info("upsert 完成: %d 条记录（主键 %s，批大小 %d）", written, _PRIMARY_FIELD, batch_size)

    def _delete_by_doc_name(self, doc_name: str) -> None:
        """按 doc_name 删除该文档的全部旧行（文档级替换第一步）。"""
        escaped = doc_name.replace("\\", "\\\\").replace('"', '\\"')
        self._client.delete(
            self._settings.collection_name,
            filter=f'doc_name == "{escaped}"',
        )
        logger.info("已删除文档 doc_name=%r 的全部旧行", doc_name)

    def row_count(self) -> int:
        """当前集合行数（集合不存在时返回 0）。"""
        if not self._has_collection():
            return 0
        stats = self._client.get_collection_stats(self._settings.collection_name)
        return int(stats.get("row_count", 0) or 0)

    def add_documents(self, documents: List[Document]) -> None:
        """增量导入文档（幂等 upsert；集合不存在则先建）。"""
        logger.info("增量导入 %d 个文档块", len(documents))
        self.upsert_documents(documents)

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
            auto_id=False,
            vector_field="vector",
            text_field="text",
            primary_field=_PRIMARY_FIELD,
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
