#!/usr/bin/env bash
# Smoke-test both demo MCP servers:
#  - nexus-mock: invoke the Lambda directly with function-URL-shaped events
#  - es-mock:    real HTTPS MCP calls to AgentCore Runtime with a Cognito OAuth token
# (initialize -> tools/list -> tools/call)
set -euo pipefail
REGION="${AWS_REGION:-us-east-1}"
FN="aiops-demo-nexus-mcp"
OUT="$(mktemp)"; trap 'rm -f "$OUT"' EXIT

call() {
  local body="$1"
  local event
  event=$(python3 -c 'import json,sys; print(json.dumps({"requestContext":{"http":{"method":"POST"}},"body":sys.argv[1],"isBase64Encoded":False}))' "$body")
  aws lambda invoke --region "$REGION" --function-name "$FN" --cli-binary-format raw-in-base64-out \
    --payload "$event" "$OUT" >/dev/null
  python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); print("HTTP", r["statusCode"]); print(json.dumps(json.loads(r["body"]) if r.get("body") else None, ensure_ascii=False, indent=2)[:1500])' "$OUT"
}

echo "== initialize";  call '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}'
echo "== tools/list";  call '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
echo "== tools/call";  call '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"nexus_get_release","arguments":{"component":"order-service","version":"1.4.0"}}}'

# ---- es-mock on AgentCore Runtime: the same path DevOps Agent takes (OAuth token -> HTTPS MCP)
STACK="${STACK_NAME:-aiops-demo}"
out() { aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
ARN="$(out EsMcpRuntimeArn)"
if [[ -z "$ARN" || "$ARN" == "None" ]]; then
  echo "== es-mock not deployed (ENABLE_AGENTCORE_MCP=false); skipped"; exit 0
fi
ENDPOINT=$(python3 -c 'import sys, urllib.parse as u; print("https://bedrock-agentcore.%s.amazonaws.com/runtimes/%s/invocations?qualifier=DEFAULT" % (sys.argv[2], u.quote(sys.argv[1], safe="")))' "$ARN" "$REGION")
echo; echo "######## es-mock (AgentCore Runtime) ${ENDPOINT}"
aws cognito-idp describe-user-pool-client --region "$REGION" --user-pool-id "$(out McpUserPoolId)" \
  --client-id "$(out McpOAuthClientId)" --query UserPoolClient.ClientSecret --output text \
| python3 "$(dirname "$0")/test_es_mcp.py" --endpoint "$ENDPOINT" --token-url "$(out McpOAuthTokenUrl)" \
    --client-id "$(out McpOAuthClientId)" --scope "$(out McpOAuthScope)"
