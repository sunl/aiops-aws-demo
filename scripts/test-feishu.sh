#!/usr/bin/env bash
# Send a test card to the demo group with the values stored in aiops-demo/feishu,
# the same way the Lambdas do: as the app bot when FEISHU_CHAT_ID is set, otherwise
# through the custom bot webhook. Never prints the stored values.
set -euo pipefail
REGION="${AWS_REGION:-us-east-1}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -z "${FEISHU_CHAT_ID:-}" ]]; then  # same destination the deployed Lambdas use
  FEISHU_CHAT_ID="$(aws cloudformation describe-stacks --region "$REGION" --stack-name "${STACK_NAME:-aiops-demo}" \
    --query "Stacks[0].Parameters[?ParameterKey=='FeishuChatId'].ParameterValue | [0]" --output text 2>/dev/null || true)"
  [[ "$FEISHU_CHAT_ID" == "None" ]] && FEISHU_CHAT_ID=""
fi
export FEISHU_CHAT_ID

aws secretsmanager get-secret-value --region "$REGION" --secret-id aiops-demo/feishu \
    --query SecretString --output text \
  | PYTHONPATH="$DIR/src/common" python3 -c '
import json, os, sys
from demo_common import feishu

cfg = json.load(sys.stdin)
card = feishu.build_card("🧪 AIOps Demo 连通性测试", "blue",
                         ["群机器人配置正确。部署完成后，告警、调查、根因、恢复卡片都会推送到本群。"],
                         note="aiops-demo · scripts/test-feishu.sh")
chat_id = os.environ.get("FEISHU_CHAT_ID", "")
try:
    via = feishu.post_to_group(cfg, card, os.environ.get("FEISHU_BASE_URL", "https://open.feishu.cn"), chat_id)
except feishu.FeishuError as exc:
    sys.exit(f"FAILED: {exc}  (19021 = signing secret wrong; 19024 = 自定义关键词 is on; "
             "19007 = custom bot disabled or removed; 230002 = app bot not in the group)")
if via == "dropped":
    sys.exit("group_webhook_url is empty and FEISHU_CHAT_ID is not set: run ./scripts/setup-secrets.sh first")
print(f"OK: test card sent via {via}")
'
