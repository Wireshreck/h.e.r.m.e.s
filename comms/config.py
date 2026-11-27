"""Configuration for the H.E.R.M.E.S communication bus.

All limits are environment-overridable so operators can tune them without
editing code (the installer sets none of them by default). Defaults are
deliberately conservative: a single agent turn cannot flood the fleet or wait
forever for a peer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return min(max(int(os.environ.get(name, default)), lo), hi)
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class CommsConfig:
    """Every runtime knob for the bus (read once at MCP server startup)."""

    db_path: Path
    max_content_bytes: int = 16_384
    max_messages_per_conversation: int = 500
    max_conversations: int = 2_000
    conversation_ttl_days: int = 30
    conversation_idle_days: int = 7
    max_depth: int = 6
    rate_limit_per_minute: int = 30
    duplicate_window_seconds: int = 60
    request_timeout_seconds: int = 30
    delivery_mode: str = "auto"  # auto | peer | inbox
    hermes_home: Path | None = None
    strict_delivery: bool = False  # fail a send if injection cannot be confirmed

    @classmethod
    def from_env(cls) -> "CommsConfig":
        home_raw = os.environ.get("HERMES_HOME")
        home = Path(home_raw).expanduser() if home_raw else None
        if home is None:
            if os.name == "nt":
                base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
            else:
                base = Path.home() / ".local" / "share"
            candidate = base / "hermes"
            home = candidate if candidate.exists() else Path.home() / ".hermes"
        db_raw = os.environ.get("HERMES_COMMS_DB")
        db = Path(db_raw).expanduser() if db_raw else home / "comms.db"
        return cls(
            db_path=db,
            max_content_bytes=_int("HERMES_COMMS_MAX_BYTES", 16_384, 256, 1_048_576),
            max_messages_per_conversation=_int("HERMES_COMMS_MAX_MESSAGES", 500, 10, 100_000),
            max_conversations=_int("HERMES_COMMS_MAX_CONVERSATIONS", 2_000, 10, 1_000_000),
            conversation_ttl_days=_int("HERMES_COMMS_TTL_DAYS", 30, 1, 3650),
            conversation_idle_days=_int("HERMES_COMMS_IDLE_DAYS", 7, 1, 3650),
            max_depth=_int("HERMES_COMMS_MAX_DEPTH", 6, 1, 32),
            rate_limit_per_minute=_int("HERMES_COMMS_RATE_LIMIT", 30, 1, 10_000),
            duplicate_window_seconds=_int("HERMES_COMMS_DEDUPE_SECONDS", 60, 0, 86_400),
            request_timeout_seconds=_int("HERMES_COMMS_TIMEOUT", 30, 1, 600),
            delivery_mode=os.environ.get("HERMES_COMMS_DELIVERY", "auto").strip().lower(),
            hermes_home=home,
            strict_delivery=_bool("HERMES_COMMS_STRICT_DELIVERY", False),
        )
