"""Feishu / Lark helpers: custom-bot signing, app API, and message cards.

Two delivery paths are supported:
* Group custom bot webhook (no app needed) for alert and RCA cards.
* Enterprise app (tenant_access_token) for replying to a specific message,
  used by the optional Q&A bot.
"""
import base64
import hashlib
import hmac
import json
import re
import time
import urllib.error
import urllib.request

CARD_TEXT_LIMIT = 3500  # keep cards well below Feishu's message size limit
TRUNCATION_NOTE = "\n\n…（内容较长，完整报告请在 DevOps Agent Web App 查看）"


class FeishuError(RuntimeError):
    """Raised when a Feishu API call returns a non-zero code."""


def bot_sign(timestamp: int, secret: str) -> str:
    """Signature for a custom bot with "signature verification" enabled.

    Feishu's algorithm: HMAC-SHA256 keyed with "{timestamp}\\n{secret}" over an
    empty message, then base64.
    """
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(string_to_sign, b"", digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _post_json(url: str, payload: dict, headers: dict = None, timeout: int = 10) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:  # surface Feishu's error body
        raise FeishuError(f"HTTP {exc.code} from Feishu: {exc.read().decode('utf-8', 'replace')}") from exc
    return json.loads(body) if body else {}


def _check(resp: dict) -> dict:
    code = resp.get("code", resp.get("StatusCode", 0))
    if code not in (0, None):
        raise FeishuError(f"Feishu error: {resp}")
    return resp


def send_group_card(webhook_url: str, card: dict, secret: str = "") -> dict:
    """Post an interactive card through a group custom bot webhook."""
    payload = {"msg_type": "interactive", "card": card}
    if secret:
        ts = int(time.time())
        payload["timestamp"] = str(ts)
        payload["sign"] = bot_sign(ts, secret)
    return _check(_post_json(webhook_url, payload))


class FeishuApp:
    """Minimal enterprise-app client: tenant token + message reply."""

    def __init__(self, base_url: str, app_id: str, app_secret: str):
        self.base_url = base_url.rstrip("/")
        self.app_id = app_id
        self.app_secret = app_secret
        self._token = None
        self._token_expiry = 0.0

    def tenant_token(self) -> str:
        if self._token and time.time() < self._token_expiry:
            return self._token
        resp = _check(
            _post_json(
                f"{self.base_url}/open-apis/auth/v3/tenant_access_token/internal",
                {"app_id": self.app_id, "app_secret": self.app_secret},
            )
        )
        self._token = resp["tenant_access_token"]
        self._token_expiry = time.time() + int(resp.get("expire", 7200)) - 120
        return self._token

    def send_card(self, chat_id: str, card: dict) -> dict:
        """Post a card to a group as the app bot (im:message:send_as_bot; the bot must be in the group)."""
        return _check(
            _post_json(
                f"{self.base_url}/open-apis/im/v1/messages?receive_id_type=chat_id",
                {"receive_id": chat_id, "msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False)},
                headers={"Authorization": f"Bearer {self.tenant_token()}"},
            )
        )

    def reply_card(self, message_id: str, card: dict, in_thread: bool = True) -> dict:
        return _check(
            _post_json(
                f"{self.base_url}/open-apis/im/v1/messages/{message_id}/reply",
                {"msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False), "reply_in_thread": in_thread},
                headers={"Authorization": f"Bearer {self.tenant_token()}"},
            )
        )


def post_to_group(cfg: dict, card: dict, base_url: str, chat_id: str = "") -> str:
    """Deliver a card to the demo group.

    As the app bot when chat_id and app credentials are set (one bot in the group),
    otherwise through the custom bot webhook. Returns "app" | "webhook" | "dropped".
    """
    if chat_id and cfg.get("app_id") and cfg.get("app_secret"):
        FeishuApp(base_url, cfg["app_id"], cfg["app_secret"]).send_card(chat_id, card)
        return "app"
    if cfg.get("group_webhook_url"):
        send_group_card(cfg["group_webhook_url"], card, cfg.get("group_webhook_secret", ""))
        return "webhook"
    return "dropped"


_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*\S)\s*$")


def md_to_lark_md(markdown: str) -> str:
    """Adapt agent Markdown to the lark_md subset (no headings, no tables)."""
    out = []
    for line in markdown.splitlines():
        m = _HEADING.match(line)
        if m:
            text = m.group(2).strip("* ")
            out.append(f"**{text}**")
        else:
            out.append(line.rstrip())
    # collapse runs of blank lines
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return text


def truncate(text: str, limit: int = CARD_TEXT_LIMIT) -> str:
    """Cut at a line boundary below `limit` characters and append a note."""
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    if cut < limit // 2:
        cut = limit
    return text[:cut].rstrip() + TRUNCATION_NOTE


def build_card(title: str, template: str, sections: list, note: str = "", button: tuple = None) -> dict:
    """Build a v1 interactive card.

    template: blue / green / orange / red / grey ...
    sections: list of lark_md strings, separated by dividers.
    button: optional (label, url).
    """
    elements = []
    for i, section in enumerate(s for s in sections if s):
        if i:
            elements.append({"tag": "hr"})
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": section}})
    if button and button[1]:
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {"tag": "button", "text": {"tag": "plain_text", "content": button[0]}, "type": "primary", "url": button[1]}
                ],
            }
        )
    if note:
        elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": note}]})
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": template, "title": {"tag": "plain_text", "content": title}},
        "elements": elements,
    }
