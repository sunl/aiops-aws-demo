"""Secrets Manager access with a small in-process cache.

boto3 is imported lazily so the pure helpers in this package can be unit tested
without it.
"""
import json
import time

_CACHE = {}
_TTL_SECONDS = 300


def get_secret_json(secret_id: str) -> dict:
    """Return the JSON value of a Secrets Manager secret, cached for 5 minutes."""
    now = time.time()
    hit = _CACHE.get(secret_id)
    if hit and now - hit[0] < _TTL_SECONDS:
        return hit[1]
    import boto3  # lazy: available in the Lambda runtime and the common layer

    raw = boto3.client("secretsmanager").get_secret_value(SecretId=secret_id)["SecretString"]
    value = json.loads(raw)
    _CACHE[secret_id] = (now, value)
    return value
