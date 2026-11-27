"""Message delivery for the H.E.R.M.E.S communication bus.

Persisting a message is not enough: a recipient must actually see it. Delivery
is therefore a separate, pluggable step. The default ``auto`` mode tries to
inject the message into the recipient agent's running gateway/peer session via
the Hermes CLI (configurable command template), then falls back to the durable
inbox if injection is unavailable. Failed delivery never crashes the sender's
turn — it is recorded on the recipient row and retried by ``pending_messages``.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Protocol

from config import CommsConfig
from protocol import AgentMessage

logger = logging.getLogger("hermes.comms.delivery")


@dataclass(frozen=True)
class DeliveryResult:
    ok: bool
    method: str
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {"delivered": self.ok, "method": self.method, "error": self.error}


class Delivery(Protocol):
    def send(self, message: AgentMessage, agent: str) -> DeliveryResult:
        ...


class InboxDelivery:
    """Durable-only delivery: the message is in the recipient's inbox and will
    be surfaced on its next comms tool call or by the dispatcher's pump."""

    def send(self, message: AgentMessage, agent: str) -> DeliveryResult:
        logger.info(
            json.dumps({"event": "delivery_inbox", "message_id": message.id, "agent": agent})
        )
        return DeliveryResult(True, "inbox")


class CliDelivery:
    """Inject a message through the Hermes CLI into another profile's session.

    The command template is environment-configurable so operators can match
    their installed Hermes version without code changes. Placeholders:
    {agent}, {sender}, {message}, {message_id}, {conversation_id}, {task_id}.
    Example for a registered peer: hermes peer dm {agent} {message}
    """

    DEFAULT_TEMPLATE = "hermes peer dm {agent} {message}"

    def __init__(self, template: str | None = None, timeout: int = 15):
        self.template = template or os.environ.get(
            "HERMES_COMMS_DELIVERY_CMD", self.DEFAULT_TEMPLATE
        )
        self.timeout = timeout

    def _argv(self, message: AgentMessage, agent: str) -> list[str]:
        values = {
            "agent": agent,
            "sender": message.sender,
            "message": message.content,
            "message_id": message.id,
            "conversation_id": message.conversation_id,
            "task_id": message.task_id or "",
        }
        # Split like a shell would (quotes preserved in Python strings) so a
        # template can embed a path with spaces, e.g. "C:/Program Files/...".
        import shlex

        parts = shlex.split(self.template, posix=False)
        return [p.format(**values) for p in parts]

    def send(self, message: AgentMessage, agent: str) -> DeliveryResult:
        argv = self._argv(message, agent)
        exe = shutil.which(argv[0])
        if not exe:
            return DeliveryResult(False, "cli", f"executable not found: {argv[0]}")
        try:
            proc = subprocess.run(
                [exe, *argv[1:]],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return DeliveryResult(False, "cli", f"delivery timed out after {self.timeout}s")
        except OSError as exc:
            return DeliveryResult(False, "cli", str(exc))
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()[:400]
            return DeliveryResult(False, "cli", err or f"exit code {proc.returncode}")
        logger.info(
            json.dumps(
                {
                    "event": "delivery_cli",
                    "message_id": message.id,
                    "agent": agent,
                    "method": argv[0],
                }
            )
        )
        return DeliveryResult(True, "cli")


class HermesTurnDelivery:
    """Deliver by starting a REAL one-shot agent turn in the target profile.

    Uses the runtime's native `hermes -p <profile> chat -q ... -Q --source comms`
    path: the recipient actually processes the message as a turn and can call
    the comms tools (including reply_to_message) itself. No peer key or extra
    service is required.
    """

    def __init__(self, timeout: int = 120):
        self.timeout = timeout

    def send(self, message: AgentMessage, agent: str) -> DeliveryResult:
        exe = shutil.which("hermes")
        if not exe:
            return DeliveryResult(False, "agent_turn", "hermes CLI not found on PATH")
        preamble = (
            f"[AGENT MESSAGE from {message.sender} | id={message.id} | "
            f"conversation={message.conversation_id}"
            + (f" | task={message.task_id}" if message.task_id else "")
            + "]\n"
        )
        if message.type.value == "request":
            preamble += (
                f"Reply with comms reply_to_message(message_id='{message.id}', "
                "content=your_answer) so the sender can correlate it.\n"
            )
        text = preamble + message.content
        try:
            proc = subprocess.run(
                [exe, "-p", agent, "chat", "-q", text, "-Q", "--source", "comms"],
                capture_output=True, text=True, timeout=self.timeout, check=False,
            )
        except subprocess.TimeoutExpired:
            return DeliveryResult(False, "agent_turn", f"turn timed out after {self.timeout}s")
        except OSError as exc:
            return DeliveryResult(False, "agent_turn", str(exc))
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()[:400]
            return DeliveryResult(False, "agent_turn", err or f"exit code {proc.returncode}")
        logger.info(
            json.dumps({"event": "delivery_agent_turn", "message_id": message.id,
                        "agent": agent, "source": "comms"})
        )
        return DeliveryResult(True, "agent_turn")


class AutoDelivery:
    def __init__(self, config: CommsConfig):
        self.config = config
        self.turn = HermesTurnDelivery()
        self.cli = CliDelivery()
        self.inbox = InboxDelivery()

    def send(self, message: AgentMessage, agent: str, *, broadcast: bool = False) -> DeliveryResult:
        if self.config.delivery_mode == "inbox":
            return self.inbox.send(message, agent)
        # Broadcasts use the durable inbox only: starting a full model turn for
        # every recipient would be needlessly expensive and slow.
        if broadcast:
            return self.inbox.send(message, agent)
        if self.config.delivery_mode == "cli":
            result = self.cli.send(message, agent)
        else:
            result = self.turn.send(message, agent)
            if not result.ok and self.config.delivery_mode == "auto":
                result = self.cli.send(message, agent)
        if result.ok:
            return result
        logger.warning(
            json.dumps(
                {
                    "event": "delivery_fallback",
                    "message_id": message.id,
                    "agent": agent,
                    "error": result.error,
                }
            )
        )
        if self.config.strict_delivery:
            return result
        return self.inbox.send(message, agent)


def make_delivery(config: CommsConfig) -> Delivery:
    mode = config.delivery_mode
    if mode == "inbox":
        return InboxDelivery()
    if mode == "cli":
        return CliDelivery()
    if mode == "turn":
        return HermesTurnDelivery()
    if mode == "auto":
        return AutoDelivery(config)
    raise ValueError(f"unknown HERMES_COMMS_DELIVERY mode: {mode!r} (use auto|turn|cli|inbox)")
