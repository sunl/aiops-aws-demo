#!/usr/bin/env bash
# Build and deploy the AIOps demo stack.
#
# Required:  AGENT_SPACE_ID=<DevOps Agent Agent Space ID>
# Optional:  ENABLE_CHAT_BOT=true|false (default: keep the deployed value, else false)
#            FEISHU_BASE_URL=https://open.feishu.cn | https://open.larksuite.com
#            FEISHU_CHAT_ID=oc_xxx: post alert/RCA cards as the app bot (no custom bot needed).
#              Default: keep the deployed value; pass FEISHU_CHAT_ID= (empty) to clear it.
#            FEISHU_WS_DESIRED_COUNT=1|0 (default 1): Fargate long-connection client on/off
#            FEISHU_WS_VPC / FEISHU_WS_SUBNETS: network for that task (default VPC by default)
#            TRAFFIC_ENABLED=true|false (default true)
#            ENABLE_AGENTCORE_MCP=true|false (default true): mock Elasticsearch MCP on AgentCore Runtime
#            AWS_REGION (default us-east-1), STACK_NAME (default aiops-demo)
set -euo pipefail

cd "$(dirname "$0")"
REGION="${AWS_REGION:-us-east-1}"
STACK="${STACK_NAME:-aiops-demo}"
BOTO3_VERSION="1.43.113"   # pinned; includes the devops-agent client
: "${AGENT_SPACE_ID:?set AGENT_SPACE_ID to your DevOps Agent Agent Space ID}"

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="aiops-demo-artifacts-${ACCOUNT}-${REGION}"
echo "==> account ${ACCOUNT}, region ${REGION}, stack ${STACK}"

# Current value of a stack parameter (empty when the stack or parameter does not exist), so a
# plain re-run keeps the Q&A bot and the app-bot delivery instead of silently resetting them.
deployed() {
  local v
  v="$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
    --query "Stacks[0].Parameters[?ParameterKey=='$1'].ParameterValue | [0]" --output text 2>/dev/null || true)"
  [[ "$v" == "None" ]] && v=""
  printf '%s' "$v"
}
ENABLE_CHAT_BOT="${ENABLE_CHAT_BOT-$(deployed EnableChatBot)}"
FEISHU_CHAT_ID="${FEISHU_CHAT_ID-$(deployed FeishuChatId)}"
CARDS_VIA="custom bot webhook"
[[ -n "$FEISHU_CHAT_ID" ]] && CARDS_VIA="app bot -> ${FEISHU_CHAT_ID}"
echo "==> chat bot: ${ENABLE_CHAT_BOT:-false}, cards via: ${CARDS_VIA}"

APP_URL="$(aws devops-agent get-operator-app --region "$REGION" --agent-space-id "$AGENT_SPACE_ID" \
  --query operatorAppUrl --output text 2>/dev/null || true)"
[[ "$APP_URL" == "None" ]] && APP_URL=""
echo "==> operator web app: ${APP_URL:-<not enabled>}"

echo "==> building common layer (boto3==${BOTO3_VERSION} + demo_common)"
PY="$(command -v python3.12 || command -v python3)"
rm -rf build && mkdir -p build/layer/python
"$PY" -m pip install --quiet --no-cache-dir --target build/layer/python "boto3==${BOTO3_VERSION}"
cp -r src/common/demo_common build/layer/python/
find build/layer src -name '__pycache__' -type d -prune -exec rm -rf {} +

if ! aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  echo "==> creating artifact bucket ${BUCKET}"
  if [[ "$REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration "LocationConstraint=${REGION}" >/dev/null
  fi
  aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
fi

echo "==> packaging"
aws cloudformation package --region "$REGION" --template-file template.yaml \
  --s3-bucket "$BUCKET" --s3-prefix "$STACK" --output-template-file build/packaged.yaml >/dev/null

ES_MCP_KEY=""
if [[ "${ENABLE_AGENTCORE_MCP:-true}" == "true" ]]; then
  # AgentCore Runtime direct code deployment: linux/arm64 wheels + main.py at the zip root.
  # The key is a hash of the sources, so an unchanged server does not create a new runtime version.
  HASH="$(cat src/es_mcp/requirements.txt src/es_mcp/main.py src/es_mcp/es_tools.py | sha256sum | cut -c1-16)"
  ES_MCP_KEY="${STACK}/es-mcp/${HASH}.zip"
  if aws s3api head-object --bucket "$BUCKET" --key "$ES_MCP_KEY" >/dev/null 2>&1; then
    echo "==> es-mock MCP package ${HASH} already uploaded"
  else
    echo "==> building es-mock MCP package for AgentCore Runtime (linux/arm64, Python 3.12)"
    mkdir -p build/es_mcp
    "$PY" -m pip install --quiet --no-cache-dir --no-deps --target build/es_mcp --only-binary=:all: \
      --implementation cp --python-version 3.12 --platform manylinux2014_aarch64 -r src/es_mcp/requirements.txt
    cp src/es_mcp/main.py src/es_mcp/es_tools.py build/es_mcp/
    find build/es_mcp -name '__pycache__' -type d -prune -exec rm -rf {} +
    find build/es_mcp -type d -exec chmod 755 {} +
    find build/es_mcp -type f -exec chmod 644 {} +
    (cd build/es_mcp && zip -qr -X ../es_mcp.zip .)
    aws s3 cp --region "$REGION" --only-show-errors build/es_mcp.zip "s3://${BUCKET}/${ES_MCP_KEY}"
  fi
fi

FEISHU_WS_KEY=""; FEISHU_WS_VPC="${FEISHU_WS_VPC:-}"; FEISHU_WS_SUBNETS="${FEISHU_WS_SUBNETS:-}"
if [[ "${ENABLE_CHAT_BOT:-false}" == "true" ]]; then
  # Feishu long-connection client on Fargate (linux/arm64): code + pinned wheels in one zip.
  HASH="$(cat src/feishu_ws/requirements.txt src/feishu_ws/client.py | sha256sum | cut -c1-16)"
  FEISHU_WS_KEY="${STACK}/feishu-ws/${HASH}.zip"
  if aws s3api head-object --bucket "$BUCKET" --key "$FEISHU_WS_KEY" >/dev/null 2>&1; then
    echo "==> feishu-ws bundle ${HASH} already uploaded"
  else
    echo "==> building feishu-ws bundle for Fargate (linux/arm64, Python 3.12)"
    mkdir -p build/feishu_ws
    "$PY" -m pip install --quiet --no-cache-dir --no-deps --target build/feishu_ws --only-binary=:all: \
      --implementation cp --python-version 3.12 --platform manylinux_2_28_aarch64 --platform manylinux2014_aarch64 \
      -r src/feishu_ws/requirements.txt
    cp src/feishu_ws/client.py build/feishu_ws/
    find build/feishu_ws -name '__pycache__' -type d -prune -exec rm -rf {} +
    # lark-oapi imports thousands of modules; ship unchecked-hash .pyc so a cold start skips compiling
    # (valid because the bundle never changes after upload; same Python minor version as the image)
    "$PY" -m compileall -q -j 0 --invalidation-mode unchecked-hash build/feishu_ws >/dev/null
    find build/feishu_ws -type d -exec chmod 755 {} +
    find build/feishu_ws -type f -exec chmod 644 {} +
    (cd build/feishu_ws && zip -qr -X ../feishu_ws.zip .)
    aws s3 cp --region "$REGION" --only-show-errors build/feishu_ws.zip "s3://${BUCKET}/${FEISHU_WS_KEY}"
  fi
  # Network: the default VPC's default (public) subnets, outbound only. use1-az3 is skipped
  # because Fargate is not offered there. Override with FEISHU_WS_VPC / FEISHU_WS_SUBNETS.
  if [[ -z "$FEISHU_WS_VPC" ]]; then
    FEISHU_WS_VPC="$(aws ec2 describe-vpcs --region "$REGION" --filters Name=is-default,Values=true \
      --query 'Vpcs[0].VpcId' --output text)"
    [[ "$FEISHU_WS_VPC" == "None" ]] && FEISHU_WS_VPC=""
  fi
  : "${FEISHU_WS_VPC:?no default VPC: set FEISHU_WS_VPC and FEISHU_WS_SUBNETS (subnets with an internet route)}"
  if [[ -z "$FEISHU_WS_SUBNETS" ]]; then
    FEISHU_WS_SUBNETS="$(aws ec2 describe-subnets --region "$REGION" \
      --filters Name=vpc-id,Values="$FEISHU_WS_VPC" Name=default-for-az,Values=true \
      --query "Subnets[?AvailabilityZoneId!='use1-az3'].SubnetId" --output text | tr -s '[:space:]' ',' | sed 's/,$//')"
  fi
  : "${FEISHU_WS_SUBNETS:?no default subnets found in ${FEISHU_WS_VPC}: set FEISHU_WS_SUBNETS}"
  echo "==> feishu-ws network: ${FEISHU_WS_VPC} (${FEISHU_WS_SUBNETS})"
fi

echo "==> deploying"
aws cloudformation deploy --region "$REGION" --stack-name "$STACK" \
  --template-file build/packaged.yaml --capabilities CAPABILITY_NAMED_IAM --no-fail-on-empty-changeset \
  --parameter-overrides \
    "AgentSpaceId=${AGENT_SPACE_ID}" \
    "OperatorAppUrl=${APP_URL}" \
    "EnableChatBot=${ENABLE_CHAT_BOT:-false}" \
    "FeishuBaseUrl=${FEISHU_BASE_URL:-https://open.feishu.cn}" \
    "FeishuChatId=${FEISHU_CHAT_ID:-}" \
    "TrafficEnabled=${TRAFFIC_ENABLED:-true}" \
    "EnableAgentCoreMcp=${ENABLE_AGENTCORE_MCP:-true}" \
    "ArtifactBucket=${BUCKET}" \
    "EsMcpCodeKey=${ES_MCP_KEY}" \
    "FeishuWsCodeKey=${FEISHU_WS_KEY}" \
    "FeishuWsVpcId=${FEISHU_WS_VPC}" \
    "FeishuWsSubnetIds=${FEISHU_WS_SUBNETS}" \
    "FeishuWsDesiredCount=${FEISHU_WS_DESIRED_COUNT:-1}"

aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
  --query 'Stacks[0].Outputs[].[OutputKey,OutputValue]' --output table
echo "==> next: scripts/setup-secrets.sh, then scripts/register-mcp.sh"
if [[ "${ENABLE_CHAT_BOT:-false}" == "true" ]]; then
  echo "==> Q&A bot: Feishu long-connection client runs on ECS Fargate (service aiops-demo-feishu-ws);"
  echo "    logs: aws logs tail /ecs/aiops-demo-feishu-ws --follow --region ${REGION}"
fi
