#!/usr/bin/env python3
"""H.E.R.M.E.S fleet installer.

Idempotently creates/repairs the 13 Hermes Agent profiles ("bots") declared in
bots/fleet.yaml, wires the kanban orchestration board, and verifies the fleet.

Usage:
    python scripts/bootstrap_fleet.py                    # profiles only
    python scripts/bootstrap_fleet.py --with-board       # also wire the kanban board
    python scripts/bootstrap_fleet.py --verify-only      # print fleet state, change nothing

Requires: `hermes` on PATH with OPENROUTER_API_KEY and GOOGLE_API_KEY configured
(the two free providers, see `hermes auth list`).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError as e:  # pragma: no cover
    raise SystemExit("PyYAML is required: python -m pip install pyyaml") from e

ROOT = Path(__file__).resolve().parent.parent
FLEET = ROOT / "bots" / "fleet.yaml"
SOULS = ROOT / "bots" / "souls"

DEFAULT_ENV_KEYS = ["OPENROUTER_API_KEY", "GOOGLE_API_KEY"]


def hermes_home() -> Path:
    """Resolve the Hermes home directory (root profile home)."""
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env).expanduser()
    if os.name == "nt":
        return Path(os.environ["LOCALAPPDATA"]) / "hermes"
    return Path.home() / ".hermes"


def find_hermes() -> str:
    for name in ("hermes", "hermes.cmd", "hermes.exe"):
        p = shutil.which(name)
        if p:
            return p
    raise SystemExit("hermes CLI not found on PATH - install Hermes Agent first")


def run_hermes(args: list[str], home: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run `hermes ...` (optionally pinned to a profile with -p)."""
    cmd = [find_hermes()]
    if home is not None:
        cmd += ["-p", str(home)]
    cmd += args
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def hermes_config_get(profile: str, key: str) -> str:
    r = run_hermes(["-p", profile, "config", "get", key], check=False)
    return (r.stdout or "").strip()


def profile_exists(profile: str) -> bool:
    r = run_hermes(["profile", "show", profile], check=False)
    return r.returncode == 0


ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$")


def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = ENV_LINE.match(line)
        if m and not m.group(1).startswith("#"):
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def merge_env(profile_home: Path, keys: dict[str, str]) -> int:
    """Append missing provider keys from the root .env into a profile .env.
    Returns how many keys were added. Values are never printed."""
    env_path = profile_home / ".env"
    existing = read_env(env_path)
    missing = [k for k in keys if k not in existing]
    if not missing:
        return 0
    lines = "".join(f"{k}={keys[k]}\n" for k in missing)
    with env_path.open("a", encoding="utf-8") as f:
        f.write(lines)
    return len(missing)


def yaml_literal(entries: list[dict[str, str]]) -> str:
    """Render fallback entries as the inline YAML `config set` accepts."""
    if not entries:
        return "[]"
    parts = ", ".join("{provider: %s, model: %s}" % (e["provider"], e["model"]) for e in entries)
    return f"[{parts}]"


