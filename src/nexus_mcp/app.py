"""Mock Nexus artifact-repository MCP server (stateless Streamable HTTP).

Stands in for the customer's Nexus MCP server so the demo can show how an
existing MCP server plugs into AWS DevOps Agent. Exposed through a Lambda
function URL with AWS_IAM auth; DevOps Agent signs requests with SigV4
(service "lambda").

Implements the JSON-RPC subset DevOps Agent needs: initialize, ping,
tools/list, tools/call, and notifications (answered with 202).
"""
import json
import os

SERVER_INFO = {"name": "nexus-mock", "version": "1.0.0"}
SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_VERSION = "2025-06-18"

# Static release metadata. 1.4.0 deployment facts are overlaid from SSM, which
# the fault-injection script updates, so timestamps match the live demo.
RELEASES = {
    "order-service": {
        "1.3.1": {
            "uploaded_at": "2026-09-20T03:12:00Z", "uploaded_by": "ci-bot", "commit": "a1b2c3d",
            "artifact": "registry.example.com/order-service:1.3.1",
            "notes": ["查询订单增加本地缓存，P99 延迟下降 30%"],
            "config": {"ORDERS_TABLE": "aiops-demo-orders"},
            "pre_deploy_steps": [],
        },
        "1.3.2": {
            "uploaded_at": "2026-10-05T07:40:00Z", "uploaded_by": "ci-bot", "commit": "9f8e7d6",
            "artifact": "registry.example.com/order-service:1.3.2",
            "notes": ["修复订单金额精度问题（Decimal 序列化）"],
            "config": {"ORDERS_TABLE": "aiops-demo-orders"},
            "pre_deploy_steps": [],
        },
        "1.4.0": {
            "uploaded_at": "2026-10-13T09:05:00Z", "uploaded_by": "release-bot", "commit": "4c5d6e7",
            "artifact": "registry.example.com/order-service:1.4.0",
            "notes": [
                "订单存储迁移到新表 aiops-demo-orders-v2（分区键调整，为分库分表做准备）",
                "配置项 ORDERS_TABLE 默认值改为 aiops-demo-orders-v2",
            ],
            "config": {"ORDERS_TABLE": "aiops-demo-orders-v2"},
            "pre_deploy_steps": [
                "1. 执行迁移任务 orders-v2-migration：创建表 aiops-demo-orders-v2 并回填历史数据",
                "2. 灰度 10% 流量观察 30 分钟",
                "3. 全量发布",
            ],
            "known_issues": ["迁移任务 orders-v2-migration 对应变更单 CHG-20261014-017 状态为“待审批”，尚未在生产执行"],
        },
    }
}

TOOLS = [
    {
        "name": "nexus_list_versions",
        "description": "List artifact versions of a component in the Nexus repository, with upload time, "
                       "uploader, commit and current deployment status. Use it to find which version was "
                       "deployed around an incident.",
        "inputSchema": {
            "type": "object",
            "properties": {"component": {"type": "string", "description": "Component name, e.g. order-service"}},
            "required": ["component"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "nexus_get_release",
        "description": "Get release details of one artifact version: release notes, configuration changes, "
                       "required pre-deployment steps and known issues.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "component": {"type": "string", "description": "Component name, e.g. order-service"},
                "version": {"type": "string", "description": "Version, e.g. 1.4.0"},
            },
            "required": ["component", "version"],
        },
        "annotations": {"readOnlyHint": True},
    },
]


def _deployment_state() -> dict:
    """Current deployment facts for order-service, from SSM (falls back to baseline)."""
    name = os.environ.get("RELEASE_PARAM")
    if not name:
        return {"deployed_version": "1.3.2"}
    try:
        import boto3

        return json.loads(boto3.client("ssm").get_parameter(Name=name)["Parameter"]["Value"])
    except Exception as exc:  # keep the MCP server answering even if SSM is unavailable
        print(json.dumps({"level": "WARN", "msg": "release state unavailable", "error": str(exc)}))
        return {"deployed_version": "1.3.2"}


