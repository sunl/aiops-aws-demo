#!/usr/bin/env bash
# Simulate a bad release: "deploy" order-service 1.4.0, whose config points to a
# DynamoDB table (aiops-demo-orders-v2) that the migration job never created.
# Every request then fails with ResourceNotFoundException and the alarm fires
# within ~1-2 minutes.
set -euo pipefail
REGION="${AWS_REGION:-us-east-1}"
FN="aiops-demo-order-service"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo "==> [${NOW}] releasing order-service 1.4.0"
aws lambda update-function-configuration --region "$REGION" --function-name "$FN" \
  --description "order-service 1.4.0 (released ${NOW})" \
  --environment '{"Variables":{"ORDERS_TABLE":"aiops-demo-orders-v2","APP_VERSION":"1.4.0"}}' \
  --query '{version:Environment.Variables.APP_VERSION,table:Environment.Variables.ORDERS_TABLE}' --output table
aws ssm put-parameter --region "$REGION" --overwrite --name /aiops-demo/release/order-service \
  --value "{\"deployed_version\":\"1.4.0\",\"deployed_at\":\"${NOW}\",\"deployed_by\":\"release-bot\"}" >/dev/null
echo "==> done. Watch the Feishu group: alarm card (~2 min) -> investigation started -> RCA card (~5-10 min)."