def apply_bot(bot: dict, home: Path, root_env: dict[str, str], dry: bool = False) -> list[str]:
    """Create/repair one bot profile. Returns a list of human-readable actions."""
    name, actions = bot["name"], []
    if not profile_exists(name):
        actions.append(f"create profile {name}")
        if not dry:
            run_hermes(["profile", "create", name, "--description", bot["description"].strip()])
    else:
        actions.append(f"exists     {name}")
    if dry:
        return actions

    # Description (authoritative; the kanban decomposer routes by it)
    run_hermes(["profile", "describe", name, "--text", bot["description"].strip()])

    # Model + provider (free tiers only)
    run_hermes(["-p", name, "config", "set", "model.provider", bot["provider"]])
    run_hermes(["-p", name, "config", "set", "model.default", bot["model"]])
    # Neutralize any provider-specific leftovers inherited from the seed config.
    for leftover in ("model.api_key", "model.api_mode", "model.base_url"):
        run_hermes(["-p", name, "config", "unset", leftover], check=False)

    # Fallback chain (tried when the primary hits rate-limit / overload / connection errors)
    run_hermes(["-p", name, "config", "set", "fallback_providers", yaml_literal(bot.get("fallback", []))])

    # Local fallback support: make sure the profile keeps its ollama provider block.
    uses_local = any(e["provider"] == "ollama-launch" for e in bot.get("fallback", []))
    if uses_local:
        r = run_hermes(["-p", name, "config", "get", "providers.ollama-launch.api"], check=False)
        if r.returncode != 0 or not r.stdout.strip():
            run_hermes(["-p", name, "config", "set", "providers.ollama-launch.api", "http://127.0.0.1:11434/v1"])
            run_hermes(["-p", name, "config", "set", "providers.ollama-launch.name", "Ollama"])
            run_hermes(["-p", name, "config", "set", "providers.ollama-launch.default_model", "qwen3:4b-32k"])

    # Credentials: copy the free-provider keys from the root .env into the profile .env.
    added = merge_env(home / "profiles" / name, root_env)
    if added:
        actions.append(f"  env: copied {added} key(s) into {name}/.env")

    # Persona
    soul = SOULS / f"{name}.md"
    if soul.exists():
        (home / "profiles" / name / "SOUL.md").write_text(soul.read_text(encoding="utf-8"), encoding="utf-8")
        actions.append(f"  soul: {soul.name}")
    return actions


def apply_board(home: Path, apply: bool, workdir: str | None) -> None:
    fleet = yaml.safe_load(FLEET.read_text(encoding="utf-8"))
    board, orch = fleet["board"], fleet["orchestrator"]

    run_hermes(["kanban", "init"])
    if not apply:
        return

    run_hermes(["config", "set", "kanban.orchestrator_profile", orch])
    run_hermes(["config", "set", "kanban.auto_decompose", "true"])
    run_hermes(["config", "set", "auxiliary.kanban_decomposer.provider", board["decomposer"]["provider"]])
    run_hermes(["config", "set", "auxiliary.kanban_decomposer.model", board["decomposer"]["model"]])
    run_hermes(["-p", orch, "tools", "enable", "kanban"])

    wd = workdir or str(ROOT)
    run_hermes(["kanban", "boards", "set-default-workdir", board["slug"], wd])
    print(f"Board '{board['slug']}' default workdir -> {wd}")


def verify(fleet: dict, home: Path) -> None:
    print(f"{'profile':<22} {'provider':<14} {'model':<40} status")
    print("-" * 96)
    for bot in fleet["bots"]:
        if profile_exists(bot["name"]):
            p = hermes_config_get(bot["name"], "model.provider") or "-"
            m = hermes_config_get(bot["name"], "model.default") or "-"
            print(f"{bot['name']:<22} {p:<14} {m:<40} installed")
        else:
            print(f"{bot['name']:<22} {bot['provider']:<14} {bot['model']:<40} MISSING (run without --verify-only)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--with-board", action="store_true", help="also wire the kanban orchestration board")
    ap.add_argument("--verify-only", action="store_true", help="print fleet state and change nothing")
    ap.add_argument("--workdir", metavar="PATH", help="board default workdir (default: this repository)")
    args = ap.parse_args()

    fleet = yaml.safe_load(FLEET.read_text(encoding="utf-8"))
    home = hermes_home()
    root_env = read_env(home / ".env")

    if args.verify_only:
        verify(fleet, home)
        return 0

    print(f"Hermes home: {home}")
    print(f"Fleet:       {len(fleet['bots'])} bots from {FLEET}")
    print()

    for bot in fleet["bots"]:
        for line in apply_bot(bot, home, root_env):
            print(line)
        print()

    if args.with_board:
        print("Wiring kanban orchestration board ...")
        apply_board(home, apply=True, workdir=args.workdir)

    print("\nFleet ready. Useful next steps:")
    print(f"  hermes -p {fleet['orchestrator']} chat   # chat with the orchestrator")
    print("  hermes kanban create \"<task>\" --assignee <bot>   # hand a card to a specialist")
    print("  hermes gateway start      # dispatcher ticks every 60s (or: hermes kanban dispatch)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
