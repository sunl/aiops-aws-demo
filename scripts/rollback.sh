#!/usr/bin/env bash
# Roll order-service back to 1.3.2 (the stable release). The alarm returns to OK
# after a minute of clean traffic and a recovery card is posted to Feishu.
set -euo pipefail
REGION="${AWS_REGION:-us-east-1}"
FN="aiops-demo-order-service"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo "==> [${NOW}] rolling back order-service to 1.3.2"
aws lambda update-function-configuration --region "$REGION" --function-name "$FN" \
  --description "order-service 1.3.2 (rolled back ${NOW})" \
  --environment '{"Variables":{"ORDERS_TABLE":"aiops-demo-orders","APP_VERSION":"1.3.2"}}' \
  --query '{version:Environment.Variables.APP_VERSION,table:Environment.Variables.ORDERS_TABLE}' --output table
aws ssm put-parameter --region "$REGION" --overwrite --name /aiops-demo/release/order-service \
  --value "{\"deployed_version\":\"1.3.2\",\"deployed_at\":\"${NOW}\",\"deployed_by\":\"oncall\"}" >/dev/null
echo "==> done."
