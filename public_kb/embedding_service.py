"""
向量化服务 — 统一的 Embedding 接口封装。

支持 OpenAI / SiliconFlow / 任意兼容 OpenAI API 格式的嵌入服务。
通过 config 中的 embedding_api_key / embedding_base_url 切换。

安全机制：
  - 自动截断超过 _MAX_TEXT_CHARS 字符的文本，防止 bge 系列模型
    （512 token 上限）对超长文本返回 400 错误。
"""

from __future__ import annotations

import logging
from typing import Optional

from langchain_openai import OpenAIEmbeddings

from .config import Settings

logger = logging.getLogger(__name__)

# bge-m3 模型限制 8192 token，中文约 1 token/字
# 留足余量设为 2000，确保长文本不被截断
_MAX_TEXT_CHARS = 2000


class _SafeEmbeddings(OpenAIEmbeddings):
    """OpenAIEmbeddings 子类，对超长文本自动截断，防止 400 错误。"""

    def embed_documents(
        self,
        texts: list[str],
        chunk_size: Optional[int] = None,
        **kwargs: object,
    ) -> list[list[float]]:
        safe_texts = [
            t[:_MAX_TEXT_CHARS] if len(t) > _MAX_TEXT_CHARS else t for t in texts
        ]
        truncated = sum(1 for t in texts if len(t) > _MAX_TEXT_CHARS)
        if truncated:
            logger.debug(
                "Embedding 安全截断: %d 条文本超出 %d 字符限制",
                truncated, _MAX_TEXT_CHARS,
            )
        return super().embed_documents(safe_texts, chunk_size=chunk_size, **kwargs)

    def embed_query(self, text: str) -> list[float]:
        safe = text[:_MAX_TEXT_CHARS] if len(text) > _MAX_TEXT_CHARS else text
        return super().embed_query(safe)


def create_embeddings(settings: Settings) -> OpenAIEmbeddings:
    """根据配置创建 OpenAIEmbeddings 实例。

    兼容 OpenAI 原生 API 及第三方兼容服务（如 SiliconFlow、CloseAI）。
    通过设置 EMBEDDING_BASE_URL 环境变量即可切换后端。

    返回的实例内置超长文本保护，不会因单条文本超出模型 token 限制而报错。

    Args:
        settings: 全局配置。

    Returns:
        配置好的 OpenAIEmbeddings 实例（实际为 _SafeEmbeddings）。
    """
    kwargs: dict = {
        "model": settings.embedding_model,
        "api_key": settings.embedding_api_key,
        # 关闭 token 长度检查，否则 langchain 会先把文本转为 token ID 再发送
        # SiliconFlow 等第三方服务不接受 token ID 格式，只接受原始文本
        "check_embedding_ctx_length": False,
        # 标准化超时与重试配置
        "timeout": settings.embedding_timeout,
        "max_retries": settings.embedding_max_retries,
    }

    # 若有自定义 base_url 则传入（如 SiliconFlow / CloseAI）
    if settings.embedding_base_url:
        kwargs["base_url"] = settings.embedding_base_url

    logger.info(
        "初始化 Embedding: model=%s, base_url=%s, timeout=%ds, max_retries=%d",
        settings.embedding_model,
        settings.embedding_base_url or "OpenAI 默认",
        settings.embedding_timeout,
        settings.embedding_max_retries,
    )

    return _SafeEmbeddings(**kwargs)
