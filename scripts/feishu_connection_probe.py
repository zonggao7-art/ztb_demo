"""飞书入口：默认仅接收事件；--reply-test 固定回复；--agent-reply 单轮 Agent 问答。"""

from __future__ import annotations

import argparse
from collections import OrderedDict
import json
import logging
from pathlib import Path
import sys
from threading import Lock
from uuid import NAMESPACE_URL, uuid5

from dotenv import dotenv_values
import lark_oapi as lark
from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody
from lark_oapi.core.log import logger as sdk_logger


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_REPLY = "已收到你的消息，连接测试成功。"


def report(message: str) -> None:
    print(message, flush=True)


class SafeStatusHandler(logging.Handler):
    """SDK 的默认连接日志包含完整 WS URL；只输出固定状态文案。"""

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if message.startswith("connected to "):
            report("[CONNECTED] 飞书长连接已建立，可回开放平台验证并保存。")
        elif message.startswith("disconnected to "):
            report("[DISCONNECTED] 飞书长连接已断开。")
        elif message.startswith("trying to reconnect "):
            report("[RECONNECTING] 正在重新连接飞书。")
        elif record.levelno >= logging.WARNING:
            # 不转发异常正文、URL、消息内容或用户标识。
            report("[SDK_WARNING] 连接或事件处理出现异常；请检查网络和平台配置。")


def read_credentials() -> tuple[str, str]:
    # 固定读取项目根目录，避免从其他工作目录启动时误读；不做变量插值。
    values = dotenv_values(PROJECT_ROOT / ".env", encoding="utf-8-sig", interpolate=False)
    keys = ("FEISHU_APP_ID", "FEISHU_APP_SECRET")
    credentials = tuple((values.get(key) or "").strip() for key in keys)
    missing = [key for key, value in zip(keys, credentials) if not value or value.startswith(("你的", "your_", "<"))]
    if missing:
        raise ValueError("请在项目 .env 中填写：" + ", ".join(missing))
    if not credentials[0].startswith("cli_"):
        raise ValueError("FEISHU_APP_ID 格式不正确，应以 cli_ 开头。")
    return credentials


def on_message(data) -> None:
    """注册接收消息事件；不记录消息正文，也不发送任何飞书消息。"""
    report("[EVENT_RECEIVED] 收到 im.message.receive_v1 事件（仅计接收，不作答）。")


def send_text_reply(api_client, message_id: str, text: str, *, namespace="feishu-probe-reply:") -> bool:
    request = (ReplyMessageRequest.builder().message_id(message_id)
               .request_body(ReplyMessageRequestBody.builder().msg_type("text")
                             .content(json.dumps({"text": text}, ensure_ascii=False))
                             .uuid(str(uuid5(NAMESPACE_URL, namespace + message_id))).build()).build())
    try:
        response = api_client.im.v1.message.reply(request)
    except Exception as exc:
        report(f"[REPLY_FAILED] 请求异常：{type(exc).__name__}；可重新发一条消息。")
        return False
    if not response.success():
        code = response.code if isinstance(response.code, int) else "unknown"
        report(f"[REPLY_FAILED] 飞书 API 返回错误码 {code}；请检查发送权限和发布状态。")
        return False
    report("[REPLY_SENT] 飞书 API 已确认回复发送成功。")
    return True


class FixedReplyHandler:
    """仅对用户的单聊文字消息回复；缓存最近 1000 个成功回复的消息 ID。"""

    def __init__(self, api_client):
        self.api_client = api_client
        self.replied = OrderedDict()
        self.lock = Lock()

    def __call__(self, data) -> None:
        event = getattr(data, "event", None)
        message = getattr(event, "message", None)
        sender = getattr(event, "sender", None)
        if (getattr(message, "chat_type", None) != "p2p"
                or getattr(message, "message_type", None) != "text"
                or getattr(sender, "sender_type", None) != "user"):
            report("[EVENT_SKIPPED] 非用户单聊文字消息，未回复。")
            return
        message_id = getattr(message, "message_id", None)
        if not isinstance(message_id, str) or not message_id:
            report("[EVENT_SKIPPED] 消息缺少有效标识，未回复。")
            return
        with self.lock:
            if message_id in self.replied:
                report("[DUPLICATE_SKIPPED] 此消息已回复，跳过重复事件。")
                return
            report("[EVENT_RECEIVED] 收到单聊文字消息，准备发送固定测试回复。")
            if not send_text_reply(self.api_client, message_id, TEST_REPLY):
                return
            self.replied[message_id] = None
            if len(self.replied) > 1000:
                self.replied.popitem(last=False)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-config", action="store_true", help="仅检查配置完整性，不连接飞书")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--reply-test", action="store_true", help="单聊固定回复测试")
    mode.add_argument("--agent-reply", action="store_true", help="通过本地网页 API 进行单轮 Agent 问答")
    args = parser.parse_args()
    try:
        app_id, app_secret = read_credentials()
    except ValueError as exc:
        report("[CONFIG_ERROR] " + str(exc))
        return 2
    report("[CONFIG_OK] 两项应用凭证已读取，内容不输出。")
    if args.check_config:
        return 0

    sdk_logger.handlers.clear()
    sdk_logger.addHandler(SafeStatusHandler())
    sdk_logger.propagate = False
    callback = on_message
    if args.reply_test or args.agent_reply:
        api_client = (lark.Client.builder().app_id(app_id).app_secret(app_secret)
                      .timeout(10).log_level(lark.LogLevel.INFO).build())
        if args.agent_reply:
            if __package__:
                from .feishu_agent_bridge import AgentReplyHandler
            else:
                from feishu_agent_bridge import AgentReplyHandler
            callback = AgentReplyHandler(
                lambda message_id, text: send_text_reply(api_client, message_id, text,
                                                        namespace="feishu-agent-reply:"), report)
            report("[MODE] 单轮 Agent 问答已开启，复用本地网页 API。")
        else:
            callback = FixedReplyHandler(api_client)
            report("[MODE] 单聊固定回复测试已开启；未接入 Agent。")
    handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(callback).build()
    client = lark.ws.Client(app_id, app_secret, event_handler=handler, log_level=lark.LogLevel.INFO)
    report("[CONNECTING] 正在连接飞书；出现 CONNECTED 后才算连接成功。")
    report("[INFO] 保持此程序运行；终端前台运行时可按 Ctrl+C 停止。")
    try:
        client.start()
    except KeyboardInterrupt:
        report("[STOPPED] 已停止连接验证程序。")
        return 0
    except Exception as exc:
        # SDK 异常可能包含敏感 URL，保留类型与数字错误码用于诊断。
        code = getattr(exc, "code", None)
        suffix = f"，错误码 {code}" if isinstance(code, int) else ""
        report(f"[FAILED] 连接程序退出：{type(exc).__name__}{suffix}。")
        return 1
    finally:
        if args.agent_reply:
            report("[STOPPING] 等待已接收的问题处理结束。")
            callback.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
