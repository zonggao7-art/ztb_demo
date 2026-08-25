"""枚举值归一化 — 将 LLM 输出的枚举类过滤对齐到库中实际 DISTINCT 值。"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from .db import _CLEAN_DB, _get_connection, _release_connection
from .models import SearchIntent

logger = logging.getLogger(__name__)

_ENUM_CACHE: dict[tuple[str, str], list[str]] = {}

_ENUM_CACHE_LOCK = threading.Lock()

def _load_enum_values(table: str, column: str) -> list[str]:
    """懒加载并缓存指定表列的 DISTINCT 枚举值（最多 500 个）。"""
    key = (table, column)
    with _ENUM_CACHE_LOCK:
        if key in _ENUM_CACHE:
            return _ENUM_CACHE[key]

    values: list[str] = []
    conn = _get_connection(_CLEAN_DB)
    if conn is not None:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT DISTINCT `{column}` FROM `{table}` "
                    f"WHERE `{column}` IS NOT NULL AND `{column}` <> '' LIMIT 500"
                )
                values = [str(row[0]).strip() for row in cur.fetchall() if row[0]]
        except Exception as e:
            logger.debug("加载枚举值失败 %s.%s: %s", table, column, e)
        finally:
            _release_connection(conn)

    with _ENUM_CACHE_LOCK:
        _ENUM_CACHE[key] = values
    return values

def _match_enum_value(token: str, enum_values: list[str]) -> Optional[str]:
    """枚举值模糊匹配：精确相等 → token 包含于枚举值 → 枚举值包含于 token。"""
    if not token or not enum_values:
        return None
    if token in enum_values:
        return token
    for value in enum_values:
        if token in value:
            return value
    for value in enum_values:
        if value in token:
            return value
    return None

_LEVEL_SCALE_CHARS = ("大", "中", "小", "微")

def _match_company_levels(token: str, enum_values: list[str]) -> list[str]:
    """企业等级展开匹配：“中大型” → [大型企业, 中型企业]。

    对每个枚举值提取其含有的规模字（大/中/小/微），
    若这些字均出现在用户表达中则视为命中。
    """
    if not token or not enum_values:
        return []
    matched: list[str] = []
    for value in enum_values:
        chars = [c for c in _LEVEL_SCALE_CHARS if c in value]
        if chars and all(c in token for c in chars):
            matched.append(value)
    return matched

def _normalize_intent_enums(intent: SearchIntent) -> SearchIntent:
    """P0-4：将 LLM 输出的枚举类过滤对齐到库中实际枚举值。

    - industry / project_stage：对 DISTINCT 值模糊匹配，命中则替换，未命中则
      放弃该硬过滤（转为语义关键词检索），绝不下推失配值；
    - company_level：展开为实际规模值，多值时转 IN 匹配；
    - 枚举值加载失败（如数据库不可用）时保留 LLM 原值，避免误删过滤。
    """
    hf = intent.hard_filters

    if hf.industry:
        enum_values = _load_enum_values("company_info", "industry")
        if enum_values:
            matched = _match_enum_value(hf.industry, enum_values)
            if matched and matched != hf.industry:
                logger.info("[ENUM_NORM] industry '%s' → '%s'", hf.industry, matched)
            elif not matched:
                logger.info(
                    "[ENUM_NORM] industry '%s' 无匹配枚举值，降级为语义检索", hf.industry
                )
            hf.industry = matched

    if hf.company_level:
        enum_values = _load_enum_values("company_info", "company_level")
        if enum_values:
            levels = _match_company_levels(hf.company_level, enum_values)
            if levels:
                if hf.company_level not in levels:
                    logger.info(
                        "[ENUM_NORM] company_level '%s' → %s", hf.company_level, levels
                    )
                if len(levels) == 1:
                    hf.company_level = levels[0]
                    hf.company_level_values = None
                else:
                    hf.company_level_values = levels
                    hf.company_level = None
            else:
                logger.info(
                    "[ENUM_NORM] company_level '%s' 无匹配枚举值，降级为语义检索",
                    hf.company_level,
                )
                hf.company_level = None

    if hf.project_stage:
        enum_values = _load_enum_values("bid_project", "project_stage")
        if enum_values:
            matched = _match_enum_value(hf.project_stage, enum_values)
            if matched and matched != hf.project_stage:
                logger.info(
                    "[ENUM_NORM] project_stage '%s' → '%s'", hf.project_stage, matched
                )
            elif not matched:
                logger.info(
                    "[ENUM_NORM] project_stage '%s' 无匹配枚举值，移除该过滤",
                    hf.project_stage,
                )
            hf.project_stage = matched

    return intent