def _component(name: str) -> dict:
    releases = RELEASES.get(name)
    if releases is None:
        raise KeyError(f"component '{name}' not found; known components: {', '.join(RELEASES)}")
    return releases


def tool_list_versions(args: dict, state: dict) -> dict:
    releases = _component(args.get("component", ""))
    deployed = state.get("deployed_version")
    versions = []
    for version, meta in releases.items():
        entry = {k: meta[k] for k in ("uploaded_at", "uploaded_by", "commit", "artifact")}
        entry["version"] = version
        entry["status"] = "deployed-production" if version == deployed else "available"
        if version == deployed and state.get("deployed_at"):
            entry["deployed_at"] = state["deployed_at"]
            entry["deployed_by"] = state.get("deployed_by")
        versions.append(entry)
    return {"component": args["component"], "deployed_version": deployed, "versions": versions}


def tool_get_release(args: dict, state: dict) -> dict:
    releases = _component(args.get("component", ""))
    version = args.get("version", "")
    if version not in releases:
        raise KeyError(f"version '{version}' not found; available: {', '.join(releases)}")
    detail = {"component": args["component"], "version": version, **releases[version]}
    detail["status"] = "deployed-production" if version == state.get("deployed_version") else "available"
    if detail["status"] == "deployed-production" and state.get("deployed_at"):
        detail["deployed_at"] = state["deployed_at"]
    return detail


TOOL_IMPLS = {"nexus_list_versions": tool_list_versions, "nexus_get_release": tool_get_release}


def _result(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle_rpc(msg: dict, state_loader=_deployment_state):
    """Handle one JSON-RPC message. Returns a response dict, or None for notifications."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
    method, req_id, params = msg["method"], msg.get("id"), msg.get("params") or {}
    if req_id is None:  # notification, e.g. notifications/initialized
        return None
    if method == "initialize":
        requested = params.get("protocolVersion")
        return _result(req_id, {
            "protocolVersion": requested if requested in SUPPORTED_VERSIONS else DEFAULT_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": "Read-only access to artifact versions and release notes in the Nexus repository.",
        })
    if method == "ping":
        return _result(req_id, {})
    if method == "tools/list":
        return _result(req_id, {"tools": TOOLS})
    if method == "tools/call":
        impl = TOOL_IMPLS.get(params.get("name"))
        if impl is None:
            return _error(req_id, -32602, f"Unknown tool: {params.get('name')}")
        try:
            data = impl(params.get("arguments") or {}, state_loader())
            return _result(req_id, {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}],
                                    "isError": False})
        except KeyError as exc:
            return _result(req_id, {"content": [{"type": "text", "text": str(exc).strip("'\"")}], "isError": True})
    return _error(req_id, -32601, f"Method not found: {method}")


def _http(status: int, body=None, headers=None):
    resp = {"statusCode": status, "headers": {"Content-Type": "application/json", **(headers or {})}}
    resp["body"] = json.dumps(body, ensure_ascii=False) if body is not None else ""
    return resp


def handler(event, context):
    method = (event.get("requestContext") or {}).get("http", {}).get("method", "POST")
    if method != "POST":  # stateless server: no SSE stream, no sessions
        return _http(405, {"error": "Method Not Allowed"}, {"Allow": "POST"})
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        import base64

        raw = base64.b64decode(raw).decode("utf-8")
    try:
        msg = json.loads(raw)
    except ValueError:
        return _http(400, _error(None, -32700, "Parse error"))
    if isinstance(msg, list):  # legacy batch support
        responses = [r for r in (handle_rpc(m) for m in msg) if r is not None]
        return _http(200, responses) if responses else _http(202)
    response = handle_rpc(msg)
    if response is None:
        return _http(202)
    if "method" in msg and msg["method"] == "tools/call":
        print(json.dumps({"level": "INFO", "msg": "tool call", "tool": (msg.get("params") or {}).get("name"),
                          "arguments": (msg.get("params") or {}).get("arguments")}, ensure_ascii=False))
    return _http(200, response)
