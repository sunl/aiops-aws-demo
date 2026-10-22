#!/usr/bin/env bash
# Register the demo MCP servers with DevOps Agent and allow their read-only tools
# in the demo Agent Space.
#   nexus  mock Nexus MCP     Lambda function URL (AWS_IAM)   SigV4, service "lambda"
#   es     mock ES MCP        AgentCore Runtime (JWT)         OAuth client credentials (Cognito)
# Usage: scripts/register-mcp.sh [nexus|es|all]   (default all)
# Each run creates a new registration; deregister the old one first when re-running.
set -euo pipefail
REGION="${AWS_REGION:-us-east-1}"
STACK="${STACK_NAME:-aiops-demo}"
TARGET="${1:-all}"
: "${AGENT_SPACE_ID:?set AGENT_SPACE_ID}"
ES_INPUT=""
trap 'if [[ -n "$ES_INPUT" ]]; then rm -f "$ES_INPUT"; fi' EXIT

out() { aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }

associate() {  # associate <serviceId> <config-key> <tool>...
  local sid="$1" key="$2"; shift 2
  local cfg
  cfg=$(python3 -c 'import json,sys; print(json.dumps({sys.argv[1]: {"tools": sys.argv[2:]}}))' "$key" "$@")
  aws devops-agent associate-service --region "$REGION" --agent-space-id "$AGENT_SPACE_ID" \
    --service-id "$sid" --configuration "$cfg" >/dev/null
  echo "    associated with Agent Space ${AGENT_SPACE_ID}: $*"
}

register_nexus() {
  local url role details sid
  url="$(out NexusMcpUrl)"; role="$(out McpInvokeRoleArn)"
  echo "==> [nexus-mock] Lambda function URL ${url}"
  details=$(python3 -c '
import json, sys
url, role, region = sys.argv[1:4]
print(json.dumps({"mcpserversigv4": {
    "name": "nexus-mock", "endpoint": url,
    "description": "Mock Nexus artifact repository (release versions and notes)",
    "authorizationConfig": {"region": region, "service": "lambda", "roleArn": role}}}))' "$url" "$role" "$REGION")
  echo "    registering (DevOps Agent validates the endpoint and discovers tools)"
  sid="$(aws devops-agent register-service --region "$REGION" --service mcpserversigv4 \
    --service-details "$details" --query serviceId --output text)"
  echo "    serviceId=${sid}"
  associate "$sid" mcpserversigv4 nexus_list_versions nexus_get_release
}

register_es() {
  local arn pool client token_url scope endpoint input sid
  arn="$(out EsMcpRuntimeArn)"
  if [[ -z "$arn" || "$arn" == "None" ]]; then
    echo "==> [es-mock] not deployed (ENABLE_AGENTCORE_MCP=false); skipping"; return
  fi
  pool="$(out McpUserPoolId)"; client="$(out McpOAuthClientId)"
  token_url="$(out McpOAuthTokenUrl)"; scope="$(out McpOAuthScope)"
  endpoint=$(python3 -c 'import sys, urllib.parse as u; print("https://bedrock-agentcore.%s.amazonaws.com/runtimes/%s/invocations?qualifier=DEFAULT" % (sys.argv[2], u.quote(sys.argv[1], safe="")))' "$arn" "$REGION")
  echo "==> [es-mock] AgentCore Runtime endpoint ${endpoint}"

  # The Cognito client secret travels stdin -> 0600 temp file -> --cli-input-json,
  # so it never appears in argv, shell history or this script's output.
  input="$(umask 077; mktemp)"
  ES_INPUT="$input"
  aws cognito-idp describe-user-pool-client --region "$REGION" --user-pool-id "$pool" --client-id "$client" \
    --query UserPoolClient.ClientSecret --output text \
  | python3 -c '
import json, sys
path, endpoint, client_id, token_url, scope = sys.argv[1:6]
secret = sys.stdin.read().strip()
doc = {"service": "mcpserver", "serviceDetails": {"mcpserver": {
    "name": "es-mock", "endpoint": endpoint,
    "description": "Mock Elasticsearch log search (AgentCore Runtime)",
    "authorizationConfig": {"oAuthClientCredentials": {
        "clientName": "aiops-demo-cognito", "clientId": client_id, "clientSecret": secret,
        "exchangeUrl": token_url, "scopes": [scope]}}}}}
with open(path, "w") as f:
    json.dump(doc, f)' "$input" "$endpoint" "$client" "$token_url" "$scope"
  echo "    registering (DevOps Agent fetches a token from Cognito, then discovers tools)"
  sid="$(aws devops-agent register-service --region "$REGION" --cli-input-json "file://${input}" \
    --query serviceId --output text)"
  rm -f "$input"
  echo "    serviceId=${sid}"
  associate "$sid" mcpserver es_search_logs es_error_summary
}

case "$TARGET" in
  nexus) register_nexus ;;
  es) register_es ;;
  all) register_nexus; register_es ;;
  *) echo "usage: $0 [nexus|es|all]" >&2; exit 2 ;;
esac
echo "==> done. Console: Agent Space > Capabilities > MCP Servers should list the registered servers."
