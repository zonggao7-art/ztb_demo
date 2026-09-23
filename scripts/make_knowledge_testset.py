"""从 DATA/repaired_knowledge 提取真实语料素材，生成 testset_knowledge.jsonl。

题目全部锚定语料真实内容（法条条号、问答题标题、小节标题），保证可答；
拒答负样本（expect_refusal=true）为语料外通用问题，用于验证拒答行为。

用法：
    python scripts/make_knowledge_testset.py              # 生成 testset_knowledge.jsonl
    python scripts/make_knowledge_testset.py --start 11   # 换等距抽样起始偏移
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASE = PROJECT_ROOT / "DATA" / "repaired_knowledge"
OUT = PROJECT_ROOT / "testset_knowledge.jsonl"

ART_RE = re.compile(r"^(第[一二三四五六七八九十百千零〇0-9]+条)(【[^】]+】)?")
Q_RE = re.compile(r"^\s*\d{1,4}[.．、]\s*(.+)")


def law_name(chapter: str) -> str:
    parts = [p.strip() for p in chapter.split(" > ")]
    for p in reversed(parts):
        if "中华人民共和国" in p:
            return p
    for p in reversed(parts):
        compact = re.sub(r"\s+", "", p)
        if compact.endswith("法") or compact.endswith("条例") \
                or compact.endswith("办法") or compact.endswith("规定") \
                or compact.endswith("规则"):
            return p
    return ""


def load_book(key: str) -> list[dict]:
    path = BASE / key / "documents.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def body_lines(text: str) -> list[str]:
    """chunk 正文行（剥掉首行【面包屑】前缀）。"""
    return [ln.strip() for ln in text.split("\n") if ln.strip() and not ln.strip().startswith("【")]


def pick(items: list, count: int, start: int = 7) -> list:
    if len(items) <= count:
        return list(items)
    step = max(1, len(items) // count)
    return [items[(start + i * step) % len(items)] for i in range(count)]


def collect_articles() -> list[dict]:
    articles: list[dict] = []
    seen: set[str] = set()
    for row in load_book("book3"):
        if row["metadata"].get("content_type") != "legal_article":
            continue
        matched = None
        for line in body_lines(row["text"])[:2]:
            matched = ART_RE.match(line)
            if matched:
                break
        if not matched:
            continue
        law = law_name(row["metadata"]["chapter"])
        if not law:
            continue
        key = law + matched.group(1)
        if key in seen:
            continue
        seen.add(key)
        articles.append({
            "law": law,
            "num": matched.group(1),
            "purpose": (matched.group(2) or "").strip("【】"),
        })
    return articles


def collect_qa_titles() -> list[str]:
    titles: list[str] = []
    seen: set[str] = set()
    for row in load_book("book2"):
        if row["metadata"].get("content_type") != "qa_pair":
            continue
        matched = None
        for line in body_lines(row["text"])[:2]:
            matched = Q_RE.match(line)
            if matched:
                break
        if not matched:
            continue
        q = matched.group(1).strip()
        if len(q) < 8:
            continue
        fingerprint = q[:30]
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        titles.append(q)
    return titles


def collect_sections() -> list[str]:
    titles: list[str] = []
    seen: set[str] = set()
    for key in ("book1", "book3"):
        for row in load_book(key):
            chapter = row["metadata"].get("chapter", "")
            parts = [p.strip() for p in chapter.split(" > ") if p.strip()]
            if len(parts) < 2:
                continue
            title = parts[-1]
            if not re.match(r"^[一二三四五六七八九十]{1,3}[、.]", title):
                continue
            if len(title) > 30 or "？" in title or title in seen:
                continue
            seen.add(title)
            titles.append(title)
    return titles


REFUSAL_QUESTIONS = (
    "今天的股市大盘走势如何？",
    "红烧肉怎么做才好吃？",
    "长江的源头在哪里？",
    "Python 的 GIL 是什么？",
    "感冒了应该吃什么药？",
    "推荐几部好看的科幻电影。",
    "三亚五月份适合潜水吗？",
    "猫掉毛严重怎么办？",
    "圆周率小数点后一百位是多少？",
    "怎么挑选一台性价比高的笔记本电脑？",
)

CROSS_BOOK_QUESTIONS = (
    ("不同法律法规对评标委员会人数组成是如何规定的？", "较难"),
    ("投标保证金在哪些情形下不予退还？", "中等"),
    ("招标方式有哪几种？分别适用于什么情形？", "中等"),
    ("开标现场出现异常情况应当如何处理？", "中等"),
    ("中标通知书发出后，招标人与中标人应当在多少日内订立书面合同？", "中等"),
    ("联合体投标时，联合体各方对招标人承担什么责任？", "中等"),
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=7)
    args = parser.parse_args()

    cases: list[dict] = []

    def add(question: str, category: str, difficulty: str,
            expect_refusal: bool = False) -> None:
        cases.append({
            "sample_id": f"kb-{len(cases) + 1:04d}",
            "question": question,
            "category": category,
            "difficulty": difficulty,
            "expect_refusal": expect_refusal,
        })

    articles = collect_articles()
    with_purpose = [a for a in articles if a["purpose"]]
    plain = [a for a in articles if not a["purpose"]]
    for a in pick(with_purpose, 8, args.start):
        add(
            f"《{a['law']}》{a['num']}（{a['purpose']}）主要规定了什么内容？",
            "条款号直查", "简单",
        )
    for a in pick(plain, 20, args.start):
        add(f"《{a['law']}》{a['num']}规定了什么内容？", "条款号直查", "中等")

    qa_titles = collect_qa_titles()
    for i, q in enumerate(pick(qa_titles, 30, args.start + 3)):
        if i % 2 == 0:
            add(q, "问答原题", "简单")
        else:
            add(f"实务中，{q}", "语义改写", "中等")

    sections = collect_sections()
    for title in pick(sections, 30, args.start):
        add(f"关于「{title}」，资料中是如何说明的？", "概念阐释", "中等")

    for question, difficulty in CROSS_BOOK_QUESTIONS:
        add(question, "跨书综合", difficulty)

    for question in REFUSAL_QUESTIONS:
        add(question, "拒答负样本", "简单", expect_refusal=True)

    duplicates = [
        q for q, n in Counter(c["question"] for c in cases).items() if n > 1
    ]
    if duplicates:
        raise RuntimeError(f"生成结果存在重复问题: {duplicates}")

    with OUT.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")

    breakdown = Counter(c["category"] for c in cases)
    print(f"生成 {len(cases)} 条 → {OUT}")
    print("分类:", dict(breakdown))


if __name__ == "__main__":
    main()
