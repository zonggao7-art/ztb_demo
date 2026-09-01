"""统一意图解析 — LLM 结构化抽取 + 关键词/实体/项目编号的确定性提取与去噪。"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from public_kb.llm_factory import create_llm

from .db import _get_settings
from .models import HardFilters, SearchIntent

logger = logging.getLogger(__name__)

_INTENT_NOISE_WORDS = {
    "推荐", "查询", "查一下", "帮我", "帮忙", "一下", "情况", "数据", "信息", "相关",
    "关于", "方面", "有没有", "有无", "最近", "想了解", "了解", "请问", "看看",
    "哪里", "哪个", "怎么", "怎么样", "多少", "几个", "哪些", "什么", "需求",
    "记录", "内容", "历史", "现在", "当前", "有关", "一个", "一下子",
}

_QUERY_FILLER_PATTERN = re.compile(
    r"(帮我|帮忙|请问|查一下|查一查|查询一下|想了解|了解一下|有没有|有无|最近|关于|方面|相关)"
)

def _build_llm() -> ChatOpenAI:
    """构造询价链路使用的 LLM（temperature=0，保证解析确定性）。

    统一由 public_kb.llm_factory.create_llm 构建，避免与 graph/rag_engine 重复。
    """
    return create_llm(_get_settings(), temperature=0.0)

_UNIFIED_INTENT_SYSTEM = """你是招投标领域的智能查询意图解析专家。
请一次性完成两项任务：① 判断二级路由（sub_route）；② 提取结构化过滤条件。

输出 JSON 格式：
{{
  "sub_route": "company_query" | "bidding_query" | "all",
  "query_type": "...",
  "hard_filters": {{
    "province": "省份 或 null（bidding_query 已禁用此字段，设为 null）",
    "city": "城市 或 null（bidding_query 已禁用此字段，设为 null）",
    "company_name": "企业名称 或 null",
    "credit_code": "统一社会信用代码 或 null",
    "industry": "所属行业（如 软件信息、日用百货）或 null",
    "company_level": "企业等级（如 中型企业、大型企业）或 null",
    "business_status": "经营状态 或 null",
    "credit_rating": "信用评级 或 null",
    "purchaser": "采购人/招标单位 或 null（bidding_query 核心检索字段）",
    "successful_bidder": "中标供应商 或 null（bidding_query 核心检索字段）",
    "agent": "代理机构 或 null",
    "project_name": "项目名称 或 null（不得作为检索条件，仅用于展示）",
    "project_number": "项目编号 或 null（bidding_query 唯一精确匹配字段）",
    "project_category": "项目类别 或 null（bidding_query 已禁用此字段，设为 null）",
    "project_stage": "项目阶段 或 null（bidding_query 已禁用此字段，设为 null）",
    "time_range": {{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}} 或 null（bidding_query 已禁用此字段，设为 null）,
    "winning_amount_range": {{"min": number, "max": number}} 或 null（bidding_query 已禁用此字段，设为 null）
  }},
  "semantic_keywords": ["业务关键词"],
  "exact_tokens": ["精确的公司名、项目编号等"],
  "sort_by": "amount_desc"|"amount_asc"|"date_desc"|"date_asc"|"relevance"|null,
  "aggregation": "max_amount"|"count"|"sum"|null,
  "top_n": number|null,
  "need_penalty_check": true/false
}}

=== sub_route 判断规则 ===
- 查询涉及企业/供应商信息（行业、等级、信用、不良记录等）→ "company_query"
- 查询涉及招标/投标/中标/采购项目历史 → "bidding_query"
- 语义模糊同时涉及多类 或 无法明确归类 → "all"

=== 各 sub_route 的 query_type 枚举 ===
company_query: "supplier_recommend"|"penalty_check"|"company_detail"|"mixed"
  - 推荐/找几个/哪些公司/供应商 → "supplier_recommend"
  - 是否有不良记录/处罚/违法 → "penalty_check"，同时 need_penalty_check=true
  - 某具体公司的详细信息 → "company_detail"

