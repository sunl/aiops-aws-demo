"""Feishu long-connection (WebSocket) client for the optional Q&A bot.

This process connects OUT to Feishu, so the demo needs no public callback URL
(some security baselines remove public Lambda permissions, and an inbound
endpoint is a larger exposure anyway). For each @bot message it:

1. ignores messages from bots and from chats outside ALLOWED_CHAT_IDS,
2. de-duplicates on message_id (Feishu may re-push with a new event_id),
3. hands the message to the chat-worker Lambda asynchronously.

The handler must finish within 3 seconds, which is why the actual DevOps Agent
call stays in the worker. Start/stop it with scripts/feishu-ws.sh.

AWS permissions needed by the credentials it runs with: secretsmanager:GetSecretValue
on aiops-demo/feishu, dynamodb:PutItem on aiops-demo-state, lambda:InvokeFunction on
aiops-demo-chat-worker.
"""
import json
import logging
import os
import re
import sys
import time

LOG = logging.getLogger("feishu-ws")
WORKER_FIELDS = ("message_id", "root_id", "parent_id", "chat_id", "chat_type", "message_type", "content")


class RedactWsUrl(logging.Filter):
    """Drop the query string (connection ticket) from the wss:// URL the SDK logs on connect."""

    _URL = re.compile(r"(wss://[^\s?]+)\?\S+")

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "wss://" in message:
            record.msg, record.args = self._URL.sub(r"\1?<redacted>", message), ()
        return True


def route(payload: dict, allowed_chats: str):
    """Return (kind, value): ("ignore", reason) or ("message", dedup key)."""
    event = payload.get("event") or {}
    message = event.get("message") or {}
    if (event.get("sender") or {}).get("sender_type") in ("app", "bot"):
        return "ignore", "message from a bot"
    ids = {c.strip() for c in (allowed_chats or "").split(",") if c.strip()}
    if ids and message.get("chat_id") not in ids:
        return "ignore", "chat not allowed"
    key = message.get("message_id") or (payload.get("header") or {}).get("event_id")
    if not key:
        return "ignore", "no message_id"
    return "message", key


def worker_payload(payload: dict) -> dict:
    """Only the message fields chat-worker reads; sender identity stays here."""
    message = (payload.get("event") or {}).get("message") or {}
    return {"event": {"message": {k: message[k] for k in WORKER_FIELDS if message.get(k)}}}


def _error_code(exc) -> str:
    return (getattr(exc, "response", None) or {}).get("Error", {}).get("Code", "")


class Dispatcher:
    """Filter, de-duplicate and forward one message event. Returns the outcome."""

    def __init__(self, table, lambda_client, worker_function: str, allowed_chats: str = ""):
        self.table = table
        self.lambda_client = lambda_client
        self.worker_function = worker_function
        self.allowed_chats = allowed_chats

    def __call__(self, payload: dict) -> str:
        chat_id = ((payload.get("event") or {}).get("message") or {}).get("chat_id", "")
        kind, value = route(payload, self.allowed_chats)
        if kind == "ignore":
            # chat_id is logged so the demo group's ID can be copied into ALLOWED_CHAT_IDS
            LOG.info("ignored: %s chat_id=%s", value, chat_id)
            return "ignored"
        try:
            self.table.put_item(Item={"pk": f"feishu-msg#{value}", "ttl": int(time.time()) + 86400},
                                ConditionExpression="attribute_not_exists(pk)")
        except Exception as exc:  # botocore ClientError; duck-typed so tests need no AWS SDK
            if _error_code(exc) == "ConditionalCheckFailedException":
                LOG.info("duplicate message %s chat_id=%s", value, chat_id)
                return "duplicate"
            raise
        self.lambda_client.invoke(FunctionName=self.worker_function, InvocationType="Event",
                                  Payload=json.dumps(worker_payload(payload), ensure_ascii=False).encode("utf-8"))
        LOG.info("accepted message %s chat_id=%s", value, chat_id)
        return "accepted"


def wait_for_credentials(read_secret, sleep=time.sleep, interval: int = 30) -> dict:
    """Block until the Feishu app credentials are present.

    The stack creates the secret empty and scripts/setup-secrets.sh fills it in
    later. Waiting (instead of exiting) keeps the Fargate task healthy, so a first
    deployment is not rolled back by the ECS deployment circuit breaker, and the
    client connects within one interval of the credentials being written.
    """
    warned = False
    while True:
        try:
            cfg = read_secret()
        except Exception:  # transient Secrets Manager / network errors: retry
            LOG.exception("reading the Feishu secret failed; retrying in %ss", interval)
            cfg = {}
        if cfg.get("app_id") and cfg.get("app_secret"):
            return cfg
        if not warned:
            LOG.warning("app_id / app_secret are empty: waiting for scripts/setup-secrets.sh (checking every %ss)",
                        interval)
            warned = True
        sleep(interval)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    redact = RedactWsUrl()
    for handler in logging.getLogger().handlers:
        handler.addFilter(redact)
    logging.getLogger("Lark").addFilter(redact)  # the SDK's own logger also prints via its own handler
    import boto3
    import lark_oapi as lark

    session = boto3.Session(region_name=os.environ.get("AWS_REGION", "us-east-1"))
    secret_id = os.environ.get("FEISHU_SECRET_ID", "aiops-demo/feishu")
    secrets = session.client("secretsmanager")
    cfg = wait_for_credentials(lambda: json.loads(secrets.get_secret_value(SecretId=secret_id)["SecretString"]))

    dispatch = Dispatcher(
        session.resource("dynamodb").Table(os.environ.get("STATE_TABLE", "aiops-demo-state")),
        session.client("lambda"),
        os.environ.get("WORKER_FUNCTION", "aiops-demo-chat-worker"),
        os.environ.get("ALLOWED_CHAT_IDS", ""),
    )

    def on_message(data) -> None:
        try:
            dispatch(json.loads(lark.JSON.marshal(data)))
        except Exception:
            # Do not re-raise: the message is already recorded for de-duplication,
            # so a Feishu re-push would only be dropped as a duplicate.
            LOG.exception("dispatch failed")

    handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(on_message).build()
    domain = os.environ.get("FEISHU_BASE_URL", lark.FEISHU_DOMAIN)
    LOG.info("starting long connection: app_id=%s domain=%s allowed_chats=%s",
             cfg["app_id"], domain, os.environ.get("ALLOWED_CHAT_IDS") or "<all>")
    lark.ws.Client(cfg["app_id"], cfg["app_secret"], event_handler=handler,
                   log_level=lark.LogLevel.INFO, domain=domain, auto_reconnect=True).start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
