"""Typed message protocol for H.E.R.M.E.S agent-to-agent communication.

The wire format is JSON (MCP tool arguments/results and SQLite both speak it).
Every message carries the correlation fields needed for task-scoped
conversations, request/response matching and loop prevention. Keep this module
dependency-free so it can be imported by the MCP server, tests and the Rust
client alike.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MessageType(str, Enum):
    REQUEST = "request"
    RESPONSE = "response"
    INFORMATIONAL = "informational"
    DELEGATION = "delegation"
    EVENT = "event"
    ACKNOWLEDGEMENT = "acknowledgement"


class DeliveryStatus(str, Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    EXPIRED = "expired"


_AGENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_ID = re.compile(r"^[a-z]{1,4}_[0-9a-f]{12,32}$")


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def valid_agent_id(value: str) -> bool:
    return isinstance(value, str) and bool(_AGENT_ID.fullmatch(value))


def content_fingerprint(sender: str, content: str, task_id: str | None) -> str:
    """Stable hash used for repeated/redundant message detection."""
    raw = f"{sender.strip().lower()}\x00{content.strip().lower()}\x00{task_id or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AgentMessage:
    """One message on the communication bus.

    ``id``/``conversation_id`` use short prefixes (m_/c_) so log lines are
    greppable; ``depth`` is the agent-to-agent hop count used for loop guards.
    """

    id: str
    sender: str
    recipients: tuple[str, ...]
    conversation_id: str
    type: MessageType
    content: str
    timestamp_ms: int
    priority: int = 1
    task_id: str | None = None
    reply_to: str | None = None
    status: DeliveryStatus = DeliveryStatus.PENDING
    depth: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self, max_content_bytes: int) -> None:
        if not _ID.fullmatch(self.id):
            raise ValueError(f"invalid message id: {self.id!r}")
        if not valid_agent_id(self.sender):
            raise ValueError(f"invalid sender id: {self.sender!r}")
        if not self.recipients or any(not valid_agent_id(r) for r in self.recipients):
            raise ValueError("recipients must be a non-empty list of valid agent ids")
        if not _ID.fullmatch(self.conversation_id):
            raise ValueError(f"invalid conversation id: {self.conversation_id!r}")
        if self.type not in MessageType:
            raise ValueError(f"invalid message type: {self.type!r}")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("message content must be non-empty")
        if len(self.content.encode("utf-8")) > max_content_bytes:
            raise ValueError(f"message exceeds {max_content_bytes} bytes")
        if not 0 <= self.priority <= 3:
            raise ValueError("priority must be between 0 and 3")
        if self.depth < 0:
            raise ValueError("depth must be non-negative")
        if self.status not in DeliveryStatus:
            raise ValueError(f"invalid delivery status: {self.status!r}")
        if self.reply_to is not None and not _ID.fullmatch(self.reply_to):
            raise ValueError(f"invalid reply_to id: {self.reply_to!r}")
        json.dumps(self.metadata)  # metadata must be JSON-serializable

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sender": self.sender,
            "recipients": list(self.recipients),
            "conversation_id": self.conversation_id,
            "task_id": self.task_id,
            "type": self.type.value,
            "content": self.content,
            "timestamp_ms": self.timestamp_ms,
            "priority": self.priority,
            "status": self.status.value,
            "reply_to": self.reply_to,
            "depth": self.depth,
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AgentMessage":
        try:
            return cls(
                id=str(raw["id"]),
                sender=str(raw["sender"]),
                recipients=tuple(str(r) for r in raw["recipients"]),
                conversation_id=str(raw["conversation_id"]),
                task_id=raw.get("task_id"),
                type=MessageType(str(raw["type"])),
                content=str(raw["content"]),
                timestamp_ms=int(raw["timestamp_ms"]),
                priority=int(raw.get("priority", 1)),
                status=DeliveryStatus(str(raw.get("status", DeliveryStatus.PENDING.value))),
                reply_to=raw.get("reply_to"),
                depth=int(raw.get("depth", 0)),
                metadata=dict(raw.get("metadata") or {}),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"malformed agent message: {exc}") from exc

    @classmethod
    def from_json(cls, raw: str) -> "AgentMessage":
        try:
            return cls.from_dict(json.loads(raw))
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"malformed agent message JSON: {exc}") from exc


@dataclass(frozen=True)
class Conversation:
    id: str
    participants: tuple[str, ...]
    topic: str = ""
    task_id: str | None = None
    created_at_ms: int = field(default_factory=now_ms)
    updated_at_ms: int = field(default_factory=now_ms)

    def validate(self) -> None:
        if not _ID.fullmatch(self.id):
            raise ValueError(f"invalid conversation id: {self.id!r}")
        if not self.participants or any(not valid_agent_id(a) for a in self.participants):
            raise ValueError("participants must contain valid agent ids")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "participants": list(self.participants),
            "topic": self.topic,
            "task_id": self.task_id,
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Conversation":
        return cls(
            id=str(raw["id"]),
            participants=tuple(str(a) for a in raw["participants"]),
            topic=str(raw.get("topic") or ""),
            task_id=raw.get("task_id"),
            created_at_ms=int(raw.get("created_at_ms") or now_ms()),
            updated_at_ms=int(raw.get("updated_at_ms") or now_ms()),
        )
