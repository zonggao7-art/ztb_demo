"""Request-local immutable snapshots. Model-visible evidence is tracked separately."""
import hashlib
import json
from copy import deepcopy

from agent.tools.base import render_tool_content
from .context import RunStopped


def stable_id(prefix, payload):
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()
    return prefix + hashlib.sha256(raw).hexdigest()[:20]


class EvidenceLedger:
    def __init__(self):
        self.entries = {}
        self.snapshots = []
        self.rag_answers = {}

    def ingest(self, task_id, tool, result, max_chars):
        snapshot = deepcopy(result)
        self.snapshots.append({"task_id": task_id, "tool": tool, "result": snapshot})
        data = snapshot.get("data") or {}
        if tool == "knowledge_qa" and snapshot.get("ok"):
            from .rag_result import validate_rag_result
            _, citations, is_refusal = validate_rag_result(data)
            self.rag_answers[task_id] = deepcopy(data)
            # 完整答案与来源留在请求内。外层只接收完成标记，不再摘要一次。
            for citation in citations:
                payload = citation.to_dict()
                eid = stable_id("e_", {"task": task_id, "citation": payload})
                self.entries[eid] = {"kind": "legal", "payload": payload,
                    "tasks": {task_id}, "metadata": {"rag_answer": True}}
            return json.dumps({"ok": True, "data": {
                "rag_answer": {"available": True, "is_refusal": is_refusal}}})
        entries = {}
        view = {"records": [], "chunks": []}
        if snapshot.get("ok"):
            for row in data.get("records", []):
                payload = {k: v for k, v in row.items() if not k.startswith("_")}
                eid = stable_id("r_", {"task": task_id, "row": payload})
                entries[eid] = {"kind": "record", "payload": payload, "tasks": {task_id},
                                "metadata": deepcopy(snapshot.get("metadata") or {})}
                view["records"].append({**payload, "record_ref": eid})
            # 检索片段是原始证据；完整 RAG 答案单独保存，不冒充证据。
            for chunk in data.get("chunks", data.get("citations", [])):
                payload = deepcopy(chunk)
                meta = payload.get("metadata") or {}
                for key in ("chunk_id", "chunk_uid", "doc_name", "chapter"):
                    payload[key] = payload.get(key) or meta.get(key)
                eid = stable_id("e_", {k: payload.get(k) for k in
                    ("chunk_id", "chunk_uid", "doc_name", "chapter", "text")})
                entries[eid] = {"kind": "legal", "payload": payload, "tasks": {task_id}, "metadata": {}}
                # Citation metadata stays in the ledger; the model only needs
                # the reference and a verbatim excerpt, not duplicate metadata.
                view["chunks"].append({"text": payload.get("text", ""), "evidence_id": eid})
        if view["chunks"]:
            # Share the bounded observation across sources instead of dropping
            # every source except the first long chunk.
            text_limit = max(80, min(500, (min(max_chars, 2400) - 100 * len(view["chunks"])) // len(view["chunks"])))
            for chunk in view["chunks"]:
                excerpt = chunk["text"][:text_limit]
                if len(chunk["text"]) > text_limit:
                    # Prefer a complete sentence within the same hard limit.
                    boundary = max(excerpt.rfind("。"), excerpt.rfind("；"), excerpt.rfind("\n"))
                    if boundary >= text_limit // 2:
                        excerpt = excerpt[:boundary + 1]
                chunk["text"] = excerpt
                # Keep the complete source for citation back-checks, separately
                # track what the model actually saw for quote authorization.
                entries[chunk["evidence_id"]]["visible_text"] = chunk["text"]
        envelope = {"ok": bool(snapshot.get("ok")), "data": view, "error": snapshot.get("error")}
        content = render_tool_content(envelope, max_chars=max_chars)
        visible = json.loads(content)["data"]
        for key, id_key in (("records", "record_ref"), ("chunks", "evidence_id")):
            for item in visible.get(key, []):
                eid = item[id_key]
                if eid in self.entries:
                    self.entries[eid]["tasks"].add(task_id)
                else:
                    self.entries[eid] = entries[eid]
        return content

    def for_task(self, task_id, kind=None):
        return [{"id": k, **v} for k, v in self.entries.items()
                if task_id in v["tasks"] and (kind is None or v["kind"] == kind)]

    def get(self, eid, task_id, kind):
        item = self.entries.get(eid)
        if item is None or task_id not in item["tasks"] or item["kind"] != kind:
            raise RunStopped("unknown_evidence")
        return deepcopy(item)
