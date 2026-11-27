"""Hermetic backend tests: no model calls, no paid APIs, real SQLite comms DB."""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

DESKTOP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESKTOP))
sys.path.insert(0, str(DESKTOP.parent / "comms"))

spec = importlib.util.spec_from_file_location("desktop_backend", DESKTOP / "backend.py")
backend = importlib.util.module_from_spec(spec)
sys.modules["desktop_backend"] = backend
assert spec.loader is not None
spec.loader.exec_module(backend)


class FakeProcess:
    def __init__(self, output: str = "orchestrator reply", code: int = 0, block: bool = False):
        self.output, self.code = output, code
        self.returncode = None
        self.pid = 4242
        self.gate = threading.Event()
        if not block:
            self.gate.set()

    def release(self):
        self.gate.set()

    def communicate(self, timeout=None):
        self.gate.wait(timeout or 10)
        self.returncode = self.code
        return self.output, ""

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15


class BackendTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "hermes"
        (self.home / "profiles").mkdir(parents=True)
        # minimal kanban db with the event table the UI reads
        conn = sqlite3.connect(self.home / "kanban.db")
        conn.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT, created_at REAL)")
        conn.execute("INSERT INTO task_events VALUES (1, 't_1', 'claimed', '{}', 1.0)")
        conn.commit(); conn.close()
        self.env = patch.dict(os.environ, {
            "HERMES_HOME": str(self.home),
            "HERMES_COMMS_DB": str(self.home / "comms.db"),
            "HERMES_COMMS_DELIVERY": "inbox",
        })
        self.env.start()
        self.tasks_patch = patch.object(backend.Runtime, "tasks", lambda _self: self.fake_tasks())
        self.tasks_patch.start()
        self.runtime = backend.Runtime()

    def tearDown(self) -> None:
        self.runtime.close()
        self.tasks_patch.stop()
        self.env.stop()
        self.tmp.cleanup()

    def fake_tasks(self):
        return [
            {"id": "t_1", "title": "running card", "assignee": "writer", "status": "running",
             "created_at": 1, "body": "do work"},
            {"id": "t_2", "title": "queued card", "assignee": "researcher", "status": "ready",
             "created_at": 2, "body": "research"},
        ]

    # -- health / discovery --------------------------------------------------
    def test_health_reports_real_runtime_state(self):
        with patch.object(backend.Runtime, "find_hermes", staticmethod(lambda: None)):
            self.assertFalse(self.runtime.health()["connected"])
        with patch.object(backend.Runtime, "find_hermes", staticmethod(lambda: "C:/hermes.cmd")):
            h = self.runtime.health()
            self.assertTrue(h["connected"])
            self.assertTrue(h["comms_ok"])
            self.assertTrue(h["kanban_ok"])

    def test_agents_from_roster_with_real_status_derivation(self):
        agents = self.runtime.agents()
        self.assertEqual(len(agents), 13)
        by_id = {a["id"]: a for a in agents}
        self.assertEqual(by_id["writer"]["status"], "working")
        self.assertEqual(by_id["researcher"]["status"], "waiting")
        self.assertEqual(by_id["developer"]["status"], "idle")
        self.assertTrue(by_id["hermes-orchestrator"]["orchestrator"])

    def test_agents_show_communicating_state_for_pending_message(self):
        self.runtime.comms.send_message("writer", "developer", "hello from backend")
        with patch.object(backend.Runtime, "tasks", lambda self: []):
            agents = self.runtime.agents()
        by_id = {a["id"]: a for a in agents}
        self.assertEqual(by_id["developer"]["status"], "communicating")
        self.assertEqual(by_id["developer"]["comms"]["pending"], 1)

    # -- comms integration ---------------------------------------------------
    def test_messages_and_conversations_come_from_comms_db(self):
        sent = self.runtime.comms.send_message("writer", "researcher", "verify the figure", task_id="t_9")
        msgs = self.runtime.messages()
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["id"], sent["message_id"])
        self.assertEqual(msgs[0]["recipients"], ["researcher"])
        convs = self.runtime.conversations()
        self.assertEqual(len(convs), 1)
        filtered = self.runtime.messages(agent="researcher")
        self.assertEqual(len(filtered), 1)
        self.assertEqual(self.runtime.messages(conversation_id="missing").__len__(), 0)

    # -- tasks / jobs --------------------------------------------------------
    def test_task_events_read_from_kanban_db(self):
        events = self.runtime.task_events()
        self.assertEqual(events[0]["kind"], "claimed")

    def test_submit_task_tracks_job_and_cancel(self):
        proc = FakeProcess("orchestrator said hello", block=True)
        with patch.object(backend.Runtime, "find_hermes", staticmethod(lambda: "C:/hermes.cmd")), \
             patch.object(backend.subprocess, "Popen", return_value=proc):
            job = self.runtime.submit_task("hello orchestrator")
            self.assertEqual(job["status"], "running")
            proc.release()
            for _ in range(50):
                if self.runtime.job_list()[0]["status"] != "running":
                    break
                time.sleep(0.02)
            self.assertEqual(self.runtime.job_list()[0]["status"], "done")
            self.assertIn("orchestrator said hello", self.runtime.job_list()[0]["response"])
            blocked = FakeProcess(block=True)
            with patch.object(backend.subprocess, "Popen", return_value=blocked):
                second = self.runtime.submit_task("cancel me")
            cancelled = self.runtime.cancel_job(second["id"])
            self.assertEqual(cancelled["status"], "cancelled")
            blocked.release()

    def test_submit_task_rejects_empty(self):
        with self.assertRaises(backend.ToolError):
            self.runtime.submit_task("   ")

    def test_snapshot_shape(self):
        snap = self.runtime.snapshot()
        for key in ("health", "agents", "tasks", "events", "messages", "conversations", "jobs"):
            self.assertIn(key, snap)
        self.assertEqual(len(snap["agents"]), 13)

    # -- HTTP surface --------------------------------------------------------
    def test_http_roundtrip(self):
        backend.RUNTIME = self.runtime
        server = backend.ThreadingHTTPServer(("127.0.0.1", 0), backend.Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            import urllib.request

            def get(path):
                with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
                    return json.loads(r.read())

            def post(path, payload):
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}{path}",
                    data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    return json.loads(r.read())

            self.assertTrue(get("/api/health")["connected"])
            self.assertEqual(len(get("/api/agents")["agents"]), 13)
            sent = post("/api/messages", {"sender": "writer", "recipient": "researcher", "content": "via HTTP"})
            self.assertTrue(sent["message_id"])
            self.assertEqual(len(get("/api/messages")["messages"]), 1)
            self.assertEqual(len(get("/api/snapshot")["agents"]), 13)
            self.assertIn("<canvas", urllib.request.urlopen(
                f"http://127.0.0.1:{port}/", timeout=10).read().decode())
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
