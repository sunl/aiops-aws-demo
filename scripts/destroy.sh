#!/usr/bin/env bash
# Remove the demo. The MCP registration and the webhook live in DevOps Agent and
# are removed separately (console, or the commands printed below).
set -euo pipefail
REGION="${AWS_REGION:-us-east-1}"
STACK="${STACK_NAME:-aiops-demo}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="aiops-demo-artifacts-${ACCOUNT}-${REGION}"

read -r -p "Delete stack ${STACK} and bucket ${BUCKET} in ${REGION}? Type 'yes': " ok
[[ "$ok" == "yes" ]] || { echo "aborted"; exit 1; }

# AgentCore Runtime writes its own log group, which the stack does not own.
RUNTIME_ID="$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='EsMcpRuntimeId'].OutputValue" --output text 2>/dev/null || true)"

aws cloudformation delete-stack --region "$REGION" --stack-name "$STACK"
aws cloudformation wait stack-delete-complete --region "$REGION" --stack-name "$STACK"
if [[ -n "$RUNTIME_ID" && "$RUNTIME_ID" != "None" ]]; then
  for lg in $(aws logs describe-log-groups --region "$REGION" \
      --log-group-name-prefix "/aws/bedrock-agentcore/runtimes/${RUNTIME_ID}" --query 'logGroups[].logGroupName' --output text); do
    aws logs delete-log-group --region "$REGION" --log-group-name "$lg" && echo "==> deleted log group ${lg}"
  done
fi
aws s3 rm "s3://${BUCKET}" --recursive --region "$REGION" >/dev/null || true
aws s3api delete-bucket --bucket "$BUCKET" --region "$REGION" || true
echo "==> stack deleted (secrets are scheduled for deletion by CloudFormation)."
cat <<EOF
Remaining DevOps Agent clean-up (only if you created them for the demo):
  aws devops-agent list-services --region ${REGION}            # find the nexus-mock and es-mock serviceIds
  aws devops-agent disassociate-service --region ${REGION} --agent-space-id <id> --association-id <assoc>
  aws devops-agent deregister-service --region ${REGION} --service-id <serviceId>
  Console: Agent Space > Capabilities > Webhook -> remove the generic webhook
EOF
