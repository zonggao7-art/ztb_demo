"""飞书单轮问答适配：复用网页 HTTP 入口，只发布 final 中的最终答案。"""

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Lock
import time
from uuid import NAMESPACE_URL, uuid5

import httpx


AGENT_URL = "http://127.0.0.1:8000/chat/stream"
FAILURE_REPLY = "本次请求未能取得完整结果，请稍后重新发送问题。"
STATUS_LABELS = {
    "complete": "本轮结果",
    "partial": "部分结果",
    "clarify": "需要补充信息",
    "unsupported": "暂不支持",
    "insufficient_evidence": "证据不足",
}


class AgentResponseError(Exception):
    """不把服务器错误正文或中间输出转发给用户。"""


def format_final(payload: dict) -> str:
    business = payload.get("business_result")
    answer = payload.get("answer")
    if (not isinstance(business, dict) or not isinstance(answer, str) or not answer.strip()
            or business.get("answer") != answer
            or business.get("execution_status") not in STATUS_LABELS
            or not isinstance(business.get("data"), dict)):
        raise AgentResponseError("invalid_final")
    sources = business["data"].get("citations", [])
    if not isinstance(sources, list):
        raise AgentResponseError("invalid_citations")
    lines, seen = [], set()
    for source in sources:
        if not isinstance(source, dict):
            raise AgentResponseError("invalid_citation")
        index = source.get("context_index")
        title, chapter = source.get("doc_name"), source.get("chapter")
        if (type(index) is not int or index < 1 or index in seen
                or not isinstance(title, str) or not isinstance(chapter, str)):
            raise AgentResponseError("invalid_citation")
        seen.add(index)
        lines.append(f"【来源{index}】{title}" + (f" · {chapter}" if chapter else ""))
    text = STATUS_LABELS[business["execution_status"]] + "\n\n" + answer
    if lines:
        text += "\n\n来源索引：\n" + "\n".join(lines)
    # 不截断业务结论、保留意见或引用。过长时明确说明单条文本展示限制。
    if len(json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")) > 16000:
        return "本轮结果较长，暂无法完整显示在飞书单条消息中。请缩小问题范围，或在网页端查询完整结果。"
    return text


def query_agent(question: str, thread_id: str, *, transport=None) -> str:
    """读取 SSE 直到 final；忽略 token、工具过程和心跳，不自行拼接答案。"""
    started = time.monotonic()
    with httpx.Client(timeout=httpx.Timeout(20, connect=5), trust_env=False, transport=transport) as client:
        with client.stream("POST", AGENT_URL, json={
            "question": question, "thread_id": thread_id, "deadline_s": 90,
        }) as response:
            response.raise_for_status()
            if "text/event-stream" not in response.headers.get("content-type", ""):
                raise AgentResponseError("not_sse")
            fields, size, request_id = {}, 0, None
            for line in response.iter_lines():
                if time.monotonic() - started > 110:
                    raise AgentResponseError("deadline_exceeded")
                size += len(line.encode("utf-8"))
                if size > 2_000_000:
                    raise AgentResponseError("frame_too_large")
                if line.startswith(":"):
                    continue
                if line:
                    name, separator, value = line.partition(":")
                    if separator and name in {"id", "event", "data"}:
                        if name in fields:
                            raise AgentResponseError("duplicate_sse_field")
                        fields[name] = value.removeprefix(" ")
                    continue
                if not fields:
                    size = 0
                    continue
                event = json.loads(fields.get("data", ""))
                if (not isinstance(event, dict) or not isinstance(event.get("payload"), dict)
                        or not isinstance(event.get("request_id"), str) or not event["request_id"]
                        or fields.get("id") != event["request_id"]
                        or fields.get("event") != event.get("type")):
                    raise AgentResponseError("invalid_envelope")
                if request_id is not None and request_id != event["request_id"]:
                    raise AgentResponseError("mixed_request_ids")
                request_id = event["request_id"]
                fields, size = {}, 0
                if event["type"] in {"error", "cancelled"}:
                    raise AgentResponseError("request_incomplete")
                if event["type"] == "final":
                    return format_final(event["payload"])
    raise AgentResponseError("missing_final")


class AgentReplyHandler:
    """快速接收，单工作线程处理；最多 8 个待处理问题，缓存最近 1000 条结果。

    待处理重复消息不再入队。发送失败时保留已生成的答案，事件重投时可重发，
    不重复消耗模型调用。缓存仅在进程内有效，适用于本地受控演示。
    """

    def __init__(self, send_reply, report, *, query=query_agent):
        self.send_reply, self.report, self.query = send_reply, report, query
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="feishu-agent")
        self.lock = Lock()
        self.active = set()
        self.results = OrderedDict()

    def __call__(self, data):
        event = getattr(data, "event", None)
        message, sender = getattr(event, "message", None), getattr(event, "sender", None)
        if (getattr(message, "chat_type", None) != "p2p"
                or getattr(message, "message_type", None) != "text"
                or getattr(sender, "sender_type", None) != "user"):
            self.report("[EVENT_SKIPPED] 仅支持用户单聊文字问题。")
            return
        message_id, chat_id = getattr(message, "message_id", None), getattr(message, "chat_id", None)
        if not isinstance(message_id, str) or not message_id or not isinstance(chat_id, str) or not chat_id:
            self.report("[EVENT_SKIPPED] 缺少消息或会话标识。")
            return
        try:
            content = json.loads(message.content)
            question = content.get("text") if isinstance(content, dict) else None
        except (ValueError, TypeError, AttributeError):
            question = None
        # 独立问题使用独立后端 thread_id；不携带飞书历史，不泄漏原始 chat_id。
        thread_id = "feishu:" + uuid5(NAMESPACE_URL, chat_id + ":" + message_id).hex
        with self.lock:
            cached = self.results.get(message_id)
            if message_id in self.active or (cached and cached[1]):
                self.report("[DUPLICATE_SKIPPED] 消息正在处理或已回复。")
                return
            if len(self.active) >= 8:
                # 抛出异常让 SDK 返回失败回执，避免把未入队的事件确认成功。
                self.report("[QUEUE_FULL] 待处理问题已达上限，未接收本次事件。")
                raise RuntimeError("feishu_queue_full")
            self.active.add(message_id)
        try:
            self.pool.submit(self._process, message_id, thread_id, question, cached)
        except Exception:
            with self.lock:
                self.active.discard(message_id)
            raise
        self.report("[QUESTION_QUEUED] 单聊问题已入队。")

    def _process(self, message_id, thread_id, question, cached):
        try:
            if cached:
                answer = cached[0]
            elif not isinstance(question, str) or not question.strip():
                answer = "请输入完整的文字问题。当前按单轮处理，请在每条消息中写明查询对象和要求。"
            elif len(question.encode("utf-8")) > 16000:
                answer = "问题过长，请缩短后重新发送。"
            else:
                self.report("[AGENT_STARTED] 开始调用与网页共用的 Agent 接口。")
                try:
                    answer = self.query(question.strip(), thread_id)
                    self.report("[AGENT_FINAL] 已取得可展示的最终结果。")
                except Exception as exc:
                    self.report(f"[AGENT_FAILED] 本轮未取得完整结果：{type(exc).__name__}。")
                    answer = FAILURE_REPLY
            with self.lock:
                self.results[message_id] = (answer, False)
                self.results.move_to_end(message_id)
                while len(self.results) > 1000:
                    self.results.popitem(last=False)
            sent = self.send_reply(message_id, answer)
            with self.lock:
                self.results[message_id] = (answer, bool(sent))
        except Exception as exc:
            self.report(f"[BRIDGE_FAILED] 本轮处理异常：{type(exc).__name__}。")
        finally:
            with self.lock:
                self.active.discard(message_id)

    def close(self):
        self.pool.shutdown(wait=True)
