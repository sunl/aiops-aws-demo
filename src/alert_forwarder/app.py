"""Alert forwarder: CloudWatch alarm (via SNS) -> AWS DevOps Agent generic webhook.

* Normalises the alarm into the webhook payload. Only title, description,
  priority and the incident reference reach the agent, so all useful context is
  written into `description`.
* Collapses repeated ALARM transitions of the same alarm for COLLAPSE_SECONDS.
* Signs the request with HMAC-SHA256 (x-amzn-event-signature / -timestamp).
* Posts an alert card to the Feishu group so the audience sees the alarm land.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from demo_common import feishu
from demo_common.config import get_secret_json

LOG = logging.getLogger()
LOG.setLevel(logging.INFO)

PRIORITY_TAGS = {"[P0]": "CRITICAL", "[P1]": "HIGH", "[P2]": "MEDIUM", "[P3]": "LOW"}


def priority_from(description: str) -> str:
    for tag, priority in PRIORITY_TAGS.items():
        if tag in (description or ""):
            return priority
    return "HIGH"


def sign(secret: str, timestamp: str, body: str) -> str:
    """base64(HMAC-SHA256(secret, f"{timestamp}:{body}")) as documented for HMAC webhooks."""
    digest = hmac.new(secret.encode("utf-8"), f"{timestamp}:{body}".encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def webhook_timestamp(now: float = None) -> str:
    ts = time.time() if now is None else now
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def describe_alarm(alarm: dict, service: str, environment: str) -> str:
    trigger = alarm.get("Trigger") or {}
    dims = ", ".join(f"{d.get('name')}={d.get('value')}" for d in trigger.get("Dimensions") or [])
    lines = [
        f"告警 {alarm.get('AlarmName')} 进入 ALARM 状态（{alarm.get('StateChangeTime')}）。",
        f"说明：{alarm.get('AlarmDescription') or '-'}",
        f"原因：{alarm.get('NewStateReason') or '-'}",
        f"指标：{trigger.get('Namespace')}/{trigger.get('MetricName')} {trigger.get('Statistic') or trigger.get('ExtendedStatistic')}"
        f"，周期 {trigger.get('Period')} 秒，阈值 {trigger.get('ComparisonOperator')} {trigger.get('Threshold')}",
        f"资源：{dims or '-'}",
        f"服务：{service}；环境：{environment}；账号：{alarm.get('AWSAccountId')}；区域：{alarm.get('Region')}",
        "请结合最近的配置变更、发布记录（制品库 Nexus）和日志定位根因，并给出处置建议。",
    ]
    return "\n".join(lines)


def build_incident(alarm: dict, action: str, incident_id: str, service: str, environment: str) -> dict:
    payload = {
        "eventType": "incident",
        "incidentId": incident_id,
        "action": action,
        "priority": priority_from(alarm.get("AlarmDescription")),
        "title": f"[{service}] {alarm.get('AlarmName')}",
        "description": describe_alarm(alarm, service, environment),
        "timestamp": webhook_timestamp(),
        "service": service,
        "data": {"metadata": {"alarmArn": alarm.get("AlarmArn"), "environment": environment}},
    }
    if action != "created":
        payload["description"] = f"告警 {alarm.get('AlarmName')} 已恢复（{alarm.get('NewStateValue')}）：{alarm.get('NewStateReason')}"
    return payload


def alarm_card(alarm: dict, service: str, recovered: bool) -> dict:
    trigger = alarm.get("Trigger") or {}
    if recovered:
        return feishu.build_card(
            f"✅ 告警恢复：{alarm.get('AlarmName')}", "green",
            [f"**服务**：{service}\n**时间**：{alarm.get('StateChangeTime')}\n**原因**：{alarm.get('NewStateReason')}"],
            note="CloudWatch → SNS → 告警转换 Lambda",
        )
    return feishu.build_card(
        f"🚨 告警：{alarm.get('AlarmName')}", "red",
        [
            f"**服务**：{service}\n**指标**：{trigger.get('Namespace')}/{trigger.get('MetricName')}"
            f"（阈值 {trigger.get('ComparisonOperator')} {trigger.get('Threshold')}）\n"
            f"**时间**：{alarm.get('StateChangeTime')}\n**原因**：{alarm.get('NewStateReason')}",
            "🤖 已自动转交 **AWS DevOps Agent** 调查，根因分析完成后推送到本群。",
        ],
        note="CloudWatch → SNS → 告警转换 Lambda → DevOps Agent Webhook",
    )


def post_webhook(url: str, secret: str, payload: dict) -> int:
    body = json.dumps(payload, ensure_ascii=False)
    ts = webhook_timestamp()
    req = urllib.request.Request(
        url, data=body.encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "x-amzn-event-timestamp": ts,
                 "x-amzn-event-signature": sign(secret, ts, body)},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                LOG.info("webhook accepted: %s %s", resp.status, resp.read()[:200])
                return resp.status
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300]
            if exc.code < 500:
                raise RuntimeError(f"webhook rejected {exc.code}: {detail}") from exc
            LOG.warning("webhook %s (attempt %s): %s", exc.code, attempt + 1, detail)
        except urllib.error.URLError as exc:
            LOG.warning("webhook network error (attempt %s): %s", attempt + 1, exc)
        time.sleep(2 ** attempt)
    raise RuntimeError("webhook failed after 3 attempts")


def _table():
    import boto3

    return boto3.resource("dynamodb").Table(os.environ["STATE_TABLE"])


def _notify_feishu(card: dict):
    try:
        cfg = get_secret_json(os.environ["FEISHU_SECRET_ID"])
        via = feishu.post_to_group(cfg, card, os.environ.get("FEISHU_BASE_URL", "https://open.feishu.cn"),
                                   os.environ.get("FEISHU_CHAT_ID", ""))
        LOG.info("feishu card delivered via %s", via)
    except Exception:  # the demo must still reach DevOps Agent if Feishu is misconfigured
        LOG.exception("feishu notification failed")


def process_alarm(alarm: dict):
    from botocore.exceptions import ClientError

    service = os.environ.get("SERVICE_NAME", "order-service")
    environment = os.environ.get("ENVIRONMENT", "demo")
    hook = get_secret_json(os.environ["WEBHOOK_SECRET_ID"])
    key = {"pk": f"alarm#{alarm['AlarmName']}"}
    table = _table()
    state = alarm.get("NewStateValue")

    if state == "ALARM":
        incident_id = f"{alarm['AlarmName']}-{int(time.time())}"
        try:
            table.put_item(
                Item={**key, "incident_id": incident_id,
                      "ttl": int(time.time()) + int(os.environ.get("COLLAPSE_SECONDS", "900"))},
                ConditionExpression="attribute_not_exists(pk) OR #ttl < :now",
                ExpressionAttributeNames={"#ttl": "ttl"},
                ExpressionAttributeValues={":now": int(time.time())},
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                LOG.info("suppressed repeated ALARM for %s", alarm["AlarmName"])
                return {"suppressed": True}
            raise
        post_webhook(hook["url"], hook["secret"], build_incident(alarm, "created", incident_id, service, environment))
        _notify_feishu(alarm_card(alarm, service, recovered=False))
        return {"incident_id": incident_id, "action": "created"}

    if state == "OK":
        item = table.get_item(Key=key).get("Item")
        if not item:
            return {"skipped": "no open incident"}
        post_webhook(hook["url"], hook["secret"],
                     build_incident(alarm, "resolved", item["incident_id"], service, environment))
        table.delete_item(Key=key)
        _notify_feishu(alarm_card(alarm, service, recovered=True))
        return {"incident_id": item["incident_id"], "action": "resolved"}

    return {"skipped": state}


def handler(event, context):
    results = []
    for record in event.get("Records", []):
        alarm = json.loads(record["Sns"]["Message"])
        LOG.info("alarm %s -> %s", alarm.get("AlarmName"), alarm.get("NewStateValue"))
        results.append(process_alarm(alarm))
    return results
