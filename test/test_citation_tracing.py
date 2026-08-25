"""引用溯源改造回归测试 — 覆盖 chunk 唯一标识、标准化 citations、校验规则 R1-R7。

纯函数级测试（不依赖 MySQL/Milvus 运行），可直接执行：
    python -m test.test_citation_tracing
或 pytest：
    pytest test/test_citation_tracing.py -v
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.language_models.fake_chat_models import (  # noqa: E402
    FakeListChatModel,
)
from langchain_core.messages import HumanMessage  # noqa: E402
from langchain_core.documents import Document  # noqa: E402

from public_kb.chunk_ids import (  # noqa: E402
    compute_chunk_uid,
    compute_text_hash,
    normalize_chunk_text,
)
from public_kb.citations import (  # noqa: E402
    Citation,
    CitationValidator,
    build_citations,
    format_citations,
    parse_citation_markers,
)
from public_kb.config import CitationRuleConfig, Settings  # noqa: E402
from public_kb.qa_chain import (  # noqa: E402
    _dense_only_retrieve,
    _entity_to_doc,
    _hybrid_search_with_full_fields,
    _search_with_full_fields,
    build_qa_chain,
)


def _doc(text="第三十七条 评标由招标人依法组建的评标委员会负责。",
         chunk_id=101, **meta) -> Document:
    metadata = {
        "doc_name": "中华人民共和国招标投标法",
        "chapter": "第四章 开标、评标和中标",
        "chunk_index": 0,
        "chunk_id": chunk_id,
    }
    metadata.update(meta)
    return Document(page_content=text, metadata=metadata)


def _citations(n=2, overrides=None) -> list:
    """构造 n 条合法 Citation（含 chunk_id/chunk_uid/数据源位置/完整原文）。"""
    overrides = overrides or {}
    out = []
    for i in range(1, n + 1):
        meta = {
            "doc_name": "中华人民共和国招标投标法",
            "chapter": "第四章 开标、评标和中标",
            "chunk_index": 0,
        }
        text = f"第{i}条 测试条文内容。"
        out.append(Citation(
            context_index=i,
            chunk_id=1000 + i,
            chunk_uid=compute_chunk_uid(text, meta),
            doc_name=meta["doc_name"],
            chapter=meta["chapter"],
            chunk_index=0,
            text=text,
            score=0.9 - i * 0.1,
            metadata={"source_file": "laws.csv"},
        ))
    for i, c in enumerate(out, 1):
        for k, v in overrides.get(i, {}).items():
            setattr(c, k, v)
    return out


# ────────────────────────────────────────────────
# chunk_uid：确定性 / 稳定性 / 规范化一致性
# ────────────────────────────────────────────────
def test_chunk_uid_deterministic():
    meta = {"doc_name": "法A", "chapter": "第一章", "chunk_index": 2}
    a = compute_chunk_uid("同一条文内容", meta)
    b = compute_chunk_uid("同一条文内容", meta)
    assert a == b
    assert a.startswith("ck-") and len(a) == len("ck-") + 32


def test_chunk_uid_stable_across_normalization():
    meta = {"doc_name": "法A", "chapter": "第一章", "chunk_index": 0}
    raw = " 第一条 内容。\r\n第二行  \r\n"
    uid = compute_chunk_uid(raw, meta)
    # 换行与首尾空白差异不影响 uid（哈希基于规范化文本）
    assert compute_chunk_uid("\n第一条 内容。\n第二行", meta) == uid


def test_chunk_uid_differs_by_source_or_content():
    meta_a = {"doc_name": "法A", "chapter": "第一章", "chunk_index": 0}
    meta_b = {"doc_name": "法B", "chapter": "第一章", "chunk_index": 0}
    text = "第一条 内容。"
    # 同内容不同文档 → 不同 uid（保留行级可区分）
    assert compute_chunk_uid(text, meta_a) != compute_chunk_uid(text, meta_b)
    # 同文档不同内容 → 不同 uid
    assert compute_chunk_uid(text, meta_a) != compute_chunk_uid("第二条 其他内容。", meta_a)


def test_text_hash_normalization():
    assert normalize_chunk_text("  a\r\nb\r  ") == "a\nb"
    assert compute_text_hash("abc") == compute_text_hash(" abc ")


# ────────────────────────────────────────────────
# build_citations：标准化结构完整性
# ────────────────────────────────────────────────
def test_build_citations_structure():
    doc = _doc(
        text="第三十七条 " + "评标委员会。" * 50,  # 长文本验证不截断
        chunk_uid="ck-abc",
        source_file="laws.csv",
        publish_date="2019-12-28",
    )
    citations = build_citations([(doc, 0.87654)])
    assert len(citations) == 1
    c = citations[0]
    assert c.context_index == 1
    assert c.chunk_id == 101
    assert c.chunk_uid == "ck-abc"
    assert c.doc_name == "中华人民共和国招标投标法"
    assert c.chapter == "第四章 开标、评标和中标"
    assert c.text == doc.page_content          # 完整原文，未被截断
    assert c.score == 0.8765
    assert c.metadata["source_file"] == "laws.csv"
    assert c.metadata["publish_date"] == "2019-12-28"
    # 顶层字段与内部字段不得重复进入 metadata
    assert "chunk_id" not in c.metadata
    assert "doc_name" not in c.metadata


def test_build_citations_computes_missing_uid():
    doc = _doc()  # 元数据无 chunk_uid
    c = build_citations([(doc, 0.5)])[0]
    assert c.chunk_uid == compute_chunk_uid(doc.page_content, doc.metadata)


def test_build_citations_order_matches_context():
    docs = [(_doc(f"第{i}条"), 0.9 - i * 0.1) for i in range(1, 4)]
    citations = build_citations(docs)
    assert [c.context_index for c in citations] == [1, 2, 3]
    assert [c.text for c in citations] == ["第1条", "第2条", "第3条"]


# ────────────────────────────────────────────────
# 标记解析
# ────────────────────────────────────────────────
def test_parse_citation_markers():
    assert parse_citation_markers("评标委员会五人以上单数【来源1】【来源2】") == [1, 2]
    assert parse_citation_markers("【来源 3】带空白标记") == [3]
    assert parse_citation_markers("重复【来源1】与【来源1】") == [1]
    assert parse_citation_markers("无标记回答") == []
    # 非【】括注不解析
    assert parse_citation_markers("[来源1]英文括号") == []


# ────────────────────────────────────────────────
# 校验规则 R1-R7
# ────────────────────────────────────────────────
def _validate(citations, answer, context_ids=None, is_refusal=False, **cfg):
    validator = CitationValidator(CitationRuleConfig(**cfg))
    return validator.validate(
        citations, answer, context_ids, is_refusal=is_refusal,
    )


def test_validator_all_pass():
    citations = _citations(2)
    answer = "评标委员会五人以上单数【来源1】。专家不少于三分之二【来源2】。"
    report = _validate(citations, answer, context_ids=[1001, 1002])
    assert report.all_passed is True
    assert report.cited_markers == [1, 2]
    assert report.uncited_chunks == []
    assert report.unknown_markers == []
    assert all(r.passed for r in report.rules if r.enabled)


def test_validator_r1_missing_chunk_id():
    citations = _citations(1, overrides={1: {"chunk_id": None}})
    report = _validate(citations, "答【来源1】", context_ids=[None])
    assert report.all_passed is False
    r1 = next(r for r in report.rules if r.rule_id == "R1_chunk_id_present")
    assert r1.passed is False and "1" in r1.detail


def test_validator_r2_missing_uid():
    citations = _citations(1, overrides={1: {"chunk_uid": ""}})
    report = _validate(citations, "答【来源1】", context_ids=[1001])
    assert report.all_passed is False
    assert not any(r.passed for r in report.rules if r.rule_id == "R2_chunk_uid_present")


def test_validator_r3_unknown_doc_name():
    citations = _citations(1, overrides={1: {"doc_name": "未知文档"}})
    report = _validate(citations, "答【来源1】", context_ids=[1001])
    assert report.all_passed is False
    assert not any(r.passed for r in report.rules if r.rule_id == "R3_source_location_present")


def test_validator_r4_empty_text():
    citations = _citations(1, overrides={1: {"text": "  "}})
    report = _validate(citations, "答【来源1】", context_ids=[1001])
    assert report.all_passed is False
    assert not any(r.passed for r in report.rules if r.rule_id == "R4_full_text_present")


def test_validator_r5_context_omission():
    """上下文 chunk 未进入 citations → 无遗漏规则失败。"""
    citations = _citations(1)
    report = _validate(citations, "答【来源1】", context_ids=[1001, 2002])
    assert report.all_passed is False
    r5 = next(r for r in report.rules if r.rule_id == "R5_context_fully_cited")
    assert r5.passed is False and "2002" in r5.detail


def test_validator_r5_extra_citation():
    """citations 凭空多出未进入上下文的 chunk → 无遗漏规则失败。"""
    citations = _citations(2)
    report = _validate(citations, "答【来源1】", context_ids=[1001])
    assert report.all_passed is False
    assert not any(r.passed for r in report.rules if r.rule_id == "R5_context_fully_cited")


def test_validator_r5_empty_context_with_citations():
    """上下文为空但引用非空 → 凭空引用，R5 失败。"""
    citations = _citations(1)
    report = _validate(citations, "答【来源1】", context_ids=[])
    assert report.all_passed is False
    assert not any(r.passed for r in report.rules if r.rule_id == "R5_context_fully_cited")


def test_validator_r6_unknown_marker():
    citations = _citations(2)
    report = _validate(citations, "答【来源9】", context_ids=[1001, 1002])
    assert report.all_passed is False
    assert report.unknown_markers == [9]
    assert not any(r.passed for r in report.rules if r.rule_id == "R6_no_unknown_markers")


def test_validator_refusal_passes():
    report = _validate(
        [], "抱歉，公共知识库中暂无相关内容，无法提供可靠回答。",
        [], is_refusal=True,
    )
    assert report.all_passed is True
    assert report.is_refusal is True


def test_validator_uncited_chunks_recorded_default_soft():
    """默认配置（R7 关闭）：未标记引用仅记录，不影响 all_passed。"""
    citations = _citations(3)
    report = _validate(citations, "只引用了第一个【来源1】", context_ids=[1001, 1002, 1003])
    assert report.all_passed is True
    assert report.uncited_chunks == [2, 3]
    r7 = next(r for r in report.rules if r.rule_id == "R7_all_context_marked")
    assert r7.enabled is False


def test_validator_r7_strict_mode():
    citations = _citations(3)
    report = _validate(
        citations, "只引用了第一个【来源1】", context_ids=[1001, 1002, 1003],
        enforce_all_context_cited=True,
    )
    assert report.all_passed is False
    r7 = next(r for r in report.rules if r.rule_id == "R7_all_context_marked")
    assert r7.enabled is True and r7.passed is False


def test_validator_disabled_rule_not_enforced():
    citations = _citations(1, overrides={1: {"chunk_id": None}})
    # R1 关闭后，chunk_id 缺失不再导致 all_passed 失败
    # （上下文 id 同样为 None，R5 也不会误报）
    report = _validate(
        citations, "答【来源1】", context_ids=[None], require_chunk_id=False,
    )
    r1 = next(r for r in report.rules if r.rule_id == "R1_chunk_id_present")
    assert r1.enabled is False
    assert report.all_passed is True


# ────────────────────────────────────────────────
# 检索路径：chunk_id / chunk_uid / 动态元数据透传
# ────────────────────────────────────────────────
def test_entity_to_doc_propagates_metadata():
    """pymilvus 3.x 嵌套 Hit.entity 结构（生产形态）→ 完整溯源元数据。"""
    inner = {
        "id": 12345,
        "text": "第一条 条文内容。",
        "vector": [0.1] * 8,  # 向量不得进入 metadata
        "doc_name": "中华人民共和国招标投标法",
        "chapter": "第一章 总则",
        "chunk_index": 0,
        "source_file": "laws.csv",
        "publish_date": "2019-12-28",
    }
    entity = {"id": 12345, "distance": 0.88, "entity": inner}
    doc = _entity_to_doc(entity, 0.88)
    assert doc.metadata["chunk_id"] == 12345
    assert doc.metadata["chunk_uid"] == compute_chunk_uid(inner["text"], inner)
    assert doc.metadata["doc_name"] == "中华人民共和国招标投标法"
    assert doc.metadata["chapter"] == "第一章 总则"
    assert doc.metadata["source_file"] == "laws.csv"
    assert "vector" not in doc.metadata and "text" not in doc.metadata
    assert "id" not in doc.metadata and "distance" not in doc.metadata


def test_entity_to_doc_flat_entity():
    """平铺 dict 实体（如 get() 返回）同样可处理。"""
    entity = {
        "id": 999,
        "text": "第三条 内容。",
        "doc_name": "某法规",
        "chapter": "第二章",
        "chunk_index": 1,
    }
    doc = _entity_to_doc(entity, 0.7)
    assert doc.page_content == "第三条 内容。"
    assert doc.metadata["chunk_id"] == 999
    assert doc.metadata["doc_name"] == "某法规"


class _MockCollection:
    """模拟 pymilvus MilvusClient 的检索接口。"""

    def __init__(self, hits, has_sparse=False, fail_star_once=False):
        self._hits = hits
        self._has_sparse = has_sparse
        self._fail_star_once = fail_star_once
        self.calls = []

    def describe_collection(self, name):
        fields = [{"name": "id"}, {"name": "text"}, {"name": "vector"}]
        if self._has_sparse:
            fields.append({"name": "sparse_vector"})
        return {"fields": fields}

    def search(self, name, data=None, anns_field=None, search_params=None,
               limit=None, output_fields=None):
        self.calls.append({"method": "search", "output_fields": output_fields})
        if self._fail_star_once and output_fields == ["*"]:
            self._fail_star_once = False
            raise RuntimeError("server does not support wildcard")
        return [self._hits[0][:limit]]

    def hybrid_search(self, name, reqs=None, ranker=None, limit=None,
                      output_fields=None):
        self.calls.append({"method": "hybrid_search", "output_fields": output_fields})
        if self._fail_star_once and output_fields == ["*"]:
            self._fail_star_once = False
            raise RuntimeError("server does not support wildcard")
        return [self._hits[0][:limit]]


class _MockEmbeddings:
    def embed_query(self, text):
        return [0.1] * 1024

    def embed_documents(self, texts):
        return [[0.1] * 1024] * len(texts)


def _hit(chunk_id=7001, text="第三十七条 评标由招标人依法组建的评标委员会负责。", score=0.9):
    """构造 pymilvus 3.x 生产形态的嵌套 Hit。"""
    inner = {
        "id": chunk_id,
        "text": text,
        "doc_name": "中华人民共和国招标投标法",
        "chapter": "第四章 开标、评标和中标",
        "chunk_index": 0,
        "source_file": "laws.csv",
    }
    entity = {"id": chunk_id, "distance": score, "entity": inner}
    return SimpleNamespace(score=score, entity=entity)


def test_search_with_full_fields_fallback():
    collection = _MockCollection([[_hit()]], fail_star_once=True)
    hits = _search_with_full_fields(
        collection, Settings(),
        data=[[0.1] * 1024], anns_field="vector",
        search_params={"metric_type": "COSINE", "params": {"nprobe": 32}},
        limit=30,
    )
    assert len(hits) == 1
    assert collection.calls[0]["output_fields"] == ["*"]
    assert collection.calls[1]["output_fields"] != ["*"]  # 回退基础字段


def test_hybrid_search_with_full_fields_fallback():
    collection = _MockCollection([[_hit()]], fail_star_once=True)
    hits = _hybrid_search_with_full_fields(
        collection, Settings(), reqs=[], ranker=None, limit=30,
    )
    assert len(hits) == 1
    assert collection.calls[0]["output_fields"] == ["*"]
    assert collection.calls[1]["output_fields"] != ["*"]


def test_dense_only_retrieve_attaches_chunk_id():
    collection = _MockCollection([[_hit()]])
    settings = Settings()
    results = _dense_only_retrieve(
        "评标委员会怎么组成？", None, settings, collection, _MockEmbeddings(),
    )
    assert len(results) == 1
    doc, score = results[0]
    assert doc.metadata["chunk_id"] == 7001
    assert doc.metadata["chunk_uid"] == compute_chunk_uid(
        doc.page_content, doc.metadata)
    assert doc.metadata["source_file"] == "laws.csv"


def test_full_chain_returns_citations_and_validation():
    """端到端：LCEL 链 invoke → 回答 + 标准化 citations + 校验报告。"""
    settings = Settings()
    hit = _hit()
    collection = _MockCollection([[hit]])
    llm = FakeListChatModel(responses=[
        "评标委员会由招标人代表和有关技术、经济等方面的专家组成，"
        "成员人数为五人以上单数【来源1】。"
    ])
    chain = build_qa_chain(
        vector_store=None, llm=llm, settings=settings,
        collection=collection, embeddings=_MockEmbeddings(),
    )
    result = chain.invoke("评标委员会由哪些人组成？")

    assert "评标委员会" in result["answer"]
    assert len(result["sources"]) == 1            # legacy 视图保留
    citations = result["citations"]
    assert len(citations) == 1
    c = citations[0]
    assert c["chunk_id"] == 7001
    assert c["chunk_uid"].startswith("ck-")
    assert c["doc_name"] == "中华人民共和国招标投标法"
    assert c["text"] == hit.entity["entity"]["text"]  # 完整原文
    validation = result["citation_validation"]
    assert validation["all_passed"] is True
    assert validation["cited_markers"] == [1]


def test_full_chain_refusal_report():
    """拒答路径：citations 为空 + is_refusal 校验报告。"""
    settings = Settings()
    collection = _MockCollection([[]])  # 无命中
    llm = FakeListChatModel(responses=["不应被调用"])
    chain = build_qa_chain(
        vector_store=None, llm=llm, settings=settings,
        collection=collection, embeddings=_MockEmbeddings(),
    )
    result = chain.invoke("知识库外的问题？")
    assert result["citations"] == []
    validation = result["citation_validation"]
    assert validation["is_refusal"] is True
    assert validation["all_passed"] is True


# ────────────────────────────────────────────────
# format_citations：呈现层渲染
# ────────────────────────────────────────────────
def _citation_dict(idx=1, chunk_id=1001, text="第一条 条文内容。", **meta):
    return {
        "context_index": idx,
        "chunk_id": chunk_id,
        "chunk_uid": f"ck-{idx}",
        "doc_name": "中华人民共和国招标投标法",
        "chapter": "第四章 开标、评标和中标",
        "chunk_index": 2,
        "text": text,
        "score": 0.9,
        "metadata": meta,
    }


def test_format_citations_empty():
    assert format_citations([]) == ""
    assert format_citations(None) == ""


def test_format_citations_single():
    block = format_citations([_citation_dict(
        chunk_index=0, page_number=465, source_file="laws.csv",
        title="中华人民共和国招标投标法", publish_date="2019-12-28",
    )])
    assert "共 1 条" in block
    assert "【来源1】中华人民共和国招标投标法" in block
    assert "第四章 开标、评标和中标" in block
    assert "页码: 465" in block
    assert "数据源文件: laws.csv" in block
    assert "发布日期: 2019-12-28" in block
    assert "chunk_id: 1001" in block and "chunk_uid: ck-1" in block
    assert "第一条 条文内容。" in block          # 原文片段完整输出


def test_format_citations_multi():
    block = format_citations([
        _citation_dict(1, text="甲"),
        _citation_dict(2, chunk_id=1002, text="乙"),
        _citation_dict(3, chunk_id=1003, text="丙"),
    ])
    assert "共 3 条" in block
    assert block.index("【来源1】") < block.index("【来源2】") < block.index("【来源3】")
    assert "甲" in block and "乙" in block and "丙" in block


def test_format_citations_no_metadata():
    """无附加元数据时仍输出【来源N】、数据源位置与原文。"""
    block = format_citations([_citation_dict(1, text="单条内容。")])
    assert "【来源1】" in block
    assert "中华人民共和国招标投标法" in block
    assert "原文: 单条内容。" in block
    assert "页码" not in block  # 无 page_number 不输出该行


def test_format_citations_text_cap():
    block = format_citations(
        [_citation_dict(1, text="长文本" * 100)], max_text_chars=20,
    )
    assert "…" in block
    assert len(block.split("原文: ", 1)[1]) <= 21


# ────────────────────────────────────────────────
# knowledge_qa 节点：citations 透传 business_result
# ────────────────────────────────────────────────
def test_node_knowledge_qa_passes_citations():
    from agent.nodes import knowledge_qa as kqa

    citations = [{
        "context_index": 1, "chunk_id": 7001, "chunk_uid": "ck-x",
        "doc_name": "法A", "chapter": "第一章", "chunk_index": 0,
        "text": "第一条", "score": 0.9, "metadata": {},
    }]
    validation = {"all_passed": True, "is_refusal": False}

    class _FakeRAG:
        def query(self, question):
            return {
                "answer": "答案【来源1】",
                "sources": [{"doc": "法A"}],
                "citations": citations,
                "citation_validation": validation,
            }

    saved = kqa._rag_engine
    kqa._rag_engine = _FakeRAG()
    try:
        result = kqa.node_knowledge_qa(
            {"messages": [HumanMessage(content="问题？")]}
        )
    finally:
        kqa._rag_engine = saved

    data = result["business_result"]["data"]
    assert data["sources"] == [{"doc": "法A"}]
    assert data["citations"] == citations
    assert data["citation_validation"] == validation


if __name__ == "__main__":
    test_funcs = [
        (name, fn) for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failed = 0
    for name, fn in test_funcs:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n共 {len(test_funcs)} 项，失败 {failed} 项")
    sys.exit(1 if failed else 0)
