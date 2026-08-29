"""
引用溯源模型与校验规则 — 回答附带被引用 chunk 的完整来源信息。

对外核心：
  - build_citations(docs_with_scores)  → 将检索结果标准化为 Citation 列表
  - parse_citation_markers(answer)     → 提取回答中的【来源N】标记
  - CitationValidator.validate(...)    → 输出结构化校验报告

校验规则集（法规类专业场景，配置见 config.CitationRuleConfig）：
  R1 chunk_id_present          每条引用必须有 Milvus 行级 chunk_id（完整性）
  R2 chunk_uid_present         每条引用必须有内容派生 chunk_uid
  R3 source_location_present   数据源位置（doc_name/chapter）必须可定位
  R4 full_text_present         原文片段必须完整非空
  R5 context_fully_cited       无遗漏 — 进入 LLM 上下文的 chunk 全部出现在引用中
  R6 no_unknown_markers        回答中【来源N】全部解析到有效引用（无错误关联/幻觉引用）
  R7 all_context_marked        严格模式 — 上下文 chunk 全部被回答标记引用

所有规则 fail-soft：只产出结构化报告，不阻断回答返回；
测评系统直接读取 citation_validation 做可信度与出处合规性评估。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.documents import Document
from pydantic import BaseModel, Field

from ..chunk_ids import compute_chunk_uid
from ..config import CitationRuleConfig

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


# ============================================================
#  构建与解析
# ============================================================

def build_citations(
    docs_with_scores: List[Tuple[Document, float]],
) -> List[Citation]:
    """将检索结果（Document, score）标准化为 Citation 列表。

    Args:
        docs_with_scores: (Document, similarity_score) 列表，顺序与
            LLM 上下文 [来源N] 编号一一对应（第 i 项对应 来源{i+1}）。

    Returns:
        Citation 列表。
    """
    citations: List[Citation] = []
    for i, (doc, score) in enumerate(docs_with_scores, 1):
        meta = doc.metadata or {}
        chunk_uid = str(meta.get("chunk_uid", "") or "") or compute_chunk_uid(
            doc.page_content, meta
        )

        # 顶层字段之外的元数据全部透传（跳过内部字段与向量）
        extra: Dict[str, Any] = {}
        for key, value in meta.items():
            if key in (
                "doc_name", "chapter", "chunk_index",
                "chunk_id", "chunk_uid", "score", "rrf_score",
            ):
                continue
            if key.startswith("_"):
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                extra[key] = value

        citations.append(Citation(
            context_index=i,
            chunk_id=meta.get("chunk_id"),
            chunk_uid=chunk_uid,
            doc_name=str(meta.get("doc_name", "") or _UNKNOWN_DOC_NAME),
            chapter=str(meta.get("chapter", "") or _UNKNOWN_CHAPTER),
            chunk_index=int(meta.get("chunk_index", -1)),
            text=doc.page_content,
            score=round(float(score), 4),
            metadata=extra,
        ))
    return citations


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


def format_citations(
    citations: List[Dict[str, Any]],
    max_text_chars: Optional[int] = None,
) -> str:
    """将标准化引用列表渲染为可读文本块（呈现层使用）。

    输出保留【来源N】标签，逐条附带：
      - 所属文档名称、章节路径、块索引编号（数据源位置）
      - 关联元数据（页码、数据源文件、来源链接、发布日期、chunk_id/chunk_uid 等）
      - 绑定 chunk 的原文片段

    Args:
        citations: Citation.to_dict() 列表（或同构 dict）。
        max_text_chars: 原文片段最大展示字符数；None 表示完整输出。

    Returns:
        多行文本块；citations 为空时返回空字符串。
    """
    if not citations:
        return ""

    lines: List[str] = []
    lines.append(f"📚 引用来源（共 {len(citations)} 条）")
    lines.append("")

    for c in citations:
        idx = c.get("context_index", "")
        doc = c.get("doc_name") or "未知文档"
        chapter = c.get("chapter") or ""
        chunk_index = c.get("chunk_index")
        text = str(c.get("text") or "")

        header = f"【来源{idx}】{doc}"
        if chapter:
            header += f"｜{chapter}"
        if chunk_index is not None and chunk_index != -1:
            header += f"（块 {chunk_index}）"
        lines.append(header)

        meta_lines: List[str] = []
        meta = c.get("metadata") or {}
        if meta.get("page_number"):
            meta_lines.append(f"页码: {meta['page_number']}")
        if meta.get("title") and meta.get("title") != doc:
            meta_lines.append(f"标题: {meta['title']}")
        if meta.get("source_file"):
            meta_lines.append(f"数据源文件: {meta['source_file']}")
        if meta.get("source_url"):
            meta_lines.append(f"来源链接: {meta['source_url']}")
        if meta.get("publish_date"):
            meta_lines.append(f"发布日期: {meta['publish_date']}")
        if meta.get("publish_time"):
            meta_lines.append(f"发布时间: {meta['publish_time']}")
        if c.get("chunk_id") is not None:
            meta_lines.append(f"chunk_id: {c['chunk_id']}")
        if c.get("chunk_uid"):
            meta_lines.append(f"chunk_uid: {c['chunk_uid']}")
        if meta_lines:
            lines.append("  " + "\n  ".join(meta_lines))

        if max_text_chars and len(text) > max_text_chars:
            lines.append(f"  原文: {text[:max_text_chars]}…")
        else:
            lines.append(f"  原文: {text}")
        lines.append("")

    return "\n".join(lines).rstrip()


# ============================================================
#  校验器
# ============================================================

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


class CitationValidator:
    """引用来源校验器 — 规则集可经 CitationRuleConfig 启停。"""

    def __init__(self, config: Optional[CitationRuleConfig] = None) -> None:
        self._config = config or CitationRuleConfig()

    def validate(
        self,
        citations: List[Citation],
        answer: str,
        context_chunk_ids: Optional[List[Optional[int]]] = None,
        *,
        is_refusal: bool = False,
    ) -> CitationValidationReport:
        """对一次回答执行全部启用的引用校验规则。

        Args:
            citations: 标准化引用列表。
            answer: LLM 回答文本。
            context_chunk_ids: 进入 LLM 上下文的全部 chunk 的 chunk_id
                （顺序对应上下文编号），用于 R5 无遗漏校验。
            is_refusal: 是否拒答（拒答时 citations 合法为空）。

        Returns:
            CitationValidationReport 结构化报告。
        """
        context_ids = context_chunk_ids or []
        cited, uncited, unknown = _compute_cited_sets(citations, answer)

        # 各规则判定（R7 关闭时也计算，仅不计入 all_passed）
        r1 = CitationValidator._make_rule(
            "R1_chunk_id_present", "chunk_id 完整性",
            "每条引用必须携带 Milvus 行级 chunk_id",
            [c.context_index for c in citations if c.chunk_id is None],
            "缺失 chunk_id 的引用",
        )
        r2 = CitationValidator._make_rule(
            "R2_chunk_uid_present", "chunk_uid 完整性",
            "每条引用必须携带内容派生稳定标识 chunk_uid",
            [c.context_index for c in citations if not c.chunk_uid],
            "缺失 chunk_uid 的引用",
        )
        r3 = CitationValidator._make_rule(
            "R3_source_location_present", "数据源位置可定位",
            "doc_name/chapter 必须非空且非占位值",
            [
                c.context_index for c in citations
                if not c.doc_name or c.doc_name == _UNKNOWN_DOC_NAME
                or not c.chapter or c.chapter == _UNKNOWN_CHAPTER
            ],
            "数据源位置缺失的引用",
        )
        r4 = CitationValidator._make_rule(
            "R4_full_text_present", "原文片段完整",
            "每条引用必须携带非空完整原文",
            [c.context_index for c in citations if not c.text.strip()],
            "原文缺失的引用",
        )
        r5 = self._check_context_fully_cited(citations, context_ids)
        r6 = CitationValidator._make_rule(
            "R6_no_unknown_markers", "引用标记有效性",
            "回答中【来源N】标记必须全部解析到有效引用，无错误关联",
            unknown, "无效标记",
        )
        r7 = CitationValidator._make_rule(
            "R7_all_context_marked", "上下文全部被标记引用",
            "严格模式：上下文 chunk 必须全部被回答【来源N】标记引用",
            uncited, "未被回答标记引用的上下文块",
        )

        rules = [r1, r2, r3, r4, r5, r6, r7]

        # 按 CitationRuleConfig 应用启停：关闭的规则不参与 all_passed 判定
        flags = {
            "R1": self._config.require_chunk_id,
            "R2": self._config.require_chunk_uid,
            "R3": self._config.require_source_location,
            "R4": self._config.require_full_text,
            "R5": self._config.check_context_completeness,
            "R6": self._config.check_marker_validity,
            "R7": self._config.enforce_all_context_cited,
        }
        for r in rules:
            r.enabled = flags.get(r.rule_id[:2], True)
            if not r.enabled:
                r.passed = True

        # 拒答场景：空 citations 是合法输出，不因 R1-R4 判空失败（空集规则自然通过）
        all_passed = is_refusal or all(
            r.passed for r in rules if r.enabled
        )

        return CitationValidationReport(
            all_passed=all_passed,
            is_refusal=is_refusal,
            context_chunks=len(context_ids),
            cited_markers=cited,
            uncited_chunks=uncited,
            unknown_markers=unknown,
            rules=rules,
        )

    # ── 规则实现 ──────────────────────────────────────────

    @staticmethod
    def _make_rule(
        rule_id: str,
        name: str,
        description: str,
        bad: List[int],
        detail_prefix: str,
    ) -> RuleResult:
        """构造"坏项列表"类规则结果（R1-R4 / R6-R7 共用）。

        规则默认启用（enabled=True），启停由 validate 按 CitationRuleConfig 统一应用。
        """
        return RuleResult(
            rule_id=rule_id,
            name=name,
            description=description,
            enabled=True,
            passed=not bad,
            detail=f"{detail_prefix}: {bad}" if bad else "",
        )

    @staticmethod
    def _check_context_fully_cited(
        citations: List[Citation],
        context_chunk_ids: List[Optional[int]],
    ) -> RuleResult:
        if not context_chunk_ids:
            # 上下文为空（拒答路径）：引用也必须为空，否则属凭空引用
            if citations:
                return RuleResult(
                    rule_id="R5_context_fully_cited",
                    name="上下文无遗漏引用",
                    description="进入 LLM 上下文的全部 chunk 必须出现在 citations 中",
                    enabled=True,
                    passed=False,
                    detail=f"上下文为空但引用非空: {[c.chunk_id for c in citations]}",
                )
            return RuleResult(
                rule_id="R5_context_fully_cited",
                name="上下文无遗漏引用",
                description="进入 LLM 上下文的全部 chunk 必须出现在 citations 中",
                enabled=True,
                passed=True,
                detail="上下文为空",
            )
        cited_ids = {c.chunk_id for c in citations}
        missing = [
            cid for cid in context_chunk_ids
            if cid is not None and cid not in cited_ids
        ]
        # 数量一致性：citations 不允许凭空多出未进入上下文的引用
        extra = [c.chunk_id for c in citations if c.chunk_id not in set(context_chunk_ids)]
        passed = not missing and not extra
        return RuleResult(
            rule_id="R5_context_fully_cited",
            name="上下文无遗漏引用",
            description="所有用于生成回答的 chunk 均可完整溯源，无遗漏、无凭空引用",
            enabled=True,
            passed=passed,
            detail=(
                f"上下文缺失引用: {missing}; 引用中凭空多出: {extra}"
                if missing or extra else ""
            ),
        )
