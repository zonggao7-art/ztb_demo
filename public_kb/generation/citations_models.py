# 功能：引用溯源子系统（M5 Step B 拆分）。
"""引用溯源模块 — 由原 citations.py(430行) 按职责拆分为：
  citations_models.py    pydantic 数据模型 + 标记解析 + 集合计算
  citations_build.py     引用构建 + 渲染
  citations_validate.py  CitationValidator 校验器
  citations.py           门面 re-export（既有导入路径保持可用）
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

# 回答中的内联引用标记，如【来源1】/【来源 2】（容忍空白）
_CITATION_MARKER_RE = re.compile(r"【\s*来源\s*(\d+)\s*】")

_UNKNOWN_DOC_NAME = "未知文档"
_UNKNOWN_CHAPTER = "未知章节"


# ============================================================
#  数据模型
# ============================================================

class Citation(BaseModel):
    """单条被引用 chunk 的标准化溯源信息（测评系统直接读取格式）。"""

    context_index: int = Field(description="上下文块编号，对应回答中【来源N】的 N（1 起）")
    chunk_id: Optional[int] = Field(default=None, description="Milvus 主键 id（行级唯一，用于回表验证）")
    chunk_uid: str = Field(description="内容派生稳定标识（跨集合重建不变，同内容重复行共享）")
    doc_name: str = Field(description="所属文档名称")
    chapter: str = Field(description="章节路径，如 '第一章 总则 > 第一条'")
    chunk_index: int = Field(default=-1, description="章节内块序号")
    text: str = Field(description="原文片段（完整 chunk 文本）")
    score: float = Field(default=0.0, description="检索相关度分数")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="全部附加元数据（source_file/source_url/publish_date 等）")

    def to_dict(self) -> Dict[str, Any]:
        """输出测评系统可读的 JSON 字典。"""
        return {
            "context_index": self.context_index,
            "chunk_id": self.chunk_id,
            "chunk_uid": self.chunk_uid,
            "doc_name": self.doc_name,
            "chapter": self.chapter,
            "chunk_index": self.chunk_index,
            "text": self.text,
            "score": self.score,
            "metadata": self.metadata,
        }


class RuleResult(BaseModel):
    """单条校验规则的结果。"""

    rule_id: str
    name: str
    description: str
    enabled: bool
    passed: bool
    detail: str = ""


class CitationValidationReport(BaseModel):
    """一次回答的完整引用校验报告。"""

    all_passed: bool
    is_refusal: bool = Field(default=False, description="是否拒答（拒答时 citations 合法为空）")
    context_chunks: int = Field(default=0, description="进入 LLM 上下文的 chunk 数")
    cited_markers: List[int] = Field(default_factory=list, description="回答中实际引用的【来源N】编号")
    uncited_chunks: List[int] = Field(default_factory=list, description="未被回答标记引用的上下文块编号")
    unknown_markers: List[int] = Field(default_factory=list, description="无法解析到有效引用的标记编号")
    rules: List[RuleResult] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "all_passed": self.all_passed,
            "is_refusal": self.is_refusal,
            "context_chunks": self.context_chunks,
            "cited_markers": self.cited_markers,
            "uncited_chunks": self.uncited_chunks,
            "unknown_markers": self.unknown_markers,
            "rules": [r.model_dump() for r in self.rules],
        }


def parse_citation_markers(answer: str) -> List[int]:
    """提取回答文本中的【来源N】标记，返回去重升序编号。

    Args:
        answer: LLM 生成的回答文本。

    Returns:
        编号列表（可能为空）。
    """
    if not answer:
        return []
    found = sorted({int(m) for m in _CITATION_MARKER_RE.findall(answer)})
    return found


def _compute_cited_sets(
    citations: List[Citation],
    answer: str,
) -> Tuple[List[int], List[int], List[int]]:
    """计算 (cited, uncited, unknown) 三集合。

    R6/R7 规则判定与 CitationValidationReport 的
    cited_markers / uncited_chunks / unknown_markers 共用同一份数据，
    避免两处重复计算（原实现中 validate 与 R6/R7 各算一遍）。
    """
    total = len(citations)
    cited = parse_citation_markers(answer)
    cited_set = set(cited)
    uncited = [i for i in range(1, total + 1) if i not in cited_set]
    unknown = [m for m in cited if m < 1 or m > total]
    return cited, uncited, unknown