bidding_query: "purchaser_query"|"bidder_query"|"project_detail"|"aggregation"|"mixed"
  - 招标过什么/采购了什么/发包历史 → "purchaser_query"（提取 purchaser 字段）
  - 某公司中标了哪些项目/中标历史/中标过什么 → "bidder_query"（提取 successful_bidder 字段）
  - P0-12放宽：只要识别到有效项目编号（含字母+数字，如 AH2024-001、ZB-2024-123、[350001]FJGGZY[GK]2024013），不论用户是否提到"中标""详情"等词，一律走 "project_detail" → 仅提取 project_number
  - 某项目的中标情况/中标详情/谁中了 → "project_detail"（仅提取 project_number，禁止使用 project_name）
  - 用户输入纯项目编号如"AH2024-001"、"ZB-2024-123" → "project_detail"，hard_filters.project_number=该编号
  - 金额最大/最高/TOP → aggregation="max_amount"，sort_by="amount_desc"
  - 提到具体年份如"2024年" → 提取到 time_range
  - 项目阶段默认"结果公告"（已中标），除非明确要查招标公告

=== 关键区分 ===
- 提问主语是公司（如"XX公司中标了什么"）→ "bidder_query"
- 提问主语是项目 或 输入中包含项目编号（如"AH2024-001的中标情况""项目编号XX的详情"甚至裸输入"AH2024-001"）→ "project_detail"
  - 仅提取 project_number，如无项目编号则设为 null
  - P0-12：纯项目编号输入如"AH2024-001"也应归类为 project_detail
  - 项目编号包含字母和数字（如 AH2024-001、ZB-2024-123），不将纯中文项目名误提取为编号

