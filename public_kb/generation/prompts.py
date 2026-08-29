"""Prompt construction for the public knowledge QA chain."""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate


INLINE_CITATION_INSTRUCTION = (
    "回答时必须在相关结论句末标注引用来源编号，格式如【来源1】【来源2】。\n"
    "未使用的参考资料不要标注其编号。"
)

USER_TEMPLATE = """参考资料：
{context}

用户问题：{question}"""


def build_prompt(system_text: str, enable_inline_citations: bool = True) -> ChatPromptTemplate:
    """构建问答提示词模板。

    Args:
        system_text: system 提示词正文（来自 Settings.system_prompt）。
        enable_inline_citations: 是否要求 LLM 在回答中内联标注【来源N】。
    """
    system = system_text
    if enable_inline_citations:
        system += "\n\n" + INLINE_CITATION_INSTRUCTION
    return ChatPromptTemplate.from_messages([
        ("system", system),
        ("user",   USER_TEMPLATE),
    ])
