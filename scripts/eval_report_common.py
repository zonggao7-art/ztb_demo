"""
测评报告共用工具 — 归一化、召回判定与统计辅助。

被 generate_three_core_report.py 等报告脚本共用（原 generate_report.py
与 generate_three_core_report.py 中重复实现的收敛点）。
"""

from __future__ import annotations

import re

_WS = re.compile(r"\s+")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def norm(s: str) -> str:
    """Canonicalize a string for substring matching (strip whitespace/commas/currency)."""
    s = str(s)
    s = s.replace("，", "").replace(",", "").replace("￥", "").replace("¥", "")
    return _WS.sub("", s)


def try_float(s: str):
    t = str(s).strip().replace(",", "").replace("，", "").replace("￥", "").replace("¥", "")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def numeric_close(a: float, b: float) -> bool:
    if a == b:
        return True
    tol = 1e-6 * max(1.0, abs(a), abs(b))
    return abs(a - b) <= tol


def value_recalled(v, corpus_norm: str, corpus_numbers: list[float]) -> bool:
    if v is None:
        return True
    s = str(v).strip()
    if s == "":
        return True

    n = norm(s)
    # 1) exact text substring (handles names/ids/dates and verbatim amounts)
    if n and n in corpus_norm:
        return True

    # 2) numeric equivalence (handles comma/decimal formatting), skip huge IDs to avoid float collision
    f = try_float(s)
    if f is not None and abs(f) < 1e15:
        if any(numeric_close(f, x) for x in corpus_numbers):
            return True

    # 3) long-text truncation tolerance (system truncates to 500 chars + ellipsis)
    if len(n) > 200 and n[:300] in corpus_norm:
        return True

    return False


def build_corpus(answer: str, records) -> tuple[str, list[float]]:
    parts = [answer or ""]
    if isinstance(records, list):
        for rec in records:
            if isinstance(rec, dict):
                parts.extend(str(v) for v in rec.values())
    corpus_norm = norm(" ".join(parts))
    numbers = [float(m.group()) for m in _NUM_RE.finditer(corpus_norm)]
    return corpus_norm, numbers


def resolve_gt_values(case: dict) -> list[tuple[str, object]]:
    """Return [(field, value), ...] for every required field across all ground-truth records."""
    fields = case.get("expected_fields", [])
    out = []
    for rec in case.get("ground_truth", []):
        for field in fields:
            if field in rec:
                out.append((field, rec[field]))
            else:
                # expression field stored under a SQL alias (e.g. "budget_amount - winning_amount" -> amount_difference)
                alias = [k for k in rec if k not in fields]
                out.append((field, rec[alias[0]] if len(alias) == 1 else None))
    return out


def pct(ok, tot):
    return round(100.0 * ok / tot, 3) if tot else 0.0


def _percentile(data, p):
    s = sorted(data)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = f + 1
    if c >= len(s):
        return s[-1]
    return s[f] + (s[c] - s[f]) * (k - f)
