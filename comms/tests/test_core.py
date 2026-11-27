"""Unit tests for the communication protocol and SQLite store.

No network, no paid APIs, no Hermes installation required: run with
    python -m unittest discover -s comms/tests -v
"""

from __future__ import annotations

import concurrent.futures
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import CommsConfig  # noqa: E402
from protocol import (  # noqa: E402
    AgentMessage,
    Conversation,
    DeliveryStatus,
    MessageType,
    content_fingerprint,
    new_id,
    now_ms,
)
from store import Store  # noqa: E402


def make_message(sender: str, recipients, conversation_id: str, **kwargs) -> AgentMessage:
    return AgentMessage(
        id=new_id("m"),
        sender=sender,
        recipients=tuple(recipients),
        conversation_id=conversation_id,
        type=kwargs.pop("type", MessageType.INFORMATIONAL),
        content=kwargs.pop("content", "test message"),
        timestamp_ms=kwargs.pop("timestamp_ms", now_ms()),
        **kwargs,
    )


class StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.config = CommsConfig(
            db_path=Path(self.tmp.name) / "comms.db",
            max_content_bytes=1024,
            rate_limit_per_minute=100,
        )
        self.store = Store(self.config)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def new_conversation(self, participants, **kwargs):
        return self.store.ensure_conversation(participants, **kwargs)[0]

    # -- protocol -----------------------------------------------------------
    def test_message_validation(self):
        conv = self.new_conversation(["writer"])
        msg = make_message("writer", ["writer"], conv.id)
        msg.validate(self.config.max_content_bytes)
        with self.assertRaises(ValueError):
            make_message("bad id!", ["writer"], conv.id).validate(self.config.max_content_bytes)
        with self.assertRaises(ValueError):
            make_message("writer", [], conv.id).validate(self.config.max_content_bytes)
        with self.assertRaises(ValueError):
            make_message("writer", ["writer"], conv.id, content="x" * 2000).validate(self.config.max_content_bytes)
        with self.assertRaises(ValueError):
            make_message("writer", ["writer"], conv.id, priority=99).validate(self.config.max_content_bytes)

    def test_message_json_roundtrip(self):
        conv = self.new_conversation(["writer", "reviewer"])
        msg = make_message("writer", ["reviewer"], conv.id, type=MessageType.REQUEST,
                           task_id="t_1", metadata={"hops": 2})
        restored = AgentMessage.from_json(msg.to_json())
        self.assertEqual(restored, msg)
        with self.assertRaises(ValueError):
            AgentMessage.from_json("not json")

    def test_fingerprint_is_stable_and_content_sensitive(self):
        a = content_fingerprint("Writer", "Hello!", "t_1")
        b = content_fingerprint("writer", "hello!", "t_1")
        c = content_fingerprint("writer", "Goodbye", "t_1")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    # -- persistence --------------------------------------------------------
    def test_message_persists_and_inbox_delivery(self):
        conv = self.new_conversation(["writer", "researcher"], task_id="t_1")
        msg = make_message("writer", ["researcher"], conv.id, task_id="t_1")
        self.store.add_message(msg)
        inbox = self.store.inbox("researcher")
        self.assertEqual(len(inbox), 1)
        self.assertEqual(inbox[0]["id"], msg.id)
        self.assertEqual(inbox[0]["recipient_status"], DeliveryStatus.PENDING.value)
        self.assertTrue(self.store.mark(msg.id, "researcher", DeliveryStatus.DELIVERED))
        self.assertEqual(self.store.inbox("researcher", unread_only=True)[0]["id"], msg.id)
        self.assertTrue(self.store.mark(msg.id, "researcher", DeliveryStatus.READ))
        self.assertEqual(self.store.inbox("researcher", unread_only=True), [])

    def test_history_is_chronological_and_bounded(self):
        conv = self.new_conversation(["a", "b"])
        for i in range(10):
            self.store.add_message(make_message("a", ["b"], conv.id,
                                                timestamp_ms=1000 + i,
                                                content=f"message {i}"))
        history = self.store.history(conv.id, limit=5)
        self.assertEqual(len(history), 5)
        self.assertEqual([m.content for m in history], [f"message {i}" for i in range(5, 10)])

    def test_task_scoped_conversation_is_reused(self):
        c1, created1 = self.store.ensure_conversation(["a", "b"], task_id="t_42", topic="first")
        c2, created2 = self.store.ensure_conversation(["b", "a"], task_id="t_42", topic="first")
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(c1.id, c2.id)
        c3, created3 = self.store.ensure_conversation(["a", "c"], task_id="t_42")
        self.assertTrue(created3)
        self.assertNotEqual(c1.id, c3.id)

    def test_request_response_correlation(self):
        conv = self.new_conversation(["a", "b"])
        request = make_message("a", ["b"], conv.id, type=MessageType.REQUEST)
        self.store.add_message(request)
        reply = make_message("b", ["a"], conv.id, type=MessageType.RESPONSE,
                             reply_to=request.id, content="answer")
        self.store.add_message(reply)
        found = self.store.find_reply(request.id, "a")
        self.assertIsNotNone(found)
        self.assertEqual(found.id, reply.id)

    def test_migrations_are_idempotent(self):
        self.assertEqual(self.store.migrate(), [])
        self.assertEqual(self.store.stats()["schema_version"], 1)

    def test_prune_removes_idle_conversations(self):
        conv = self.new_conversation(["a"])
        self.store.add_message(make_message("a", ["a"], conv.id))
        self.store._conn.execute(
            "UPDATE conversations SET updated_at_ms = 1 WHERE id = ?", (conv.id,)
        )
        self.store._conn.execute(
            "UPDATE conversations SET created_at_ms = 1 WHERE id = ?", (conv.id,)
        )
        self.assertGreaterEqual(self.store.prune(), 1)
        self.assertIsNone(self.store.get_conversation(conv.id))

    # -- guards -------------------------------------------------------------
    def test_rate_limit_and_duplicate_detection(self):
        conv = self.new_conversation(["a", "b"])
        msg = make_message("a", ["b"], conv.id)
        self.store.add_message(msg)
        self.assertTrue(self.store.duplicate(msg))
        self.assertFalse(self.store.duplicate(make_message("a", ["b"], conv.id, content="different")))
        tight = CommsConfig(db_path=self.config.db_path, rate_limit_per_minute=1)
        limited_store = Store(tight)
        try:
            self.assertTrue(limited_store.rate_limited("a"))
        finally:
            limited_store.close()

    def test_conversation_limit(self):
        conv = self.new_conversation(["a", "b"])
        for _ in range(self.config.max_messages_per_conversation):
            self.store.add_message(make_message("a", ["b"], conv.id))
        self.assertTrue(self.store.conversation_full(conv.id))

    def test_concurrent_writes(self):
        conv = self.new_conversation(["a", "b"])

        def send(i: int) -> None:
            self.store.add_message(make_message("a", ["b"], conv.id,
                                                content=f"concurrent {i}", timestamp_ms=2000 + i))

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(send, range(40)))
        self.assertEqual(self.store.stats()["messages"], 40)
        self.assertEqual(len(self.store.history(conv.id, limit=100)), 40)


if __name__ == "__main__":
    unittest.main()