=== 重要规则 ===
- bidding_query 的 hard_filters 仅允许提取 purchaser / successful_bidder / project_number 三个字段进行检索，其他字段（province、city、project_category、project_stage、time_range、winning_amount_range）一律设为 null，不参与数据库检索
- bidding_query 的 semantic_keywords 严格仅保留企业名关键词（purchaser 或 successful_bidder 的工商全称），禁止将项目名称、标的物、代理机构名称等输入为语义关键词
- company_query 的 hard_filters 可提取 company_name、credit_code、industry、province、city 等字段
- 仅填写 sub_route 对应的专用字段，其他路由字段统一设为 null
- 行业关键词提取词元（如"软件信息"->["软件","信息"]）
- semantic_keywords 仅保留业务实体名词（公司名、地区、项目主题、品类），不保留语气词或抽象词
- 企业名称单独作为一个 semantic_keyword，不与查询意图词合并
- 口语化表达先剥离寒暄和修饰语，再提取 1~3 个核心业务关键词
- winning_amount_range 中 min/max 使用纯数字
- sort_by: amount_desc/amount_asc/date_desc/date_asc 用于 bidding
- need_penalty_check 仅当用户明确表达对应需求时设为 true
- 若用户查询中完全无法提取到有效的项目编号或合规公司名（含后缀：公司/集团/大学/学院/医院等），则将所有 hard_filters 字段设为 null，语义关键词也设为空数组 []
"""

_UNIFIED_INTENT_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _UNIFIED_INTENT_SYSTEM),
    ("user", "用户查询：{question}\n请仅输出 JSON。"),
])

def _extract_json(text: str) -> Optional[dict[str, Any]]:
    """从可能包含 markdown 代码块的文本中提取 JSON。"""
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        candidate = m.group(1).strip()
    else:
        candidate = text.strip()
    start = candidate.find("{")
    if start == -1:
        return None
    depth = 0
    end = -1
    in_str = False
    escape = False
    for i, ch in enumerate(candidate[start:], start):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if not in_str:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
    if end == -1:
        return None
    try:
        return json.loads(candidate[start:end])
    except json.JSONDecodeError:
        return None

def _dedupe_keep_order(tokens: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        if token and token not in seen:
            seen.add(token)
            result.append(token)
    return result

def _normalize_token(token: str) -> str:
    token = _QUERY_FILLER_PATTERN.sub(" ", token or "")
    token = re.sub(r"[`~!@#$%^&*()_\-+=\[\]{}|\\:;\"'<>,./?，。！？；、（）【】《》“”‘’\s]+", "", token)
    return token.strip()

_CODE_TOKEN_PATTERN = re.compile(r"\d")

def _looks_like_code(token: str) -> bool:
    """判断 token 是否具备编号/代码特征（项目编号、统一社会信用代码等）。

    P0-1：纯中文实体名（公司名、单位名）不具备数字特征，不得作为
    project_number/credit_code 等编号类列的精确匹配条件，
    否则会生成 `project_number = '公司名'` 这类恒假条件导致全链路零召回。
    """
    if not token:
        return False
    # P0-7：排除超长字符串（>50字符），项目名称含年份数字不应被误判为编号
    if len(token) > 50:
        return False
    return bool(_CODE_TOKEN_PATTERN.search(token))

_PROJECT_NUMBER_RE = re.compile(
    # 多括号格式：[350001]FJGGZY[GK]2024013 — 必须优先于单括号子模式
    r'(?:[\[\(]\d+[\]\)]\s*[A-Za-z]+\s*[\[\(][A-Za-z]+[\]\)]\s*\d+)'
    # 单括号/无括号格式：AH2024-001、[350001]FJGGZY2024013、GZ2024001
    r'|(?:[\[\(]?\s*[A-Za-z]{2,8}\s*[\]\)]?\s*[\-_]?\s*\d{2,4}\s*[\-_]?\s*\d{2,8})'
    # 年份-字母-序号格式：2024-AH-001
    r'|(?:\d{2,6}\s*[\-_]\s*[A-Za-z]{2,8}\s*[\-_]\s*\d{2,8})'
    # 字母+长数字格式：AH2024001
    r'|(?:[A-Za-z]{2,8}\s*\d{4,10})'
)

def _extract_project_number_candidate(text: str) -> Optional[str]:
    """P0-12：从自然语言文本中确定性提取项目编号候选。

    扫描原始输入文本，查找同时包含字母和数字、且格式符合
    常见招投标项目编号模式（如 AH2024-001、[350001]FJGGZY[GK]2024013）
    的子串。排除明显是公司名、金额、年份等误判。

    返回最可能的项目编号，未找到则返回 None。
    此函数不依赖 LLM，作为 LLM 意图解析的补充兜底。
    """
    if not text:
        return None

    matches = _PROJECT_NUMBER_RE.findall(text)
    if not matches:
        return None

    for candidate in matches:
        candidate = candidate.strip()
        if not candidate or len(candidate) < 3 or len(candidate) > 50:
            continue
        # 确认同时含字母和数字（排除纯数字年份等）
        if not re.search(r'[A-Za-z]', candidate) or not re.search(r'\d', candidate):
            continue
        # 排除含"万""亿""千""百"等金额单位
        if re.search(r'[万亿千百]', candidate):
            continue
        # 排除公司名后缀误判
        if any(candidate.endswith(s) for s in _COMPANY_NAME_SUFFIXES):
            continue
        # 排除过短纯数字+字母混合但无分隔符（如 "AB12" 太短不像编号）
        if len(candidate) < 6 and '-' not in candidate and '[' not in candidate:
            continue

        logger.info(
            "[PROJECT_NUMBER_EXTRACT] 确定性提取到项目编号候选: '%s' from text=%.50s",
            candidate,
            text,
        )
        return candidate

    return None

_COMPANY_NAME_SUFFIXES = (
    "有限责任公司", "股份有限公司", "有限公司", "集团公司",
    "集团", "公司", "事务所", "研究院", "大学", "学院",
    "医院", "中心", "合伙企业", "厂", "站", "处", "局",
    "委员会", "办公室", "总会", "协会", "商会", "学会",
)

_COMPANY_NAME_MIN_LEN = 4

_COMPANY_NAME_MAX_LEN = 80

def _is_valid_company_name(name: str) -> bool:
    """P0-11：工商主体名称格式校验。

    规则：
    1. 长度在 [_COMPANY_NAME_MIN_LEN, _COMPANY_NAME_MAX_LEN] 之间
    2. 必须以已知企业后缀结尾（有限公司、集团、大学等）
    3. 不含纯数字/代码特征（排除将项目编号误识别为公司名）

    不符合格式的名称将拒绝参与任何 bid_project 检索流程，
    防止 LLM 将项目名片段误提取为"企业名"后触发全链路模糊召回。
    """
    if not name or not isinstance(name, str):
        return False
    stripped = name.strip()
    if len(stripped) < _COMPANY_NAME_MIN_LEN or len(stripped) > _COMPANY_NAME_MAX_LEN:
        return False
    # 排除纯数字/代码特征（如"2024项目"不应被当作公司名）
    if _looks_like_code(stripped) and not any(
        stripped.endswith(suffix) for suffix in _COMPANY_NAME_SUFFIXES
    ):
        return False
    # 校验必须包含合法企业后缀
    return any(stripped.endswith(suffix) for suffix in _COMPANY_NAME_SUFFIXES)

def _denoise_keywords(tokens: list[str], question: str = "", max_keywords: int = 5) -> list[str]:
    cleaned: list[str] = []
    for raw in tokens:
        token = _normalize_token(str(raw))
        if len(token) < 2:
            continue
        if token in _INTENT_NOISE_WORDS:
            continue
        if token in {"推荐供应商", "查询信息", "相关信息"}:
            continue
        # P2：超长关键词截断 — LLM 偶尔把整句（含疑问句式）当作单个关键词，
        # 会污染 FULLTEXT 与语义向量，按常见机构后缀切分保留实体主体
        token = _split_overlong_keyword(token)
        cleaned.append(token)

    cleaned = _dedupe_keep_order(cleaned)
    if cleaned:
        return cleaned[:max_keywords]

    if question:
        fallback = []
        for token in _extract_keywords(question):
            norm = _normalize_token(token)
            if len(norm) >= 2 and norm not in _INTENT_NOISE_WORDS:
                fallback.append(norm)
        cleaned = _dedupe_keep_order(fallback)

    return cleaned[:max_keywords]

_OVERLONG_KEYWORD_LIMIT = 12

_ENTITY_SUFFIXES = (
    "有限责任公司", "股份有限公司", "有限公司", "集团公司", "股份公司",
    "事务所", "研究院", "大学", "学院", "医院", "集团", "公司",
)

def _split_overlong_keyword(token: str) -> str:
    """P2：超长关键词切分，保留实体主体、剥离疑问句式尾巴。

    如“武汉江腾铁路工程有限责任公司中标过什么项目”
    → 在实体后缀“有限公司”后截断，保留公司名主体。
    使用 rfind 取最后一次出现位置，避免实体名内部含后缀子串
    （如“工程有限”+“公司”）时提前截断。
    """
    if len(token) <= _OVERLONG_KEYWORD_LIMIT:
        return token
    best_end = -1
    for suffix in _ENTITY_SUFFIXES:
        idx = token.rfind(suffix)
        if idx != -1:
            best_end = max(best_end, idx + len(suffix))
    if best_end >= _OVERLONG_KEYWORD_LIMIT // 2:
        return token[:best_end]
    return token[:_OVERLONG_KEYWORD_LIMIT]

def _post_process_intent(intent: SearchIntent) -> SearchIntent:
    """P1-2：对 LLM 输出做轻量去噪和归一化。"""
    intent.semantic_keywords = _denoise_keywords(
        intent.semantic_keywords, intent.original_question, max_keywords=5
    )
    intent.exact_tokens = _dedupe_keep_order(
        [_normalize_token(token) for token in intent.exact_tokens if _normalize_token(token)]
    )[:3]
    if intent.top_n is not None:
        intent.top_n = max(1, min(int(intent.top_n), 20))
    return intent

def _parse_unified_intent(question: str, llm: ChatOpenAI) -> SearchIntent:
    """统一意图解析：一次 LLM 调用同时完成 sub_route 判断 + structured filters 抽取。

    替代旧方案中的 _classify_sub_intent() + 三个独立 Intent Prompt。
    """
    if not question:
        return SearchIntent(hard_filters=HardFilters(), original_question=question)

    start = time.perf_counter()
    try:
        chain = _UNIFIED_INTENT_PROMPT | llm | StrOutputParser()
        raw = chain.invoke({"question": question})
        logger.info("[UNIFIED_INTENT] raw_output=%s", raw[:500])
        parsed = _extract_json(raw)
        if parsed:
            intent = SearchIntent.from_dict(parsed, question)
        else:
            logger.warning("[UNIFIED_INTENT] 无法解析 LLM 输出，回退到关键词提取")
            intent = SearchIntent(
                hard_filters=HardFilters(),
                semantic_keywords=_extract_keywords(question),
                original_question=question,
            )
    except Exception as e:
        logger.warning("[UNIFIED_INTENT] LLM 调用失败 %s，回退到关键词提取", e)
        intent = SearchIntent(
            hard_filters=HardFilters(),
            semantic_keywords=_extract_keywords(question),
            original_question=question,
        )

    intent = _post_process_intent(intent)
    elapsed = time.perf_counter() - start
    logger.info(
        "[UNIFIED_INTENT] cost=%.3fs sub_route=%s query_type=%s keywords=%s",
        elapsed,
        intent.sub_route,
        intent.query_type,
        intent.semantic_keywords,
    )
    return intent

async def _parse_unified_intent_async(question: str, llm: ChatOpenAI) -> SearchIntent:
    """统一意图解析（异步版，阶段 3）。

    与 _parse_unified_intent 行为完全一致（sub_route + filters 一次抽取、
    不可解析/异常时回退关键词提取、post_process 去噪），
    仅将 chain.invoke 替换为 chain.ainvoke。
    """
    if not question:
        return SearchIntent(hard_filters=HardFilters(), original_question=question)

    start = time.perf_counter()
    try:
        chain = _UNIFIED_INTENT_PROMPT | llm | StrOutputParser()
        raw = await chain.ainvoke({"question": question})
        logger.info("[UNIFIED_INTENT] raw_output=%s", raw[:500])
        parsed = _extract_json(raw)
        if parsed:
            intent = SearchIntent.from_dict(parsed, question)
        else:
            logger.warning("[UNIFIED_INTENT] 无法解析 LLM 输出，回退到关键词提取")
            intent = SearchIntent(
                hard_filters=HardFilters(),
                semantic_keywords=_extract_keywords(question),
                original_question=question,
            )
    except Exception as e:
        logger.warning("[UNIFIED_INTENT] LLM 调用失败 %s，回退到关键词提取", e)
        intent = SearchIntent(
            hard_filters=HardFilters(),
            semantic_keywords=_extract_keywords(question),
            original_question=question,
        )

    intent = _post_process_intent(intent)
    elapsed = time.perf_counter() - start
    logger.info(
        "[UNIFIED_INTENT] cost=%.3fs sub_route=%s query_type=%s keywords=%s",
        elapsed,
        intent.sub_route,
        intent.query_type,
        intent.semantic_keywords,
    )
    return intent


def _safe_parse_intent(raw: SearchIntent) -> SearchIntent:
    """容错回填：防止 LLM 遗漏字段导致下游 NullPointer。"""
    valid_routes = {"company_query", "bidding_query", "all"}
    if not raw.sub_route or raw.sub_route not in valid_routes:
        raw.sub_route = "all"
    if not raw.query_type:
        raw.query_type = "mixed"
    if raw.hard_filters is None:
        raw.hard_filters = HardFilters()
    if raw.semantic_keywords is None:
        raw.semantic_keywords = []
    if raw.exact_tokens is None:
        raw.exact_tokens = []
    return _post_process_intent(raw)

def _extract_keywords(question: str) -> list[str]:
    """从问题中提取搜索关键词。"""
    stop_words = {"的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一",
                  "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
                  "没有", "看", "好", "自己", "这", "帮", "查", "一下", "哪些", "什么",
                  "查询"}
    words = []
    for sep in ["？", "?", "，", ",", "。", ".", "！", "!", "；", ";", " "]:
        question = question.replace(sep, "|")
    for token in question.split("|"):
        token = _normalize_token(token)
        if len(token) >= 2 and token not in stop_words:
            words.append(token)
    words = _dedupe_keep_order(words)
    return words[:5] if words else [_normalize_token(question)[:10]]
