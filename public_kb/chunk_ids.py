"""
Chunk 稳定标识工具 — 内容派生的业务唯一 chunk_uid。

背景（见 docs/数据基础分析与核心逻辑审计报告_20260814.md）：
  - Milvus 主键 id 为 auto_id 自增，无业务含义，且重建集合后会变化；
  - chunk_index 仅在同一 chapter 内递增，实测 99.4% 为 0，不可作全局唯一键；
  - 55.57% 的 chunk 内容重复（同源多快照导入 + 法规条文跨文档引用）。

方案：
  chunk_uid 由 (doc_name, chapter, chunk_index, 内容哈希) 确定性派生，
  入库时写入动态字段固化；检索时对存量数据用同一函数即时计算，
  保证新旧数据口径一致、跨集合重建稳定：
    - 同一内容的重复行（同 doc/chapter/index/text）共享同一 uid → 供测评去重检测；
    - 不同文档引用的同一条文（doc_name 不同）得到不同 uid → 保留行级可区分性。

注意：text 的规范化口径必须与检索/入库两侧完全一致（strip + 统一换行），
否则同一 chunk 会算出两个 uid。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict

_UID_PREFIX = "ck-"

# 统一换行：\r\n / \r → \n（与审计报告去重检测口径一致）
_NEWLINE_RE = re.compile(r"\r\n|\r")


def normalize_chunk_text(text: str) -> str:
    """规范化 chunk 文本：去首尾空白 + 统一换行。

    Args:
        text: 原始 chunk 文本。

    Returns:
        规范化后的文本（仅用于哈希/比对，不改变存储原文）。
    """
    if not text:
        return ""
    return _NEWLINE_RE.sub("\n", text).strip()


def compute_text_hash(text: str) -> str:
    """计算规范化文本的 MD5 哈希（16 进制 32 位）。

    Args:
        text: 原始 chunk 文本。

    Returns:
        MD5 十六进制字符串。
    """
    return hashlib.md5(
        normalize_chunk_text(text).encode("utf-8")
    ).hexdigest()


def compute_chunk_uid(text: str, metadata: Dict[str, Any]) -> str:
    """从 chunk 文本与元数据确定性派生业务唯一标识。

    格式: ck-<md5(doc_name|chapter|chunk_index|md5(text)[:16])>

    Args:
        text: chunk 原文。
        metadata: Document.metadata（需含 doc_name / chapter / chunk_index）。

    Returns:
        chunk_uid 字符串。
    """
    doc_name = str(metadata.get("doc_name", ""))
    chapter = str(metadata.get("chapter", ""))
    chunk_index = str(metadata.get("chunk_index", -1))
    text_key = compute_text_hash(text)[:16]
    base = f"{doc_name}|{chapter}|{chunk_index}|{text_key}"
    return _UID_PREFIX + hashlib.md5(base.encode("utf-8")).hexdigest()
