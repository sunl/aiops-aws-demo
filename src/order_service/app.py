"""Demo workload: order-service.

Reads/writes orders in DynamoDB. The table name comes from ORDERS_TABLE, which
the fault-injection script changes to simulate a bad release (v1.4.0 points at
a table that was never created).
"""
import json
import os
import time
import uuid
from decimal import Decimal

import boto3

TABLE_NAME = os.environ["ORDERS_TABLE"]
APP_VERSION = os.environ.get("APP_VERSION", "unknown")
_table = boto3.resource("dynamodb").Table(TABLE_NAME)


def _log(level: str, msg: str, **fields):
    print(json.dumps({"level": level, "service": "order-service", "version": APP_VERSION,
                      "table": TABLE_NAME, "msg": msg, **fields}, ensure_ascii=False, default=str))


def create_order(body: dict) -> dict:
    order = {
        "order_id": str(uuid.uuid4()),
        "sku": body.get("sku", "SKU-DEMO"),
        "quantity": int(body.get("quantity", 1)),
        "amount": Decimal(str(body.get("amount", "99.00"))),
        "created_at": int(time.time()),
    }
    _table.put_item(Item=order)
    return order


def get_order(order_id: str):
    return _table.get_item(Key={"order_id": order_id}).get("Item")


def handler(event, context):
    """Event shape: {"action": "create"|"get", "body": {...}, "order_id": "..."}."""
    action = event.get("action", "create")
    try:
        if action == "create":
            order = create_order(event.get("body") or {})
            _log("INFO", "order created", order_id=order["order_id"])
            return {"statusCode": 201, "order_id": order["order_id"]}
        if action == "get":
            item = get_order(event["order_id"])
            return {"statusCode": 200 if item else 404, "found": bool(item)}
        return {"statusCode": 400, "error": f"unknown action {action}"}
    except Exception as exc:  # log with context, then fail the invocation so Errors metric counts it
        _log("ERROR", "order request failed", action=action,
             error_type=type(exc).__name__, error=str(exc))
        raise
