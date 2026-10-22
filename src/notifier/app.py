"""DevOps Agent investigation events (EventBridge) -> Feishu cards.

* Investigation Created  -> "调查已开始" card
* Investigation Completed -> RCA card built from the investigation summary
  (read with ListJournalRecords; the event itself carries only the record id)
* Failed / Timed Out / Cancelled / Linked / Skipped -> status card
* Mitigation Completed -> mitigation summary card

Investigations started from the Feishu Q&A bot are answered in that thread
(the chat worker stores task_id -> message_id); everything else goes to the
demo group as the app bot (FEISHU_CHAT_ID), or through the custom bot webhook
when no chat_id is configured.
"""
import logging
import os
import re

from demo_common import feishu
from demo_common.config import get_secret_json

LOG = logging.getLogger()
LOG.setLevel(logging.INFO)

STATUS_CARDS = {
    "Investigation Failed": ("❌ 调查失败", "red"),
    "Investigation Timed Out": ("⏱️ 调查超时", "orange"),
    "Investigation Cancelled": ("⏹️ 调查已取消", "grey"),
    "Investigation Linked": ("🔗 已关联到进行中的调查（重复告警）", "grey"),
    "Investigation Skipped": ("⏭️ 调查已按 Skill 规则跳过", "grey"),
}

_CAUSE = re.compile(r"^#{2,4}\s*(?:Cause|根因|原因)\s*[:：]\s*(.+)$", re.IGNORECASE)


def extract_causes(summary_md: str, limit: int = 3) -> list:
    """Pull 'Cause: ...' headings out of the investigation summary."""
    causes = []
    for line in summary_md.splitlines():
        m = _CAUSE.match(line.strip())
        if m:
            causes.append(m.group(1).strip())
    return causes[:limit]


def summary_text(content) -> str:
    """Journal record content is a Markdown string; tolerate a dict wrapper."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        for key in ("markdown", "content", "text", "summary"):
            if isinstance(content.get(key), str):
                return content[key]
    return str(content or "")


def rca_card(task: dict, summary_md: str, app_url: str) -> dict:
    causes = extract_causes(summary_md)
    head = f"**调查**：{task.get('title', '-')}\n**优先级**：{task.get('priority', '-')}"
    cause_block = "**根因结论**\n" + "\n".join(f"- {c}" for c in causes) if causes else ""
    body = feishu.truncate(feishu.md_to_lark_md(summary_md))
    return feishu.build_card(
        "✅ DevOps Agent 根因分析完成", "green",
        [head, cause_block, body],
        note="AWS DevOps Agent · 处置操作请人工确认后执行",
        button=("查看完整调查", app_url),
    )


def _client():
    import boto3

    return boto3.client("devops-agent")


def fetch_summary(space_id: str, execution_id: str, record_id: str, record_type: str) -> str:
    if not (execution_id and record_id):
        return ""
    paginator = _client().get_paginator("list_journal_records")
    for page in paginator.paginate(agentSpaceId=space_id, executionId=execution_id, recordType=record_type):
        for record in page.get("records", []):
            if record.get("recordId") == record_id:
                return summary_text(record.get("content"))
    return ""


def fetch_task(space_id: str, task_id: str) -> dict:
    if not task_id:
        return {}
    try:
        return _client().get_backlog_task(agentSpaceId=space_id, taskId=task_id).get("task", {})
    except Exception:
        LOG.exception("get_backlog_task failed for %s", task_id)
        return {}


def _thread_target(task_id: str):
    """message_id of the Feishu message that started this investigation, if any."""
    if not (task_id and os.environ.get("CHAT_BOT_ENABLED") == "true"):
        return None
    import boto3

    item = boto3.resource("dynamodb").Table(os.environ["STATE_TABLE"]).get_item(Key={"pk": f"task#{task_id}"}).get("Item")
    return item.get("message_id") if item else None


def deliver(card: dict, task_id: str):
    cfg = get_secret_json(os.environ["FEISHU_SECRET_ID"])
    message_id = _thread_target(task_id)
    if message_id and cfg.get("app_id"):
        app = feishu.FeishuApp(os.environ.get("FEISHU_BASE_URL", "https://open.feishu.cn"), cfg["app_id"], cfg["app_secret"])
        app.reply_card(message_id, card)
        return "thread"
    via = feishu.post_to_group(cfg, card, os.environ.get("FEISHU_BASE_URL", "https://open.feishu.cn"),
                               os.environ.get("FEISHU_CHAT_ID", ""))
    if via == "dropped":
        LOG.warning("no Feishu destination configured; card dropped")
    return via


def build_card_for(detail_type: str, detail: dict, app_url: str):
    meta, data = detail.get("metadata", {}), detail.get("data", {})
    space_id, task_id, execution_id = meta.get("agent_space_id"), meta.get("task_id"), meta.get("execution_id")
    task = fetch_task(space_id, task_id)
    head = f"**调查**：{task.get('title', task_id or '-')}\n**优先级**：{data.get('priority') or task.get('priority', '-')}"

    if detail_type == "Investigation Created":
        return feishu.build_card("🔍 DevOps Agent 开始调查", "blue",
                                 [head, "正在关联指标、日志、变更记录与制品库信息…"],
                                 button=("实时查看调查过程", app_url))
    if detail_type == "Investigation Completed":
        summary = fetch_summary(space_id, execution_id, data.get("summary_record_id"), "investigation_summary_md")
        if not summary:
            return feishu.build_card("✅ 调查已完成", "green", [head, "本次调查未生成摘要，请在 Web App 查看详情。"],
                                     button=("查看调查", app_url))
        return rca_card(task, summary, app_url)
    if detail_type == "Mitigation Completed":
        summary = fetch_summary(space_id, execution_id, data.get("summary_record_id"), "mitigation_summary_md")
        return feishu.build_card("🛠️ 处置方案已生成", "orange",
                                 [head, feishu.truncate(feishu.md_to_lark_md(summary or "请在 Web App 查看处置方案。"))],
                                 note="处置操作请人工确认后执行", button=("查看处置方案", app_url))
    if detail_type in STATUS_CARDS:
        title, color = STATUS_CARDS[detail_type]
        return feishu.build_card(title, color, [head], button=("查看调查", app_url))
    return None


def handler(event, context):
    detail_type = event.get("detail-type", "")
    detail = event.get("detail", {})
    LOG.info("event %s task=%s", detail_type, detail.get("metadata", {}).get("task_id"))
    card = build_card_for(detail_type, detail, os.environ.get("OPERATOR_APP_URL", ""))
    if card is None:
        return {"skipped": detail_type}
    return {"delivered": deliver(card, detail.get("metadata", {}).get("task_id"))}
