#!/usr/bin/env bash
# Store the DevOps Agent webhook and Feishu credentials in Secrets Manager.
# Values are read with `read -s` and passed over stdin, so they never land in
# shell history or in a process's argument list.
#
# Safe to re-run: press Enter to keep the value already stored, or type "-" to
# clear it. That lets you add the Q&A bot app credentials later without
# re-entering the DevOps Agent HMAC secret (the console shows it only once).
# The group bot webhook URL defaults to .local/feishu-group-webhook.url if present.
set -euo pipefail
REGION="${AWS_REGION:-us-east-1}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
GROUP_URL_FILE="$DIR/.local/feishu-group-webhook.url"

ask() {  # ask <var> <prompt> [secret]
  local __v
  if [[ "${3:-}" == "secret" ]]; then read -r -s -p "$2: " __v; echo; else read -r -p "$2: " __v; fi
  printf -v "$1" '%s' "$__v"
}

echo "Enter = keep the stored value, '-' = clear it."
echo "--- DevOps Agent generic webhook (Agent Space > Capabilities > Webhook > Add webhook, HMAC)"
ask WEBHOOK_URL "Webhook URL"
ask WEBHOOK_SECRET "HMAC signing secret" secret

echo "--- Feishu group custom bot (optional: not needed when deployed with FEISHU_CHAT_ID; Enter to skip)"
if [[ -s "$GROUP_URL_FILE" ]]; then
  ask GROUP_URL "Group bot webhook URL (Enter = use .local/feishu-group-webhook.url)"
  if [[ -z "$GROUP_URL" ]]; then GROUP_URL="$(tr -d '[:space:]' < "$GROUP_URL_FILE")"; fi
else
  ask GROUP_URL "Group bot webhook URL"
fi
ask GROUP_SECRET "Signature secret (安全设置 > 签名校验; '-' if verification is off)" secret

echo "--- Feishu enterprise app for the Q&A bot (Enter to skip or keep)"
ask APP_ID "App ID (凭证与基础信息)"
ask APP_SECRET "App Secret (凭证与基础信息)" secret
ask VERIFY_TOKEN "Verification Token (事件与回调 > 加密策略)" secret

read -r -d '' PY <<'EOF' || true
import json, os, subprocess, sys, tempfile
region = sys.argv[1]
vals = (sys.stdin.read().split("\n") + [""] * 7)[:7]

def aws(*args, **kwargs):
    return subprocess.run(["aws", *args, "--region", region], check=True, **kwargs)

def current(name):
    out = aws("secretsmanager", "get-secret-value", "--secret-id", name, "--query", "SecretString",
              "--output", "text", stdout=subprocess.PIPE, text=True).stdout
    try:
        value = json.loads(out)
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}

def put(name, updates):
    value = current(name)
    changed = []
    for key, new in updates.items():
        if new == "":
            continue                      # Enter: keep the stored value
        value[key] = "" if new == "-" else new
        changed.append(key)
    if changed:
        # file:// keeps the value out of argv; NamedTemporaryFile is created 0600
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
            json.dump(value, f)
            path = f.name
        try:
            aws("secretsmanager", "put-secret-value", "--secret-id", name,
                "--secret-string", f"file://{path}", stdout=subprocess.DEVNULL)
        finally:
            os.remove(path)
    empty = [k for k in updates if not value.get(k)]
    # key names only, never values
    print(f"{name}: " + (f"updated {', '.join(changed)}" if changed else "unchanged")
          + (f"; empty: {', '.join(empty)}" if empty else ""))

url, secret, group_url, group_secret, app_id, app_secret, token = vals
put("aiops-demo/devops-agent-webhook", {"url": url, "secret": secret})
put("aiops-demo/feishu", {"group_webhook_url": group_url, "group_webhook_secret": group_secret,
                          "app_id": app_id, "app_secret": app_secret, "verification_token": token})
EOF

printf '%s\n' "$WEBHOOK_URL" "$WEBHOOK_SECRET" "$GROUP_URL" "$GROUP_SECRET" "$APP_ID" "$APP_SECRET" "$VERIFY_TOKEN" \
  | python3 -c "$PY" "$REGION"
