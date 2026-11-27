#!/usr/bin/env python3
"""H.E.R.M.E.S agent communication bus — MCP stdio server.

Exposes the comms tools to any Hermes profile that registers this server in
``mcp_servers`` (see bots/fleet.yaml). Implemented as plain JSON-RPC 2.0 over
stdio so it needs no third-party package: Python 3.9+ is enough. All logs go
to stderr because stdout is reserved for the MCP protocol.

Run directly for a smoke check:
    echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}' | python comms/server.py
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from typing import Any, Callable

from config import CommsConfig
from tools import CommsService, ToolError

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format='{"ts":%(created)f,"level":"%(levelname)s","logger":"%(name)s","msg":%(message)s}',
)
logger = logging.getLogger("hermes.comms.server")

SERVER_NAME = "hermes-comms"
SERVER_VERSION = "0.1.0"

SERVICE: CommsService | None = None


def _tool_definitions() -> list[dict[str, Any]]:
    def tool(name: str, description: str, schema: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": name,
            "description": description,
            "inputSchema": {"type": "object", "properties": schema["properties"],
                            "required": schema.get("required", [])},
        }

    agent = {"type": "string", "description": "Agent profile id (e.g. 'researcher')"}
    content = {"type": "string", "description": "Message body (plain text)"}
    optional_id = {"type": "string", "description": "Optional task/card id used to scope the conversation"}
    return [
        tool("send_message", "Send a direct message to one agent. Delivers to its inbox/gateway and persists it.", {
            "properties": {
                "sender": agent, "recipient": agent, "content": content,
                "task_id": optional_id, "conversation_id": {"type": "string"},
                "type": {"type": "string", "enum": ["informational", "event", "delegation", "acknowledgement"],
                         "default": "informational"},
                "priority": {"type": "integer", "minimum": 0, "maximum": 3, "default": 1},
                "metadata": {"type": "object"},
            }, "required": ["sender", "recipient", "content"],
        }),
        tool("broadcast_message", "Broadcast a message to several agents, or 'all' for the whole fleet.", {
            "properties": {
                "sender": agent,
                "recipients": {"oneOf": [{"type": "array", "items": agent}, {"type": "string"}],
                               "description": "List of agent ids, comma-separated ids, or 'all'"},
                "content": content, "task_id": optional_id, "conversation_id": {"type": "string"},
                "priority": {"type": "integer", "minimum": 0, "maximum": 3, "default": 1},
            }, "required": ["sender", "recipients", "content"],
        }),
        tool("ask_agent", "Ask another agent for help and wait briefly for its reply (bounded timeout). "
             "The request stays in its inbox if it is busy; reply_to_message can answer later.", {
            "properties": {
                "sender": agent, "agent": agent, "question": content,
                "task_id": optional_id, "conversation_id": {"type": "string"},
                "timeout_seconds": {"type": "integer", "minimum": 1, "description": "Max wait (capped by config)"},
            }, "required": ["sender", "agent", "question"],
        }),
        tool("reply_to_message", "Reply to an incoming message; correlation and hop limits are automatic.", {
            "properties": {"sender": agent, "message_id": {"type": "string"}, "content": content,
                           "metadata": {"type": "object"}},
            "required": ["sender", "message_id", "content"],
        }),
        tool("get_inbox", "Retrieve incoming messages for an agent (marks them read unless disabled).", {
            "properties": {
                "agent": agent, "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
                "unread_only": {"type": "boolean", "default": False},
                "since_ms": {"type": "integer", "default": 0},
                "mark_read": {"type": "boolean", "default": True},
            }, "required": ["agent"],
        }),
        tool("get_conversation", "Read the persisted history of a task-scoped conversation.", {
            "properties": {"conversation_id": {"type": "string"},
                           "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
                           "agent": agent},
            "required": ["conversation_id"],
        }),
        tool("get_agent_status", "Check another agent's known status and pending inbox size.", {
            "properties": {"agent": agent}, "required": ["agent"],
        }),
        tool("list_agents", "Discover fleet agents by id, role, description or capability (fuzzy query).", {
            "properties": {"query": {"type": "string", "default": ""},
                           "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20}},
            "required": [],
        }),
        tool("delegate_task", "Delegate work through the existing kanban dispatcher (creates an assigned card).", {
            "properties": {"sender": agent, "agent": agent, "title": {"type": "string"},
                           "body": {"type": "string", "default": ""}, "task_id": optional_id},
            "required": ["sender", "agent", "title"],
        }),
        tool("pending_messages", "Retry delivery of failed/pending messages for an agent.", {
            "properties": {"agent": agent, "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}},
            "required": ["agent"],
        }),
    ]


def _dispatch(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if SERVICE is None:
        raise ToolError("service not initialised")
    methods: dict[str, Callable[..., dict[str, Any]]] = {
        "send_message": SERVICE.send_message,
        "broadcast_message": SERVICE.broadcast_message,
        "ask_agent": SERVICE.ask_agent,
        "reply_to_message": SERVICE.reply_to_message,
        "get_inbox": SERVICE.get_inbox,
        "get_conversation": SERVICE.get_conversation,
        "get_agent_status": SERVICE.get_agent_status,
        "list_agents": SERVICE.list_agents,
        "delegate_task": SERVICE.delegate_task,
        "pending_messages": SERVICE.pending_messages,
    }
    if name not in methods:
        raise ToolError(f"unknown tool: {name}")
    return methods[name](**args)


def handle(request: dict[str, Any]) -> dict[str, Any] | None:
    """Return a JSON-RPC response, or None for notifications (no reply)."""
    method = request.get("method")
    rid = request.get("id")
    params = request.get("params") or {}
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": rid,
            "result": {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": _tool_definitions()}}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            result = _dispatch(str(name), args)
            return {
                "jsonrpc": "2.0", "id": rid,
                "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                           "isError": False},
            }
        except (ToolError, ValueError, TypeError) as exc:
            return {
                "jsonrpc": "2.0", "id": rid,
                "result": {"content": [{"type": "text", "text": str(exc)}], "isError": True},
            }
        except Exception:  # never crash the stdio loop on a bad tool call
            logger.error("tool call failed: %s", traceback.format_exc())
            return {
                "jsonrpc": "2.0", "id": rid,
                "result": {"content": [{"type": "text", "text": "internal error (see comms server log)"}],
                           "isError": True},
            }
    if rid is None:  # unknown notification: ignore
        return None
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"Method not found: {method}"}}


def main() -> int:
    global SERVICE
    SERVICE = CommsService(CommsConfig.from_env())
    logger.info("server_started db=%s delivery=%s", SERVICE.config.db_path, SERVICE.config.delivery_mode)
    SERVICE.store.prune()
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                response = {"jsonrpc": "2.0", "id": None,
                            "error": {"code": -32700, "message": "Parse error"}}
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
                continue
            response = handle(request)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
    finally:
        SERVICE.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
