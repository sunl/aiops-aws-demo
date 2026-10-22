"""Mock Elasticsearch MCP server, hosted on Amazon Bedrock AgentCore Runtime.

AgentCore Runtime (direct code deployment, entry point main.py) expects a
stateless Streamable HTTP MCP server on 0.0.0.0:8000/mcp. Callers are
authenticated by the runtime's JWT authorizer (Amazon Cognito, OAuth client
credentials) before a request reaches this process, so the server itself does
no auth -- do not run it elsewhere without putting authentication in front.

This is the standard MCP SDK (FastMCP) unchanged, which is the point of the
demo: an existing containerised MCP server moves to AgentCore with only the
host / port / stateless settings below.
"""
import functools
import json
import logging
import os
from typing import Annotated, Literal

import boto3
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

import es_tools

logging.basicConfig(level=logging.INFO, format="%(message)s")
LOG = logging.getLogger("es-mock")

LOG_GROUPS = es_tools.parse_log_groups(os.environ.get("LOG_GROUPS", ""))


@functools.lru_cache(maxsize=1)
def _logs():
    """Created on first use so a missing region shows up as a tool error rather than a crash at startup."""
    region = os.environ.get("LOGS_REGION") or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        raise es_tools.ToolError("server misconfigured: LOGS_REGION is not set")
    return boto3.client("logs", region_name=region)

mcp = FastMCP(
    "es-mock",
    instructions="Read-only search and aggregation over application logs in the Elasticsearch cluster "
                 "(indices logs-<service>-*). Use it to find error types, affected versions and when errors started.",
    host=os.environ.get("MCP_HOST", "0.0.0.0"),  # AgentCore requires 0.0.0.0; local tests use 127.0.0.1
    port=int(os.environ.get("MCP_PORT", "8000")),
    stateless_http=True,
)
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


def _audit(tool: str, **args):
    LOG.info(json.dumps({"level": "INFO", "msg": "tool call", "tool": tool, "arguments": args}, ensure_ascii=False))


@mcp.tool(annotations=READ_ONLY)
def es_search_logs(
    service: Annotated[str, Field(description="Service name, e.g. order-service (searches index logs-<service>-*)")],
    query: Annotated[str, Field(description="Optional text the log line must contain, e.g. ResourceNotFound")] = "",
    level: Annotated[Literal["", "ERROR", "WARN", "INFO"], Field(description="Optional log level filter")] = "",
    minutes: Annotated[int, Field(ge=1, le=es_tools.MAX_MINUTES, description="Look back this many minutes")] = 30,
    size: Annotated[int, Field(ge=1, le=es_tools.MAX_SIZE, description="Maximum documents to return")] = 20,
) -> dict:
    """Search a service's structured application logs in Elasticsearch, newest first. Each hit's _source holds
    the log fields (level, version, table, msg, error_type, error)."""
    _audit("es_search_logs", service=service, query=query, level=level, minutes=minutes, size=size)
    return es_tools.search_logs(_logs(), LOG_GROUPS, service, query=query, level=level, minutes=minutes, size=size)


@mcp.tool(annotations=READ_ONLY)
def es_error_summary(
    service: Annotated[str, Field(description="Service name, e.g. order-service")],
    minutes: Annotated[int, Field(ge=1, le=es_tools.MAX_MINUTES, description="Look back this many minutes")] = 60,
) -> dict:
    """Aggregate a service's ERROR logs in Elasticsearch: counts by error type, application version and table,
    first/last occurrence, and an errors-per-minute timeline. Use it to see when errors started and what changed."""
    _audit("es_error_summary", service=service, minutes=minutes)
    return es_tools.error_summary(_logs(), LOG_GROUPS, service, minutes=minutes)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
