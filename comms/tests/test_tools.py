"""Tests for the agent-facing communication tools and MCP server.

Delivery is mocked so these tests never spawn a model turn or use a paid API.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import CommsConfig  # noqa: E402
from delivery import DeliveryResult  # noqa: E402
from protocol import DeliveryStatus, MessageType, new_id, now_ms  # noqa: E402
from registry import find_agents, get_agent, load_agents  # noqa: E402
from store import Store  # noqa: E402
from tools import CommsService, ToolError  # noqa: E402


class FakeDelivery:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def send(self, message, agent, broadcast: bool = False) -> DeliveryResult:
        self.calls.append((message.id, agent))
        if self.fail:
            return DeliveryResult(False, "fake", "simulated failure")
        return DeliveryResult(True, "fake")


class ToolsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.config = CommsConfig(
            db_path=Path(self.tmp.name) / "comms.db",
            max_content_bytes=4096,
            rate_limit_per_minute=100_000,
            request_timeout_seconds=3,
        )
        self.store = Store(self.config)
        self.delivery = FakeDelivery()
        self.service = CommsService(self.config, self.store, self.delivery)

    def tearDown(self) -> None:
        self.service.close()
        self.store.close()
        self.tmp.cleanup()

    # -- direct messaging ---------------------------------------------------
    def test_direct_message_delivery_and_history(self):
        out = self.service.send_message("writer", "researcher", "Please verify X", task_id="t_1")
        self.assertEqual(out["status"], "delivered")
        self.assertEqual(len(self.delivery.calls), 1)
        conv = self.service.get_conversation(out["conversation_id"], agent="researcher")
        self.assertEqual(conv["count"], 1)
        self.assertEqual(conv["messages"][0]["content"], "Please verify X")
        self.assertEqual(conv["conversation"]["task_id"], "t_1")

    def test_send_rejects_invalid_input_and_self_loop(self):
        with self.assertRaises(ToolError):
            self.service.send_message("writer", "writer", "self talk")
        with self.assertRaises(ToolError):
            self.service.send_message("writer", "researcher", "x", type="not-a-type")
        with self.assertRaises(ToolError):
            self.service.send_message("writer", "researcher", "x" * 10_000)

    def test_broadcast_to_all_and_to_list(self):
        out = self.service.broadcast_message("writer", "all", "FYI")
        self.assertEqual(len(out["recipients"]), len(load_agents()) - 1)
        self.assertEqual(len(self.delivery.calls), len(out["recipients"]))
        out2 = self.service.broadcast_message("writer", ["researcher", "tester"], "FYI 2")
        self.assertEqual(out2["recipients"], ["researcher", "tester"])
        with self.assertRaises(ToolError):
            self.service.broadcast_message("writer", [], "nobody")

    # -- discovery ----------------------------------------------------------
    def test_agent_discovery(self):
        results = find_agents("security threats", limit=3)
        self.assertTrue(results)
        self.assertTrue(any(a.id == "security" for a in results))
        self.assertIsNotNone(get_agent("hermes-orchestrator"))
        out = self.service.list_agents("binary analysis")
        self.assertGreater(out["count"], 0)
        self.assertIn("status", out["agents"][0])

    # -- request/response ---------------------------------------------------
    def test_ask_agent_receives_reply(self):
        def answer() -> None:
            time.sleep(0.3)
            # find the request and reply through the same service
            with self.store._lock:
                row = self.store._conn.execute(
                    "SELECT id FROM messages WHERE type='request' ORDER BY timestamp_ms DESC LIMIT 1"
                ).fetchone()
            self.service.reply_to_message("researcher", row["id"], "Here is the answer")

        t = threading.Thread(target=answer)
        t.start()
        out = self.service.ask_agent("writer", "researcher", "What is X?", timeout_seconds=3)
        t.join()
        self.assertTrue(out["answered"])
        self.assertEqual(out["content"], "Here is the answer")
        self.assertEqual(out["from"], "researcher")

    def test_ask_agent_timeout_is_bounded(self):
        start = time.monotonic()
        out = self.service.ask_agent("writer", "researcher", "status?", timeout_seconds=1)
        self.assertFalse(out["answered"])
        self.assertLess(time.monotonic() - start, 3)

    def test_reply_depth_and_loop_guard(self):
        sent = self.service.send_message("writer", "researcher", "question")
        reply = self.service.reply_to_message("researcher", sent["message_id"], "answer")
        self.assertEqual(reply["depth"], 1)
        # A reply to the reply raises depth again; fabricate a deep chain via
        # _send with the configured cap to prove the guard fires.
        conv = sent["conversation_id"]
        with self.assertRaises(ToolError):
            self.service._send(
                "writer", ("researcher",), "too deep", MessageType.RESPONSE,
                conversation_id=conv, depth=self.config.max_depth,
            )

    def test_only_recipient_can_reply(self):
        sent = self.service.send_message("writer", "researcher", "question")
        with self.assertRaises(ToolError):
            self.service.reply_to_message("designer", sent["message_id"], "not mine")

    # -- guards & failures --------------------------------------------------
    def test_duplicate_and_rate_limit(self):
        self.service.send_message("writer", "researcher", "same content", task_id="t_dup")
        with self.assertRaises(ToolError):
            self.service.send_message("writer", "researcher", "same content", task_id="t_dup")
        tight = CommsConfig(db_path=self.config.db_path, rate_limit_per_minute=1)
        limited_store = Store(tight)
        limited = CommsService(tight, limited_store, FakeDelivery())
        try:
            with self.assertRaises(ToolError):
                limited.send_message("writer", "researcher", "first")
        finally:
            limited_store.close()

    def test_delivery_failure_is_recorded_and_retried(self):
        failing = FakeDelivery(fail=True)
        svc = CommsService(self.config, self.store, failing)
        out = svc.send_message("writer", "researcher", "will fail")
        row = self.store._conn.execute(
            "SELECT status FROM message_recipients WHERE message_id=?", (out["message_id"],)
        ).fetchone()
        self.assertEqual(row["status"], DeliveryStatus.FAILED.value)
        failing.fail = False
        retried = svc.pending_messages("researcher")
        self.assertEqual(retried["attempted"], 1)
        self.assertTrue(retried["results"][0]["delivered"])
        row = self.store._conn.execute(
            "SELECT status FROM message_recipients WHERE message_id=?", (out["message_id"],)
        ).fetchone()
        self.assertEqual(row["status"], DeliveryStatus.DELIVERED.value)

    def test_concurrent_messages_from_multiple_agents(self):
        def send(i: int):
            sender = "writer" if i % 2 == 0 else "researcher"
            recipient = "researcher" if i % 2 == 0 else "writer"
            return self.service.send_message(sender, recipient, f"msg {i}", task_id=None)

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(send, range(20)))
        self.assertEqual(len({r["message_id"] for r in results}), 20)
        self.assertEqual(self.store.stats()["messages"], 20)

    def test_get_inbox_marks_read(self):
        self.service.send_message("writer", "researcher", "read me")
        msgs = self.service.get_inbox("researcher")["messages"]
        self.assertEqual(len(msgs), 1)
        self.assertEqual(self.service.get_inbox("researcher", unread_only=True)["count"], 0)

    def test_acknowledgement_type_is_supported(self):
        out = self.service.send_message("writer", "researcher", "noted", type="acknowledgement")
        self.assertEqual(out["status"], "delivered")


@unittest.skipUnless(
    __import__("importlib").util.find_spec("mcp") is not None,
    "official MCP SDK not installed (skip protocol client test)",
)
class McpProtocolTestCase(unittest.TestCase):
    """Spawn the real stdio server and drive it with the official MCP client."""

    def test_mcp_client_roundtrip(self):
        import asyncio

        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        import uuid

        server = str(Path(__file__).resolve().parents[1] / "server.py")
        db = str(Path(tempfile.gettempdir()) / f"comms_mcp_{uuid.uuid4().hex}.db")
        env = {**os.environ, "HERMES_COMMS_DB": db, "HERMES_COMMS_DELIVERY": "inbox"}

        async def run() -> dict:
            params = StdioServerParameters(command=sys.executable, args=[server], env=env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    sent = await session.call_tool(
                        "send_message",
                        {"sender": "writer", "recipient": "researcher", "content": "via MCP"},
                    )
                    inbox = await session.call_tool("get_inbox", {"agent": "researcher"})
                    return {
                        "tools": [t.name for t in listed.tools],
                        "sent": json.loads(sent.content[0].text),
                        "inbox": json.loads(inbox.content[0].text),
                    }

        out = asyncio.run(run())
        self.assertIn("send_message", out["tools"])
        self.assertEqual(len(out["tools"]), 10)
        self.assertEqual(out["inbox"]["count"], 1)
        self.assertEqual(out["inbox"]["messages"][0]["id"], out["sent"]["message_id"])
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(db + suffix)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
