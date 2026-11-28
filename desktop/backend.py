#!/usr/bin/env python3
"""H.E.R.M.E.S. desktop backend — a thin HTTP API over the real runtime.

This service never simulates agents or messages. It reads the kanban DB and
comms.db, invokes the Hermes CLI for orchestrator turns, and delegates every
agent-to-agent operation to the existing ``comms`` service (the same code the
MCP tools use). The Electron shell in ``desktop/`` is only a window; all state
comes from here.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "comms"))

from config import CommsConfig  # noqa: E402
from registry import load_agents  # noqa: E402
from store import Store  # noqa: E402
from tools import CommsService, ToolError  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

HERE = Path(__file__).resolve().parent
UI_DIR = HERE / "ui"
ORCHESTRATOR = "hermes-orchestrator"
SNAPSHOT_TTL = 2.0  # seconds — near-real-time without hammering the CLI/DB
MAX_INPUT = 20_000

DEFAULT_SETTINGS: dict[str, Any] = {
    "version": 1,
    "colors": {
        "bg": "#08090c", "bg2": "#0c0e13", "line": "#1c2029", "ink": "#d9dee8",
        "dim": "#798291", "accent": "#00e5ff", "teal": "#00d4aa", "amber": "#e8b04b",
        "red": "#ff5f56", "violet": "#a78bfa",
    },
    "canvas": {
        "positions": {}, "preset": "algorithm", "showHubEdges": True,
        "showCommEdges": True, "commActivityMs": 15000,
    },
    "behavior": {
        "pollMs": 2500, "revealDelayMs": 110, "retractDelayMs": 550, "autoFit": True,
    },
}

COLOR_KEYS = frozenset(DEFAULT_SETTINGS["colors"])
CANVAS_BOOL_KEYS = frozenset(("showHubEdges", "showCommEdges", "autoFit"))
BEHAVIOR_BOOL_KEYS = frozenset(("autoFit",))
HEX = __import__("re").compile(r"^#[0-9a-fA-F]{6}$")


def hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "hermes"
    return Path.home() / ".hermes"


@dataclass
class Job:
    id: str
    text: str
    status: str = "queued"  # queued|running|done|error|cancelled
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    stdout: list[str] = field(default_factory=list)
    stderr: list[str] = field(default_factory=list)
    process: subprocess.Popen | None = None

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id, "text": self.text, "status": self.status,
            "created_at": self.created_at, "finished_at": self.finished_at,
            "response": "".join(self.stdout)[-8000:],
            "error": "".join(self.stderr)[-2000:],
        }


class Runtime:
    """Single source of truth for UI snapshots and actions."""

    def __init__(self) -> None:
        self.home = hermes_home()
        self.config = CommsConfig.from_env()
        self.store = Store(self.config)
        self.comms = CommsService(self.config, self.store)
        self.lock = threading.RLock()
        self.jobs: dict[str, Job] = {}
        self.settings_path = self.home / "desktop-settings.json"
        self._snapshot: dict[str, Any] | None = None
        self._snapshot_at = 0.0
        self._hermes_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])

    def close(self) -> None:
        self.comms.close()
        self.store.close()

    # -- runtime health -----------------------------------------------------
    @staticmethod
    def find_hermes() -> str | None:
        for name in ("hermes.cmd", "hermes.exe", "hermes"):
            found = shutil.which(name)
            if found:
                return found
        return None

    def health(self) -> dict[str, Any]:
        exe = self.find_hermes()
        db_ok = self.store.db_path.exists()
        return {
            "connected": bool(exe),
            "hermes": exe or "not found on PATH",
            "comms_db": str(self.store.db_path),
            "comms_ok": db_ok,
            "kanban_db": str(self.home / "kanban.db"),
            "kanban_ok": (self.home / "kanban.db").exists(),
            "time": time.time(),
        }

    # -- reads --------------------------------------------------------------
    def _run(self, args: list[str], timeout: int = 20) -> subprocess.CompletedProcess:
        exe = self.find_hermes()
        if not exe:
            raise RuntimeError("Hermes CLI not found on PATH")
        return subprocess.run([exe, *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout, check=False)

    def tasks(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        if now - self._hermes_cache[0] < SNAPSHOT_TTL:
            return self._hermes_cache[1]
        tasks: list[dict[str, Any]] = []
        try:
            proc = self._run(["kanban", "list", "--json"])
            if proc.returncode == 0:
                tasks = json.loads(proc.stdout or "[]")
        except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            tasks = []
        self._hermes_cache = (now, tasks)
        return tasks

    def task_events(self, limit: int = 60) -> list[dict[str, Any]]:
        path = self.home / "kanban.db"
        if not path.exists():
            return []
        try:
            conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM task_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            conn.close()
        except sqlite3.Error:
            return []
        out = []
        for r in rows:
            d = dict(r)
            payload = d.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    pass
            d["payload"] = payload
            out.append(d)
        return out

    def _profile_models(self) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for agent in load_agents():
            path = self.home / "profiles" / agent.id / "config.yaml"
            model: dict[str, str] = {}
            if path.exists() and yaml is not None:
                try:
                    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                    section = data.get("model") or {}
                    model = {"provider": str(section.get("provider") or ""),
                             "model": str(section.get("default") or "")}
                except Exception:
                    model = {}
            out[agent.id] = model
        return out

    def _comms_counts(self) -> dict[str, dict[str, int]]:
        counts: dict[str, dict[str, int]] = {}
        try:
            with self.store._lock:
                rows = self.store._conn.execute(
                    """SELECT agent_id,
                              SUM(CASE WHEN status='delivered' THEN 1 ELSE 0 END) AS pending,
                              COUNT(*) AS total
                       FROM message_recipients GROUP BY agent_id"""
                ).fetchall()
            counts = {r["agent_id"]: {"pending": int(r["pending"] or 0), "total": int(r["total"])}
                      for r in rows}
        except sqlite3.Error:
            pass
        return counts

    def agents(self) -> list[dict[str, Any]]:
        tasks = self.tasks()
        models = self._profile_models()
        counts = self._comms_counts()
        out = []
        for agent in load_agents():
            assigned = [t for t in tasks if t.get("assignee") == agent.id
                        and t.get("status") not in ("archived",)]
            running = [t for t in assigned if t.get("status") == "running"]
            failed = [t for t in assigned if t.get("status") == "blocked" or t.get("last_failure_error")]
            pending = counts.get(agent.id, {}).get("pending", 0)
            if running:
                state = "working"
            elif failed:
                state = "failed"
            elif pending:
                state = "communicating"
            elif any(t.get("status") in ("ready", "todo", "review", "scheduled") for t in assigned):
                state = "waiting"
            else:
                state = "idle"
            out.append({
                "id": agent.id, "role": agent.role, "description": agent.description,
                "provider": models.get(agent.id, {}).get("provider", agent.provider),
                "model": models.get(agent.id, {}).get("model", agent.model),
                "tools": list(agent.tools), "orchestrator": agent.orchestrator,
                "status": state,
                "tasks": [{"id": t.get("id"), "title": t.get("title"), "status": t.get("status")}
                          for t in assigned[:8]],
                "current_task": running[0].get("title") if running else None,
                "comms": counts.get(agent.id, {"pending": 0, "total": 0}),
            })
        return out

    def messages(self, limit: int = 150, since_ms: int = 0,
                 conversation_id: str | None = None, agent: str | None = None) -> list[dict[str, Any]]:
        sql = """SELECT m.*, GROUP_CONCAT(r.agent_id) AS recipients
                 FROM messages m LEFT JOIN message_recipients r ON r.message_id=m.id
                 WHERE m.timestamp_ms >= ?"""
        params: list[Any] = [int(since_ms)]
        if conversation_id:
            sql += " AND m.conversation_id=?"
            params.append(conversation_id)
        if agent:
            sql += " AND (m.sender=? OR EXISTS (SELECT 1 FROM message_recipients x WHERE x.message_id=m.id AND x.agent_id=?))"
            params += [agent, agent]
        sql += " GROUP BY m.id ORDER BY m.timestamp_ms DESC, m.id DESC LIMIT ?"
        params.append(min(max(limit, 1), 500))
        with self.store._lock:
            rows = self.store._conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["recipients"] = [x for x in (d.get("recipients") or "").split(",") if x]
            try:
                d["metadata"] = json.loads(d.get("metadata") or "{}")
            except json.JSONDecodeError:
                d["metadata"] = {}
            out.append(d)
        return out

    def conversations(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list_conversations(limit=limit)

    # -- settings & models --------------------------------------------------
    def settings(self) -> dict[str, Any]:
        data = json.loads(json.dumps(DEFAULT_SETTINGS))  # deep copy
        try:
            if self.settings_path.exists():
                stored = json.loads(self.settings_path.read_text(encoding="utf-8"))
                for section in ("colors", "canvas", "behavior"):
                    if isinstance(stored.get(section), dict):
                        data[section].update(stored[section])
        except (OSError, json.JSONDecodeError):
            pass  # a corrupt settings file never breaks the app
        return data

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.settings()
        for key, value in (payload.get("colors") or {}).items():
            if key in COLOR_KEYS and isinstance(value, str) and HEX.fullmatch(value):
                current["colors"][key] = value
        canvas = payload.get("canvas") or {}
        if isinstance(canvas.get("positions"), dict):
            positions = {}
            for agent_id, pos in canvas["positions"].items():
                if isinstance(pos, dict) and all(
                    isinstance(pos.get(k), (int, float)) for k in ("x", "y")
                ):
                    positions[str(agent_id)[:64]] = {
                        "x": float(pos["x"]), "y": float(pos["y"]),
                    }
            current["canvas"]["positions"] = positions
        for key in ("showHubEdges", "showCommEdges"):
            if key in canvas and isinstance(canvas[key], bool):
                current["canvas"][key] = canvas[key]
        if isinstance(canvas.get("commActivityMs"), int):
            current["canvas"]["commActivityMs"] = min(max(canvas["commActivityMs"], 1000), 300000)
        if canvas.get("preset") in ("algorithm", "radial", "grid", "custom"):
            current["canvas"]["preset"] = canvas["preset"]
        behavior = payload.get("behavior") or {}
        for key, lo, hi in (("pollMs", 500, 60000), ("revealDelayMs", 0, 2000),
                            ("retractDelayMs", 0, 5000)):
            if isinstance(behavior.get(key), int):
                current["behavior"][key] = min(max(behavior[key], lo), hi)
        if isinstance(behavior.get("autoFit"), bool):
            current["behavior"]["autoFit"] = behavior["autoFit"]
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.settings_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
        os.replace(tmp, self.settings_path)
        self._snapshot = None  # settings affect snapshots (positions/edges)
        return current

    def reset_settings(self) -> dict[str, Any]:
        try:
            self.settings_path.unlink()
        except OSError:
            pass
        self._snapshot = None
        return self.settings()

    def models(self) -> dict[str, Any]:
        """Model choices are real configured models, never invented catalog data."""
        options: dict[str, list[str]] = {}
        try:
            data = (ROOT / "bots" / "fleet.yaml").read_text(encoding="utf-8")
            if yaml is not None:
                fleet = yaml.safe_load(data) or {}
                for bot in fleet.get("bots") or []:
                    provider = str(bot.get("provider") or "")
                    for model in [bot.get("model")] + [f.get("model") for f in (bot.get("fallback") or [])]:
                        if model and provider:
                            options.setdefault(provider, [])
                            if str(model) not in options[provider]:
                                options[provider].append(str(model))
        except Exception:
            pass
        current = {}
        for agent in load_agents():
            path = self.home / "profiles" / agent.id / "config.yaml"
            provider, model = agent.provider, agent.model
            if path.exists() and yaml is not None:
                try:
                    section = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("model") or {}
                    provider = str(section.get("provider") or provider)
                    model = str(section.get("default") or model)
                except Exception:
                    pass
            current[agent.id] = {"provider": provider, "model": model}
            if provider and model:
                options.setdefault(provider, [])
                if model not in options[provider]:
                    options[provider].append(model)
        return {"options": options, "current": current, "customAllowed": True}

    def set_model(self, agent_id: str, provider: str, model: str) -> dict[str, Any]:
        known = {a.id for a in load_agents()}
        if agent_id not in known:
            raise ToolError(f"unknown agent: {agent_id}")
        provider, model = str(provider).strip(), str(model).strip()
        if not provider or not model or len(model) > 200 or any(c in model for c in "\r\n\x00"):
            raise ToolError("invalid provider/model")
        self._run(["-p", agent_id, "config", "set", "model.provider", provider])
        self._run(["-p", agent_id, "config", "set", "model.default", model])
        sys.stderr.write(f"[desktop] model_change agent={agent_id} provider={provider} model={model}\n")
        self._hermes_cache = (0.0, [])
        self._snapshot = None
        return {"agent": agent_id, "provider": provider, "model": model}

    # -- actions ------------------------------------------------------------
    def submit_task(self, text: str) -> dict[str, Any]:
        text = str(text).strip()
        if not text or len(text) > MAX_INPUT:
            raise ToolError("task text must be 1..20000 characters")
        exe = self.find_hermes()
        if not exe:
            raise RuntimeError("Hermes CLI not found on PATH")
        job = Job(id=f"j_{uuid.uuid4().hex[:16]}", text=text)
        with self.lock:
            self.jobs[job.id] = job
        try:
            job.process = subprocess.Popen(
                [exe, "-p", ORCHESTRATOR, "chat", "-q", text, "-Q", "--source", "desktop"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            job.status, job.finished_at = "error", time.time()
            job.stderr.append(str(exc))
            raise
        job.status = "running"
        threading.Thread(target=self._collect, args=(job,), daemon=True, name=f"job-{job.id}").start()
        return job.view()

    def _collect(self, job: Job) -> None:
        assert job.process is not None
        try:
            out, err = job.process.communicate(timeout=3600)
            job.stdout.append(out or "")
            job.stderr.append(err or "")
            job.status = "done" if job.process.returncode == 0 else "error"
        except subprocess.TimeoutExpired:
            job.process.kill()
            job.status, job.finished_at = "error", time.time()
            job.stderr.append("job exceeded 3600s")
            return
        except Exception as exc:  # never lose a job on a thread failure
            job.status = "error"
            job.stderr.append(str(exc))
        job.finished_at = time.time()

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            job = self.jobs.get(job_id)
        if not job:
            raise ToolError(f"unknown job: {job_id}")
        if job.status in ("done", "error", "cancelled"):
            return job.view()
        if job.process and job.process.poll() is None:
            job.process.terminate()
        job.status, job.finished_at = "cancelled", time.time()
        return job.view()

    def job_list(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.lock:
            recent = sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)[:limit]
            return [j.view() for j in recent]

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._snapshot and now - self._snapshot_at < SNAPSHOT_TTL:
            return self._snapshot
        data = {
            "health": self.health(),
            "settings": self.settings(),
            "agents": self.agents(),
            "tasks": self.tasks()[:100],
            "events": self.task_events(),
            "messages": self.messages(),
            "conversations": self.conversations(),
            "jobs": self.job_list(),
            "server_time": time.time(),
        }
        with self.lock:
            self._snapshot, self._snapshot_at = data, now
        return data


RUNTIME: Runtime | None = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter logs
        sys.stderr.write("[desktop] " + (fmt % args) + "\n")

    # -- helpers ------------------------------------------------------------
    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, name: str) -> None:
        path = (UI_DIR / name).resolve()
        if UI_DIR not in path.parents or not path.is_file():
            self._json({"error": "not found"}, 404)
            return
        body = path.read_bytes()
        ctype = "text/html; charset=utf-8" if name.endswith(".html") else (
            "text/css; charset=utf-8" if name.endswith(".css") else
            "application/javascript; charset=utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 100_000:
            raise ToolError("request body too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            raise ToolError(f"invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ToolError("JSON object expected")
        return data

    # -- routes -------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        assert RUNTIME is not None
        parsed = urlparse(self.path)
        path, q = parsed.path, parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path == "/app.js":
                return self._static("app.js")
            if path == "/styles.css":
                return self._static("styles.css")
            if path == "/api/health":
                return self._json(RUNTIME.health())
            if path == "/api/snapshot":
                return self._json(RUNTIME.snapshot())
            if path == "/api/agents":
                return self._json({"agents": RUNTIME.agents()})
            if path == "/api/tasks":
                return self._json({"tasks": RUNTIME.tasks()})
            if path == "/api/messages":
                return self._json({"messages": RUNTIME.messages(
                    limit=int(q.get("limit", ["150"])[0]),
                    since_ms=int(q.get("since", ["0"])[0]),
                    conversation_id=q.get("conversation", [None])[0],
                    agent=q.get("agent", [None])[0],
                )})
            if path == "/api/conversations":
                return self._json({"conversations": RUNTIME.conversations()})
            if path == "/api/jobs":
                return self._json({"jobs": RUNTIME.job_list()})
            if path == "/api/settings":
                return self._json(RUNTIME.settings())
            if path == "/api/models":
                return self._json(RUNTIME.models())
            self._json({"error": "not found"}, 404)
        except (ToolError, ValueError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:
            sys.stderr.write(f"[desktop] GET {path} failed: {exc!r}\n")
            self._json({"error": "internal error"}, 500)

    def do_POST(self) -> None:  # noqa: N802
        assert RUNTIME is not None
        path = urlparse(self.path).path
        try:
            data = self._body()
            if path == "/api/tasks":
                return self._json(RUNTIME.submit_task(str(data.get("text") or "")), 201)
            if path == "/api/messages":
                return self._json(RUNTIME.comms.send_message(
                    sender=str(data["sender"]), recipient=str(data["recipient"]),
                    content=str(data["content"]),
                    task_id=data.get("task_id"), conversation_id=data.get("conversation_id"),
                ), 201)
            if path == "/api/ask":
                return self._json(RUNTIME.comms.ask_agent(
                    sender=str(data["sender"]), agent=str(data["agent"]),
                    question=str(data["question"]),
                    task_id=data.get("task_id"),
                    timeout_seconds=data.get("timeout_seconds"),
                ))
            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                return self._json(RUNTIME.cancel_job(path.split("/")[3]))
            if path == "/api/settings":
                return self._json(RUNTIME.save_settings(data))
            if path == "/api/settings/reset":
                return self._json(RUNTIME.reset_settings())
            if path.startswith("/api/agents/") and path.endswith("/model"):
                agent_id = path.split("/")[3]
                return self._json(RUNTIME.set_model(agent_id, str(data.get("provider") or ""),
                                                    str(data.get("model") or "")))
            self._json({"error": "not found"}, 404)
        except (ToolError, KeyError, ValueError) as exc:
            self._json({"error": str(exc)}, 400)
        except RuntimeError as exc:
            self._json({"error": str(exc)}, 503)
        except Exception as exc:
            sys.stderr.write(f"[desktop] POST {path} failed: {exc!r}\n")
            self._json({"error": "internal error"}, 500)


def main() -> int:
    global RUNTIME
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=int(os.environ.get("HERMES_DESKTOP_PORT", "8787")))
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    RUNTIME = Runtime()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    sys.stderr.write(f"[desktop] backend on http://{args.host}:{args.port}\n")
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        RUNTIME.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
