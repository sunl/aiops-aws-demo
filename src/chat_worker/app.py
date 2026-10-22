"""Feishu Q&A worker: forwards @bot messages to the DevOps Agent A2A endpoint.

* "调查 <问题>" (or "/investigate <问题>") starts an asynchronous investigation;
  the RCA card is posted back into the same thread by the notifier.
* Anything else is answered synchronously through the `chat` tenant. Follow-up
  messages in the same thread continue the same A2A conversation.
"""
import json
import logging
import os
import re
import time

from demo_common import feishu
from demo_common.a2a import A2AClient
from demo_common.config import get_secret_json

LOG = logging.getLogger()
LOG.setLevel(logging.INFO)

_MENTION = re.compile(r"@_user_\d+")
_INVESTIGATE = re.compile(r"^\s*(?:调查|/investigate|investigate)[\s:：]*(.*)$", re.IGNORECASE | re.DOTALL)
HELP = ("我可以帮你查询运维信息，或发起一次根因调查：\n"
        "- 直接提问，例如：`order-service 最近一次发布改了什么？`\n"
        "- 发起调查，例如：`调查 order-service 从 10:20 开始大量报错`")


def message_text(message: dict) -> str:
    if message.get("message_type") != "text":
        return ""
    try:
        text = json.loads(message.get("content") or "{}").get("text", "")
    except ValueError:
        return ""
    return _MENTION.sub("", text).strip()


def parse_command(text: str):
    """Return ("investigate", question) | ("chat", question) | ("help", "")."""
    if not text:
        return "help", ""
    m = _INVESTIGATE.match(text)
    if m:
        return ("investigate", m.group(1).strip()) if m.group(1).strip() else ("help", "")
    return "chat", text


def _table():
    import boto3

    return boto3.resource("dynamodb").Table(os.environ["STATE_TABLE"])


def handler(event, context):
    message = (event.get("event") or {}).get("message") or {}
    message_id = message.get("message_id")
    thread_key = message.get("root_id") or message_id
    kind, question = parse_command(message_text(message))

    cfg = get_secret_json(os.environ["FEISHU_SECRET_ID"])
    app = feishu.FeishuApp(os.environ.get("FEISHU_BASE_URL", "https://open.feishu.cn"), cfg["app_id"], cfg["app_secret"])
    client = A2AClient(os.environ.get("A2A_REGION", "us-east-1"), os.environ["AGENT_SPACE_ID"])
    table = _table()

    try:
        if kind == "help":
            app.reply_card(message_id, feishu.build_card("👋 AIOps 助手", "blue", [HELP]))
            return {"kind": kind}

        if kind == "investigate":
            info = client.start_investigation(question)
            if info.get("task_id"):
                table.put_item(Item={"pk": f"task#{info['task_id']}", "message_id": message_id,
                                     "ttl": int(time.time()) + 86400})
            app.reply_card(message_id, feishu.build_card(
                "🔍 已发起调查", "blue",
                [f"**问题**：{question}", "DevOps Agent 正在调查（通常 5–8 分钟），结果会回复在本话题。"],
                note=f"task: {info.get('task_id', '-')}"))
            return {"kind": kind, **info}

        conv_key = {"pk": f"conv#{thread_key}"}
        prior = table.get_item(Key=conv_key).get("Item") or {}
        started = time.time()
        answer, task_id = client.chat(question, prior.get("a2a_task_id"))
        if task_id:
            table.put_item(Item={**conv_key, "a2a_task_id": task_id, "ttl": int(time.time()) + 7200})
        body = feishu.truncate(feishu.md_to_lark_md(answer or "（DevOps Agent 没有返回内容）"))
        app.reply_card(message_id, feishu.build_card(
            "💬 DevOps Agent", "blue", [body],
            note=f"耗时 {time.time() - started:.0f} 秒 · 在本话题内继续追问可保留上下文"))
        return {"kind": kind, "a2a_task_id": task_id}
    except Exception as exc:
        LOG.exception("worker failed")
        app.reply_card(message_id, feishu.build_card("⚠️ 处理失败", "red", [f"`{type(exc).__name__}`: {str(exc)[:500]}"]))
        raise
