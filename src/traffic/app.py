"""Synthetic traffic: invokes order-service a few times per minute."""
import json
import os
import random

import boto3

TARGET = os.environ["TARGET_FUNCTION"]
REQUESTS = int(os.environ.get("REQUESTS_PER_RUN", "20"))
_lambda = boto3.client("lambda")


def _invoke(payload: dict) -> dict:
    resp = _lambda.invoke(FunctionName=TARGET, Payload=json.dumps(payload).encode("utf-8"))
    body = json.loads(resp["Payload"].read() or b"{}")
    return {"failed": "FunctionError" in resp, "body": body}


def handler(event, context):
    ok = failed = 0
    for _ in range(REQUESTS):
        result = _invoke({"action": "create",
                          "body": {"sku": random.choice(["SKU-1001", "SKU-2002", "SKU-3003"]),
                                   "quantity": random.randint(1, 3)}})
        if result["failed"]:
            failed += 1
            continue
        ok += 1
        order_id = result["body"].get("order_id")
        if order_id and _invoke({"action": "get", "order_id": order_id})["failed"]:
            failed += 1
    summary = {"requests": REQUESTS, "ok": ok, "failed": failed}
    print(json.dumps(summary))
    return summary
