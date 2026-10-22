#!/usr/bin/env python3
"""Smoke-test the es-mock MCP server: initialize -> tools/list -> tools/call.

Deployed (AgentCore Runtime, OAuth):
    <client secret on stdin> | test_es_mcp.py --endpoint URL --token-url URL --client-id ID --scope SCOPE
Local (python src/es_mcp/main.py with MCP_HOST=127.0.0.1, no auth):
    test_es_mcp.py --local --endpoint http://127.0.0.1:8000/mcp

Standard library only. The client secret is read from stdin so it never appears
in argv; the access token is never printed.
"""
import argparse
import base64
import json
import sys
import urllib.parse
import urllib.request


def get_token(token_url, client_id, secret, scope):
    basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    req = urllib.request.Request(token_url, method="POST",
                                 data=urllib.parse.urlencode({"grant_type": "client_credentials", "scope": scope}).encode(),
                                 headers={"Authorization": f"Basic {basic}",
                                          "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)["access_token"]


def parse_body(content_type, raw):
    """A Streamable HTTP response is either one JSON object or an SSE stream of JSON-RPC messages."""
    if "text/event-stream" in (content_type or ""):
        messages = [json.loads(line[5:].strip()) for line in raw.splitlines() if line.startswith("data:") and line[5:].strip()]
        return next((m for m in reversed(messages) if "result" in m or "error" in m), messages[-1] if messages else None)
    return json.loads(raw) if raw.strip() else None


class Client:
    def __init__(self, endpoint, token=None):
        self.endpoint, self.token, self.session_id, self.next_id = endpoint, token, None, 1

    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})}
        if not notify:
            msg["id"] = self.next_id
            self.next_id += 1
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(self.endpoint, data=json.dumps(msg).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=60) as resp:
            self.session_id = resp.headers.get("Mcp-Session-Id") or self.session_id
            body = resp.read().decode()
            return resp.status, parse_body(resp.headers.get("Content-Type"), body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", required=True)
    ap.add_argument("--token-url")
    ap.add_argument("--client-id")
    ap.add_argument("--scope", default="es-mock/read")
    ap.add_argument("--local", action="store_true", help="no OAuth (local server only)")
    args = ap.parse_args()

    token = None
    if not args.local:
        secret = sys.stdin.read().strip()
        token = get_token(args.token_url, args.client_id, secret, args.scope)
        print("== OAuth token obtained from Cognito (client credentials)")
    c = Client(args.endpoint, token)

    status, init = c.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": "smoke", "version": "0"}})
    print(f"== initialize: HTTP {status}, server={init['result']['serverInfo']['name']}, "
          f"protocol={init['result']['protocolVersion']}")
    c.send("notifications/initialized", notify=True)
    status, tools = c.send("tools/list")
    print(f"== tools/list: HTTP {status}, tools={[t['name'] for t in tools['result']['tools']]}")
    status, res = c.send("tools/call", {"name": "es_error_summary", "arguments": {"service": "order-service", "minutes": 60}})
    result = res["result"]
    text = result["content"][0]["text"]
    print(f"== tools/call es_error_summary: HTTP {status}, isError={result.get('isError', False)}")
    print(text[:1500])
    return 1 if result.get("isError") else 0


if __name__ == "__main__":
    sys.exit(main())
