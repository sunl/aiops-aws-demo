"""Mock Elasticsearch log-search tools, backed by CloudWatch Logs Insights.

Stands in for the customer's Elasticsearch MCP server: tool names and the
response shape follow Elasticsearch `_search` conventions (hits, aggregations),
while the data comes from the demo's CloudWatch log group so an investigation
sees the live order-service events.

Pure functions with an injected Logs client; main.py wires them into FastMCP.
"""
import json
import re
import time
from datetime import datetime, timezone

LEVELS = ("ERROR", "WARN", "INFO")
MAX_MINUTES = 1440
MAX_SIZE = 100
QUERY_TIMEOUT_SECONDS = 25
# User text goes into a Logs Insights query, so only a conservative character
# set is accepted (letters incl. CJK, digits, space and - _ . : / @).
_SAFE_TEXT = re.compile(r"^[\w\s\-.:/@]{1,100}$")


class ToolError(ValueError):
    """Invalid input or a failed query; surfaced to the MCP client as isError."""


def parse_log_groups(raw: str) -> dict:
    """LOG_GROUPS env var: JSON object mapping service name -> log group name."""
    try:
        mapping = json.loads(raw or "{}")
    except ValueError as exc:
        raise ValueError(f"LOG_GROUPS is not valid JSON: {exc}") from exc
    if not isinstance(mapping, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
        raise ValueError("LOG_GROUPS must be a JSON object of service name -> log group name")
    return mapping


def _log_group(groups: dict, service: str) -> str:
    if service not in groups:
        known = ", ".join(f"logs-{s}-*" for s in groups) or "none"
        raise ToolError(f"index logs-{service}-* not found; available indices: {known}")
    return groups[service]


def _window(minutes: int, now: float) -> tuple:
    if not isinstance(minutes, int) or not 1 <= minutes <= MAX_MINUTES:
        raise ToolError(f"minutes must be an integer between 1 and {MAX_MINUTES}")
    end = int(now)
    return end - minutes * 60, end


def _iso(ts: str) -> str:
    """Logs Insights timestamp ('2026-10-14 10:21:00.123') -> ISO 8601 UTC."""
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc).isoformat(
            timespec="milliseconds").replace("+00:00", "Z")
    except (TypeError, ValueError):
        return ts


def build_search_query(level: str, text: str, size: int) -> str:
    if level and level not in LEVELS:
        raise ToolError(f"level must be one of {', '.join(LEVELS)} (or empty for all levels)")
    if text and not _SAFE_TEXT.match(text):
        raise ToolError("query may contain only letters, digits, spaces and - _ . : / @ (max 100 characters)")
    if not isinstance(size, int) or not 1 <= size <= MAX_SIZE:
        raise ToolError(f"size must be an integer between 1 and {MAX_SIZE}")
    parts = ["fields @timestamp, @message", "filter ispresent(level)"]
    if level:
        parts.append(f'filter level = "{level}"')
    if text:
        parts.append(f'filter @message like "{text}"')
    parts += ["sort @timestamp desc", f"limit {size}"]
    return " | ".join(parts)


ERROR_BREAKDOWN_QUERY = (
    'filter level = "ERROR" | stats count(*) as doc_count, min(@timestamp) as first_seen, '
    "max(@timestamp) as last_seen by error_type, version, table | sort doc_count desc | limit 20"
)
ERROR_TIMELINE_QUERY = 'filter level = "ERROR" | stats count(*) as doc_count by bin(1m) as minute | sort minute asc'


def _rows(results: list) -> list:
    return [{f["field"]: f.get("value") for f in row if not f["field"].startswith("@ptr")} for row in results]


def run_queries(client, log_group: str, start: int, end: int, queries: list, sleep=time.sleep, clock=time.monotonic):
    """Start the queries together, poll until all complete; returns a list of row lists."""
    ids = [client.start_query(logGroupName=log_group, startTime=start, endTime=end, queryString=q)["queryId"]
           for q in queries]
    results = [None] * len(ids)
    deadline = clock() + QUERY_TIMEOUT_SECONDS
    while any(r is None for r in results):
        for i, qid in enumerate(ids):
            if results[i] is not None:
                continue
            resp = client.get_query_results(queryId=qid)
            status = resp.get("status")
            if status == "Complete":
                results[i] = _rows(resp.get("results", []))
            elif status in ("Failed", "Cancelled", "Timeout", "Unknown"):
                raise ToolError(f"log query {status.lower()}")
        if any(r is None for r in results):
            if clock() > deadline:
                for i, qid in enumerate(ids):
                    if results[i] is None:
                        try:
                            client.stop_query(queryId=qid)
                        except Exception:  # best effort
                            pass
                raise ToolError(f"log query did not finish within {QUERY_TIMEOUT_SECONDS}s; narrow the time window")
            sleep(0.5)
    return results


def _source(message: str, timestamp: str) -> dict:
    try:
        doc = json.loads(message)
        if not isinstance(doc, dict):
            raise ValueError
    except (TypeError, ValueError):
        doc = {"message": (message or "").rstrip()}
    return {"@timestamp": _iso(timestamp), **doc}


def search_logs(client, groups: dict, service: str, query: str = "", level: str = "", minutes: int = 30,
                size: int = 20, now: float = None, sleep=time.sleep) -> dict:
    """Most recent structured log documents of a service, newest first."""
    log_group = _log_group(groups, service)
    insights = build_search_query(level, query, size)
    start, end = _window(minutes, time.time() if now is None else now)
    t0 = time.monotonic()
    (rows,) = run_queries(client, log_group, start, end, [insights], sleep=sleep)
    hits = []
    for row in rows:
        src = _source(row.get("@message", ""), row.get("@timestamp"))
        day = src["@timestamp"][:10].replace("-", ".")
        hits.append({"_index": f"logs-{service}-{day}", "_source": src})
    return {
        "took": int((time.monotonic() - t0) * 1000),
        "timed_out": False,
        "query": {"service": service, "level": level or "any", "text": query, "last_minutes": minutes},
        "hits": {"total": {"value": len(hits), "relation": "gte" if len(hits) == size else "eq"}, "hits": hits},
    }


def error_summary(client, groups: dict, service: str, minutes: int = 60, now: float = None,
                  sleep=time.sleep) -> dict:
    """ERROR documents aggregated by error type / version / table, plus a per-minute timeline."""
    log_group = _log_group(groups, service)
    start, end = _window(minutes, time.time() if now is None else now)
    t0 = time.monotonic()
    breakdown, timeline = run_queries(client, log_group, start, end,
                                      [ERROR_BREAKDOWN_QUERY, ERROR_TIMELINE_QUERY], sleep=sleep)
    buckets = [{
        "key": row.get("error_type") or "unknown",
        "doc_count": int(row.get("doc_count") or 0),
        "version": row.get("version"),
        "table": row.get("table"),
        "first_seen": _iso(row.get("first_seen")),
        "last_seen": _iso(row.get("last_seen")),
    } for row in breakdown]
    per_minute = [{"key_as_string": _iso(row.get("minute")), "doc_count": int(row.get("doc_count") or 0)}
                  for row in timeline]
    return {
        "took": int((time.monotonic() - t0) * 1000),
        "timed_out": False,
        "query": {"service": service, "level": "ERROR", "last_minutes": minutes},
        "hits": {"total": {"value": sum(b["doc_count"] for b in buckets), "relation": "eq"}},
        "aggregations": {"by_error": {"buckets": buckets}, "errors_per_minute": {"buckets": per_minute}},
    }
