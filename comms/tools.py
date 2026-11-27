"""Agent-facing communication tools.

These functions are the single implementation behind both the MCP tool server
and the unit/integration tests. Every tool validates inputs, applies the
safety guards (rate limit, duplicate, depth, conversation cap) and returns a
JSON-serialisable dict. No placeholder or mock-only code lives here.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import replace
import subprocess
import time
from typing import Any

from config import CommsConfig
from delivery import Delivery, make_delivery
from protocol import (
    AgentMessage,
    DeliveryStatus,
    MessageType,
    new_id,
    now_ms,
)
from registry import agent_status, find_agents, get_agent, load_agents
from store import Store

logger = logging.getLogger("hermes.comms.tools")


class ToolError(ValueError):
    """Raised for invalid tool input or a rejected safety check."""


def _log(event: str, **fields: Any) -> None:
    logger.info(json.dumps({"event": event, **fields}, ensure_ascii=False))


class CommsService:
    def __init__(
        self,
        config: CommsConfig | None = None,
        store: Store | None = None,
        delivery: Delivery | None = None,
    ):
        self.config = config or CommsConfig.from_env()
        self.store = store or Store(self.config)
        self.delivery = delivery or make_delivery(self.config)
        self._owns_store = store is None

    def close(self) -> None:
        if self._owns_store:
            self.store.close()

    # -- helpers ------------------------------------------------------------
    def _known(self, agent: str) -> str:
        name = str(agent).strip()
        if not name:
            raise ToolError("agent id must not be empty")
        # Profiles can be created outside fleet.yaml; allow them but record a
        # warning so discovery still prefers roster entries.
        if get_agent(name) is None:
            _log("unknown_agent", agent=name)
        return name

    def _conversation(
        self,
        sender: str,
        recipients: tuple[str, ...],
        task_id: str | None,
        conversation_id: str | None,
        topic: str,
    ) -> str:
        if conversation_id:
            conv = self.store.get_conversation(conversation_id)
            if conv is None:
                raise ToolError(f"conversation not found: {conversation_id}")
            if sender not in conv.participants:
                raise ToolError("sender is not a participant of that conversation")
            return conv.id
        conv, _ = self.store.ensure_conversation(
            (sender, *recipients), task_id=task_id, topic=topic
        )
        return conv.id

    def _send(
        self,
        sender: str,
        recipients: tuple[str, ...],
        content: str,
        msg_type: MessageType,
        *,
        task_id: str | None = None,
        conversation_id: str | None = None,
        topic: str = "",
        priority: int = 1,
        reply_to: str | None = None,
        depth: int = 0,
        metadata: dict[str, Any] | None = None,
        deliver: bool = True,
        broadcast: bool = False,
    ) -> AgentMessage:
        sender = self._known(sender)
        recipients = tuple(dict.fromkeys(self._known(r) for r in recipients))
        if sender in recipients:
            raise ToolError("an agent cannot send a message to itself (loop risk)")
        if depth >= self.config.max_depth:
            raise ToolError(
                f"communication depth limit reached ({self.config.max_depth}); "
                "start a new conversation or use a kanban card instead"
            )
        conv_id = self._conversation(sender, recipients, task_id, conversation_id, topic)
        msg = AgentMessage(
            id=new_id("m"),
            sender=sender,
            recipients=recipients,
            conversation_id=conv_id,
            type=msg_type,
            content=content,
            timestamp_ms=now_ms(),
            priority=priority,
            task_id=task_id,
            reply_to=reply_to,
            depth=depth,
            metadata=dict(metadata or {}),
        )
        try:
            msg.validate(self.config.max_content_bytes)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        if self.store.rate_limited(sender):
            raise ToolError("rate limit reached: too many messages this minute")
        if self.store.duplicate(msg):
            raise ToolError("duplicate message detected (same sender/content/task recently)")
        if self.store.conversation_full(conv_id):
            raise ToolError("conversation is full; start a new conversation")
        self.store.add_message(msg)
        _log(
            "message_created",
            message_id=msg.id,
            conversation_id=conv_id,
            task_id=task_id,
            sender=sender,
            recipients=list(recipients),
            type=msg_type.value,
            depth=depth,
        )
        outcomes: list[bool] = []
        if deliver:
            for recipient in recipients:
                send = getattr(self.delivery, "send")
                try:
                    result = send(msg, recipient, broadcast=broadcast)
                except TypeError:  # custom Delivery implementations with 2-arg send
                    result = send(msg, recipient)
                self.store.mark(
                    msg.id,
                    recipient,
                    DeliveryStatus.DELIVERED if result.ok else DeliveryStatus.FAILED,
                    error=result.error,
                )
                _log(
                    "message_delivered" if result.ok else "message_delivery_failed",
                    message_id=msg.id,
                    agent=recipient,
                    method=result.method,
                    error=result.error,
                )
                outcomes.append(result.ok)
        if outcomes:
            msg = replace(
                msg,
                status=DeliveryStatus.DELIVERED if all(outcomes) else DeliveryStatus.PENDING,
            )
        return msg

    # -- tools --------------------------------------------------------------
    def send_message(
        self,
        sender: str,
        recipient: str,
        content: str,
        *,
        task_id: str | None = None,
        conversation_id: str | None = None,
        type: str = "informational",
        priority: int = 1,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a direct message to one agent. Returns ids and delivery state."""
        try:
            msg_type = MessageType(str(type).lower())
        except ValueError as exc:
            raise ToolError(f"unknown message type: {type}") from exc
        if msg_type is MessageType.REQUEST:
            raise ToolError("use ask_agent for requests that need a response")
        msg = self._send(
            sender, (recipient,), content, msg_type,
            task_id=task_id, conversation_id=conversation_id,
            priority=priority, metadata=metadata,
        )
        return {
            "message_id": msg.id,
            "conversation_id": msg.conversation_id,
            "recipient": recipient,
            "status": msg.status.value,
        }

    def broadcast_message(
        self,
        sender: str,
        recipients: list[str] | str,
        content: str,
        *,
        task_id: str | None = None,
        conversation_id: str | None = None,
        priority: int = 1,
    ) -> dict[str, Any]:
        """Broadcast to a list of agents, or to every registered agent with 'all'."""
        if isinstance(recipients, str):
            if recipients.strip().lower() == "all":
                targets = tuple(a.id for a in load_agents() if a.id != sender)
            else:
                targets = tuple(x.strip() for x in recipients.split(",") if x.strip())
        else:
            targets = tuple(str(x).strip() for x in recipients if str(x).strip())
        if not targets:
            raise ToolError("no recipients supplied for broadcast")
        if len(targets) > 50:
            raise ToolError("broadcast limited to 50 recipients")
        msg = self._send(
            sender, targets, content, MessageType.INFORMATIONAL,
            task_id=task_id, conversation_id=conversation_id, priority=priority,
            metadata={"broadcast": True}, broadcast=True,
        )
        return {
            "message_id": msg.id,
            "conversation_id": msg.conversation_id,
            "recipients": list(targets),
            "status": msg.status.value,
        }

    def ask_agent(
        self,
        sender: str,
        agent: str,
        question: str,
        *,
        task_id: str | None = None,
        conversation_id: str | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Send a REQUEST and wait (bounded) for that agent's RESPONSE.

        The wait is capped by config so the caller's executor is never blocked
        indefinitely; if the peer is busy the request stays in its inbox and a
        later ``reply_to_message`` still correlates correctly.
        """
        msg = self._send(
            sender, (agent,), question, MessageType.REQUEST,
            task_id=task_id, conversation_id=conversation_id,
            metadata={"expects_response": True},
        )
        timeout = min(
            max(int(timeout_seconds or self.config.request_timeout_seconds), 1),
            self.config.request_timeout_seconds,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            reply = self.store.find_reply(msg.id, sender)
            if reply is not None:
                _log("request_answered", message_id=msg.id, reply_id=reply.id, agent=agent)
                return {
                    "answered": True,
                    "request_id": msg.id,
                    "reply_id": reply.id,
                    "conversation_id": msg.conversation_id,
                    "from": reply.sender,
                    "content": reply.content,
                }
            time.sleep(0.2)
        _log("request_timeout", message_id=msg.id, agent=agent, timeout_seconds=timeout)
        return {
            "answered": False,
            "request_id": msg.id,
            "conversation_id": msg.conversation_id,
            "from": agent,
            "content": None,
            "note": f"no reply within {timeout}s; the request remains in the peer's inbox",
        }

    def reply_to_message(
        self,
        sender: str,
        message_id: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Reply to an incoming message; correlation and hop count are automatic."""
        with self.store._lock:
            row = self.store._conn.execute(
                "SELECT * FROM messages WHERE id=?", (message_id,)
            ).fetchone()
        if row is None:
            raise ToolError(f"message not found: {message_id}")
        original = self.store._message_from_row(row)
        if sender not in original.recipients:
            raise ToolError("only a recipient can reply to a message")
        msg = self._send(
            sender, (original.sender,), content, MessageType.RESPONSE,
            task_id=original.task_id, conversation_id=original.conversation_id,
            reply_to=original.id, depth=original.depth + 1, metadata=metadata,
        )
        self.store.mark(message_id, sender, DeliveryStatus.READ)
        return {
            "message_id": msg.id,
            "conversation_id": msg.conversation_id,
            "reply_to": message_id,
            "depth": msg.depth,
            "status": msg.status.value,
        }

    def get_inbox(
        self,
        agent: str,
        *,
        limit: int = 50,
        unread_only: bool = False,
        since_ms: int = 0,
        mark_read: bool = True,
    ) -> dict[str, Any]:
        """Retrieve incoming messages; by default marks them read on retrieval."""
        agent = self._known(agent)
        messages = self.store.inbox(agent, limit=limit, since_ms=since_ms, unread_only=unread_only)
        if mark_read:
            for m in messages:
                self.store.mark(m["id"], agent, DeliveryStatus.READ)
        return {"agent": agent, "count": len(messages), "messages": messages}

    def get_conversation(
        self,
        conversation_id: str,
        *,
        limit: int = 100,
        agent: str | None = None,
    ) -> dict[str, Any]:
        conv = self.store.get_conversation(conversation_id)
        if conv is None:
            raise ToolError(f"conversation not found: {conversation_id}")
        if agent and agent not in conv.participants:
            raise ToolError("agent is not a participant of that conversation")
        messages = self.store.history(conversation_id, limit=limit)
        return {
            "conversation": conv.to_dict(),
            "count": len(messages),
            "messages": [m.to_dict() for m in messages],
        }

    def get_agent_status(self, agent: str) -> dict[str, Any]:
        return agent_status(self._known(agent), self.store)

    def list_agents(self, query: str = "", *, limit: int = 20) -> dict[str, Any]:
        agents = find_agents(query, limit=limit)
        return {
            "count": len(agents),
            "agents": [a.to_dict(agent_status(a.id, self.store)) for a in agents],
        }

    def delegate_task(
        self,
        sender: str,
        agent: str,
        title: str,
        body: str = "",
        *,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Delegate via the EXISTING kanban dispatcher (no duplicate runtime).

        Creates a card assigned to the target agent; the gateway dispatcher
        claims it atomically and spawns that specialist as a worker process.
        """
        agent = self._known(agent)
        if not title.strip():
            raise ToolError("task title must not be empty")
        exe = shutil.which("hermes")
        if not exe:
            raise ToolError("hermes CLI not found on PATH")
        argv = [exe, "kanban", "create", title.strip()]
        if body:
            argv += ["--body", body]
        if agent:
            argv += ["--assignee", agent]
        if task_id:
            argv += ["--parents", task_id]
        argv += ["--json"]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ToolError("kanban create timed out") from exc
        if proc.returncode != 0:
            raise ToolError((proc.stderr or proc.stdout or "kanban create failed").strip()[:400])
        _log("delegation_kanban", sender=sender, agent=agent, task_id=task_id, title=title[:120])
        try:
            created = json.loads(proc.stdout)
        except json.JSONDecodeError:
            created = {"raw": proc.stdout.strip()[:1000]}
        return {"delegated": True, "assignee": agent, "card": created}

    def pending_messages(self, agent: str, *, limit: int = 20) -> dict[str, Any]:
        """Retry delivery of failed/pending messages (idempotent pump)."""
        agent = self._known(agent)
        pending = self.store.pending_for(agent, limit=limit)
        results = []
        for msg in pending:
            result = self.delivery.send(msg, agent)
            self.store.mark(
                msg.id, agent,
                DeliveryStatus.DELIVERED if result.ok else DeliveryStatus.FAILED,
                error=result.error,
            )
            results.append({"message_id": msg.id, **result.to_dict()})
        return {"agent": agent, "attempted": len(results), "results": results}
