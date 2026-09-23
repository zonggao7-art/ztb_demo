# -*- coding: utf-8 -*-
"""核验 testset_200.jsonl 的 ground_truth_chunk_ids 与 DATA/repaired_knowledge chunk 原文的一致性。

检查层级：
  1. uid 真实性：ground_truth_chunk_ids 是否存在于三本书的 documents.jsonl 及 manifest uid 清单
  2. 文本一致性：ground_truth_text[i] 是否与该 uid 的 chunk 原文一致（精确/规范化）
  3. 答案溯源：reference 有多少比例可由该题所引 chunk 原文覆盖（difflib 匹配块 + 最长逐字重合）
  4. 问题关联：question 与该 chunk 原文的字符二元组重合度（问题为改写，仅作参考信号）
"""
import json
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "second_round_eval_data"
TESTSET = SRC_DIR / "testset_200.jsonl"
KB_DIR = ROOT / "DATA" / "repaired_knowledge"

WS_RE = re.compile(r"\s+")


def normalize(s: str) -> str:
    return WS_RE.sub("", s or "")


def load_chunks():
    chunks = {}
    per_book = {}
    for book in ["book1", "book2", "book3"]:
        uids = []
        with open(KB_DIR / book / "documents.jsonl", encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                uid = rec["metadata"]["chunk_uid"]
                chunks[uid] = {"book": book, "text": rec["text"], "meta": rec["metadata"]}
                uids.append(uid)
        per_book[book] = uids
    return chunks, per_book


def check_manifest(chunks, per_book):
    manifest = json.loads((KB_DIR / "manifest.json").read_text(encoding="utf-8"))
    import hashlib
    issues = []
    for book, uids in per_book.items():
        m = manifest["books"][book]
        if sorted(uids) != sorted(m["chunk_uids"]):
            issues.append(f"{book}: documents.jsonl 与 manifest uid 清单不一致")
        # 与 public_kb/book_pipeline.py 一致：按 documents.jsonl 文件顺序 join
        digest = hashlib.sha256("\n".join(uids).encode("utf-8")).hexdigest()
        if digest != m["chunk_uid_digest"]:
            issues.append(f"{book}: uid 摘要不匹配（算得 {digest}，manifest {m['chunk_uid_digest']}）")
    total = sum(len(v) for v in per_book.values())
    declared = sum(m["chunk_count"] for m in manifest["books"].values())
    if total != declared:
        issues.append(f"chunk 总数不一致：文件 {total} vs manifest 声明 {declared}")
    return issues, declared


def coverage(haystack: str, needle: str):
    """返回 (needle 被 haystack 匹配块覆盖的比例, 最长逐字重合长度)。基于规范化文本。"""
    a, b = normalize(needle), normalize(haystack)
    if not a:
        return 1.0, 0
    sm = SequenceMatcher(None, a, b, autojunk=False)
    matched = sum(bl.size for bl in sm.get_matching_blocks())
    longest = max((bl.size for bl in sm.get_matching_blocks()), default=0)
    return matched / len(a), longest


def bigram_containment(needle: str, haystack: str):
    a, b = normalize(needle), normalize(haystack)
    if len(a) < 2:
        return 1.0
    grams = {a[i:i + 2] for i in range(len(a) - 1)}
    hit = sum(1 for g in grams if g in b)
    return hit / len(grams)


def main():
    chunks, per_book = load_chunks()
    issues, declared_total = check_manifest(chunks, per_book)
    print(f"语料加载：{len(chunks)} chunks（book1={len(per_book['book1'])} "
          f"book2={len(per_book['book2'])} book3={len(per_book['book3'])}），"
          f"manifest 声明 {declared_total}")
    if issues:
        print("!! manifest 完整性问题：")
        for it in issues:
            print("   -", it)
    else:
        print("manifest 完整性：documents.jsonl uid 清单与 manifest 一致，摘要校验通过")

    items = [json.loads(l) for l in open(TESTSET, encoding="utf-8")]
    print(f"测试集：{len(items)} 条")

    details = []
    flags = Counter()
    for item in items:
        rec = {
            "index": len(details),
            "query_type": item["query_type"],
            "question": item["question"],
            "chunk_ids": item["ground_truth_chunk_ids"],
        }
        missing_uids = [u for u in item["ground_truth_chunk_ids"] if u not in chunks]
        rec["missing_uids"] = missing_uids
        if missing_uids:
            flags["uid 不存在"] += 1

        # 文本一致性
        text_mismatch = []
        for uid, gt in zip(item["ground_truth_chunk_ids"], item["ground_truth_text"]):
            if uid in chunks:
                actual = chunks[uid]["text"]
                if gt != actual and normalize(gt) != normalize(actual):
                    text_mismatch.append(uid)
        rec["text_mismatch_uids"] = text_mismatch
        if text_mismatch:
            flags["ground_truth_text 与 chunk 原文不一致"] += 1

        # 答案与问题溯源（对齐后的语料 = 该题全部 chunk 原文拼接）
        corpus_text = "\n".join(
            chunks[u]["text"] for u in item["ground_truth_chunk_ids"] if u in chunks
        )
        ref_cov, ref_longest = coverage(corpus_text, item["reference"])
        q_cov, q_longest = coverage(corpus_text, item["question"])
        q_bigram = bigram_containment(item["question"], corpus_text)
        rec["reference_containment"] = round(ref_cov, 4)
        rec["reference_longest_verbatim"] = ref_longest
        rec["question_containment"] = round(q_cov, 4)
        rec["question_longest_verbatim"] = q_longest
        rec["question_bigram_containment"] = round(q_bigram, 4)
        rec["chunk_books"] = sorted({chunks[u]["book"] for u in item["ground_truth_chunk_ids"] if u in chunks})
        details.append(rec)

    # 汇总
    n = len(details)
    ok_uid = sum(1 for d in details if not d["missing_uids"])
    ok_text = sum(1 for d in details if not d["text_mismatch_uids"])
    ref_covs = [d["reference_containment"] for d in details]
    q_covs = [d["question_containment"] for d in details]
    q_bi = [d["question_bigram_containment"] for d in details]

    def dist(vals, edges):
        out = Counter()
        for v in vals:
            for lo, hi in edges:
                if lo <= v < hi or (hi == 1.01 and v == 1.0):
                    out[f"{lo}-{hi}"] += 1
                    break
        return dict(out)

    print("\n== 1. uid 真实性 ==")
    print(f"全部 uid 存在：{ok_uid}/{n}")
    print("== 2. ground_truth_text 与 chunk 原文一致 ==")
    print(f"完全一致（含仅空白差异）：{ok_text}/{n}")
    print("== 3. reference 被 chunk 原文覆盖比例 ==")
    print("分布：", dist(ref_covs, [(0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.0), (1.0, 1.01)]))
    print(f"平均 {sum(ref_covs)/n:.3f}，中位 {sorted(ref_covs)[n//2]:.3f}")
    print("== 4. question 与 chunk 词汇关联（改写题面，仅供参考）==")
    print("containment 分布：", dist(q_covs, [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]))
    print("bigram 重合分布：", dist(q_bi, [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]))
    print(f"问题标记的其他问题：{dict(flags)}")

    # 低覆盖明细（answer 溯源弱 → 最值得人工看）
    weak = sorted(details, key=lambda d: d["reference_containment"])[:20]
    print("\n== reference 覆盖率最低 20 条 ==")
    for d in weak:
        print(f"[{d['index']:3d}] {d['query_type']:10s} ref_cov={d['reference_containment']:.2f} "
              f"verbatim={d['reference_longest_verbatim']:3d} q_bigram={d['question_bigram_containment']:.2f} "
              f"uids={d['chunk_ids'][:1]}… {d['question'][:40]}")

    out = {
        "generated_on": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "testset": str(TESTSET),
        "corpus": str(KB_DIR),
        "manifest_issues": issues,
        "summary": {
            "items": n,
            "uid_all_exist": ok_uid,
            "text_all_match": ok_text,
            "reference_containment_mean": round(sum(ref_covs) / n, 4),
            "question_bigram_mean": round(sum(q_bi) / n, 4),
            "flags": dict(flags),
        },
        "details": details,
    }
    out_path = SRC_DIR / "testset_200_chunk_verification.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n明细已写入 {out_path}")


if __name__ == "__main__":
    sys.exit(main())
