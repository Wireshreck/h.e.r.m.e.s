"""Agent discovery for the H.E.R.M.E.S fleet.

The roster in ``bots/fleet.yaml`` is the single source of truth (the kanban
decomposer already routes by those descriptions). The registry reads it, adds
live inbox state from the comms DB, and offers capability-based lookup so an
agent can find a peer by role/description without memorising profile ids.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - ruamel ships with Hermes
    from ruamel.yaml import YAML  # type: ignore

from store import Store


@dataclass(frozen=True)
class AgentInfo:
    id: str
    role: str
    description: str
    provider: str = ""
    model: str = ""
    tools: tuple[str, ...] = field(default_factory=tuple)
    orchestrator: bool = False

    @property
    def searchable(self) -> str:
        return " ".join(
            [self.id, self.role, self.description, self.provider, self.model, *self.tools]
        ).lower()

    def to_dict(self, status: dict[str, Any] | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "role": self.role,
            "description": self.description,
            "provider": self.provider,
            "model": self.model,
            "tools": list(self.tools),
            "orchestrator": self.orchestrator,
        }
        if status:
            out["status"] = status
        return out


def fleet_path() -> Path:
    env = os.environ.get("HERMES_FLEET_YAML")
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parent.parent / "bots" / "fleet.yaml"


def load_agents() -> list[AgentInfo]:
    path = fleet_path()
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text) or {}
    except NameError:  # ruamel fallback
        data = YAML(typ="safe").load(text) or {}  # type: ignore
    base_tools = tuple((data.get("tools") or {}).get("base") or [])
    orchestrator = str(data.get("orchestrator") or "")
    agents = []
    for bot in data.get("bots") or []:
        tool_cfg = bot.get("tools") or {}
        tools = sorted(
            set(base_tools)
            | set(tool_cfg.get("enable") or [])
            | ({"kanban"} if bot["name"] == orchestrator else set())
            - set(tool_cfg.get("disable") or [])
            - set((data.get("tools") or {}).get("disable_default") or [])
        )
        agents.append(
            AgentInfo(
                id=str(bot["name"]),
                role=str(bot.get("role") or ""),
                description=" ".join(str(bot.get("description") or "").split()),
                provider=str(bot.get("provider") or ""),
                model=str(bot.get("model") or ""),
                tools=tuple(tools),
                orchestrator=bot["name"] == orchestrator,
            )
        )
    return agents


def get_agent(name: str) -> AgentInfo | None:
    target = name.strip().lower()
    for agent in load_agents():
        if agent.id.lower() == target:
            return agent
    return None


def find_agents(query: str, limit: int = 5) -> list[AgentInfo]:
    """Rank by exact id, role, then token overlap in description/tools."""
    q = query.strip().lower()
    agents = load_agents()
    if not q:
        return agents[:limit]
    scored: list[tuple[int, AgentInfo]] = []
    tokens = [t for t in q.replace("-", " ").replace("_", " ").split() if len(t) > 1]
    for agent in agents:
        score = 0
        if agent.id.lower() == q:
            score += 1000
        if q in agent.role.lower():
            score += 100
        haystack = agent.searchable
        if q in haystack:
            score += 50
        for token in tokens:
            if token in haystack:
                score += 10
        if score:
            scored.append((score, agent))
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [agent for _, agent in scored[:limit]]


def agent_status(agent: str, store: Store) -> dict[str, Any]:
    """Liveness from durable state: pending inbox, last message, conversations."""
    pending = store.inbox(agent, limit=1000, unread_only=True)
    return {
        "agent": agent,
        "known": get_agent(agent) is not None,
        "pending_messages": len(pending),
        "last_message_ms": max((m["timestamp_ms"] for m in pending), default=None),
        "state": "has_pending" if pending else "idle",
    }
