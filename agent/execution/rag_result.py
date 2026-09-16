"""完整 RAG 结果的接入检查：检查结构与引用，不宣称核实了法律结论。"""
from pydantic import ValidationError

from public_kb.citations import Citation, CitationValidator, parse_citation_markers
from public_kb.config import CitationRuleConfig
from .context import RunStopped


def validate_rag_result(data):
    answer = data.get("answer")
    raw_citations = data.get("citations")
    if not isinstance(answer, str) or not answer.strip() or not isinstance(raw_citations, list):
        raise RunStopped("invalid_rag_result")
    if type(data.get("is_refusal", False)) is not bool:
        raise RunStopped("invalid_rag_result")
    is_refusal = data.get("is_refusal", False)
    if is_refusal:
        # 无检索结果时只能展示 RAG 的固定拒答，不允许无证据的自由回答。
        if raw_citations or answer != "抱歉，公共知识库中暂无相关内容，无法提供可靠回答。":
            raise RunStopped("invalid_rag_result")
    elif not raw_citations or not parse_citation_markers(answer):
        raise RunStopped("citation_invalid")
    try:
        citations = [Citation.model_validate(c) for c in raw_citations]
    except (ValidationError, TypeError) as exc:
        raise RunStopped("citation_invalid") from exc
    if [c.context_index for c in citations] != list(range(1, len(citations) + 1)):
        raise RunStopped("citation_invalid")
    # 保留完整来源，但不要求正文引用每个检索片段，与原 RAG 的默认规则一致。
    report = CitationValidator(CitationRuleConfig()).validate(
        citations, answer, [c.chunk_id for c in citations], is_refusal=is_refusal)
    if not report.all_passed:
        raise RunStopped("citation_invalid")
    return answer, citations, is_refusal
