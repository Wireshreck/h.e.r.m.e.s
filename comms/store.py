"""SQLite persistence for the H.E.R.M.E.S communication bus.

Follows the runtime's SQLite conventions: one DB in the Hermes home directory,
WAL journaling, busy timeout, foreign keys, forward-only numbered migrations.
All writes are serialized with a re-entrant lock; reads use the same connection
with `check_same_thread=False` because the MCP server may serve calls from
different worker threads.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from config import CommsConfig
from protocol import (
    AgentMessage,
    Conversation,
    DeliveryStatus,
    MessageType,
    content_fingerprint,
    new_id,
    now_ms,
)

_MIGRATIONS = Path(__file__).resolve().parent / "migrations"


class Store:
    def __init__(self, config: CommsConfig):
        self.config = config
        self.db_path = Path(config.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.db_path), timeout=10, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=10000")
            self._conn.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    # -- migrations ---------------------------------------------------------
    def migrate(self) -> list[int]:
        with self._lock:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                       version INTEGER PRIMARY KEY, applied_at_ms INTEGER NOT NULL)"""
            )
            applied = {int(r[0]) for r in self._conn.execute("SELECT version FROM schema_migrations")}
            ran: list[int] = []
            for path in sorted(_MIGRATIONS.glob("*.sql")):
                version = int(path.stem.split("_", 1)[0])
                if version in applied:
                    continue
                self._conn.executescript(path.read_text(encoding="utf-8"))
                self._conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at_ms) VALUES (?, ?)",
                    (version, now_ms()),
                )
                ran.append(version)
            return ran

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- conversations ------------------------------------------------------
    def ensure_conversation(
        self,
        participants: Iterable[str],
        *,
        topic: str = "",
        task_id: str | None = None,
        conversation_id: str | None = None,
    ) -> tuple[Conversation, bool]:
        """Return (conversation, created). Reuses an open task-scoped conversation
        with the same participant set so long tasks don't spawn endless threads."""
        parts = tuple(sorted(set(participants)))
        ts = now_ms()
        with self._lock:
            if conversation_id is None and task_id:
                row = self._conn.execute(
                    """SELECT c.* FROM conversations c
                       WHERE c.task_id = ? AND c.status = 'open'
                         AND c.message_count < ?
                       ORDER BY c.updated_at_ms DESC LIMIT 1""",
                    (task_id, self.config.max_messages_per_conversation),
                ).fetchone()
                if row is not None:
                    existing_parts = tuple(
                        r[0]
                        for r in self._conn.execute(
                            "SELECT agent_id FROM conversation_participants WHERE conversation_id = ? ORDER BY agent_id",
                            (row["id"],),
                        )
                    )
                    if set(existing_parts) == set(parts):
                        found = self.get_conversation(row["id"])
                        if found is not None:
                            return found, False

            cid = conversation_id or new_id("c")
            conv = Conversation(
                id=cid,
                participants=parts,
                topic=topic,
                task_id=task_id,
                created_at_ms=ts,
                updated_at_ms=ts,
            )
            conv.validate()
            self._conn.execute(
                """INSERT INTO conversations(id, topic, task_id, created_at_ms, updated_at_ms)
                   VALUES (?, ?, ?, ?, ?)""",
                (conv.id, conv.topic, conv.task_id, ts, ts),
            )
            self._conn.executemany(
                "INSERT INTO conversation_participants(conversation_id, agent_id, joined_at_ms) VALUES (?, ?, ?)",
                [(cid, a, ts) for a in parts],
            )
            return conv, True

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if row is None:
                return None
            parts = tuple(
                r[0]
                for r in self._conn.execute(
                    "SELECT agent_id FROM conversation_participants WHERE conversation_id = ? ORDER BY agent_id",
                    (conversation_id,),
                )
            )
            data = dict(row)
            data["participants"] = parts
            return Conversation.from_dict(data)

    def history(self, conversation_id: str, limit: int = 100) -> list[AgentMessage]:
        lim = min(max(int(limit), 1), self.config.max_messages_per_conversation)
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM messages WHERE conversation_id = ?
                   ORDER BY timestamp_ms DESC, id DESC LIMIT ?""",
                (conversation_id, lim),
            ).fetchall()
        return [self._message_from_row(r) for r in reversed(rows)]

    def list_conversations(self, agent: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        lim = min(max(int(limit), 1), 500)
        with self._lock:
            if agent:
                rows = self._conn.execute(
                    """SELECT c.* FROM conversations c
                       JOIN conversation_participants p ON p.conversation_id = c.id
                       WHERE p.agent_id = ? ORDER BY c.updated_at_ms DESC LIMIT ?""",
                    (agent, lim),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM conversations ORDER BY updated_at_ms DESC LIMIT ?", (lim,)
                ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["participants"] = [
                    x[0]
                    for x in self._conn.execute(
                        "SELECT agent_id FROM conversation_participants WHERE conversation_id = ? ORDER BY agent_id",
                        (r["id"],),
                    )
                ]
                out.append(d)
            return out

    # -- messages -----------------------------------------------------------
    def _message_from_row(self, row: sqlite3.Row) -> AgentMessage:
        return AgentMessage(
            id=row["id"],
            sender=row["sender"],
            recipients=tuple(
                r[0]
                for r in self._conn.execute(
                    "SELECT agent_id FROM message_recipients WHERE message_id = ? ORDER BY agent_id",
                    (row["id"],),
                )
            ),
            conversation_id=row["conversation_id"],
            type=MessageType(row["type"]),
            content=row["content"],
            timestamp_ms=int(row["timestamp_ms"]),
            priority=int(row["priority"]),
            task_id=row["task_id"],
            reply_to=row["reply_to"],
            status=DeliveryStatus(row["status"]),
            depth=int(row["depth"]),
            metadata=json.loads(row["metadata"] or "{}"),
        )

    def add_message(self, msg: AgentMessage) -> AgentMessage:
        """Persist a validated message atomically with its per-recipient rows."""
        msg.validate(self.config.max_content_bytes)
        fp = content_fingerprint(msg.sender, msg.content, msg.task_id)
        ts = msg.timestamp_ms or now_ms()
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(
                    """INSERT INTO messages(id, conversation_id, sender, task_id, type, content,
                                            timestamp_ms, priority, status, reply_to, depth, metadata, fingerprint)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        msg.id, msg.conversation_id, msg.sender, msg.task_id, msg.type.value,
                        msg.content, ts, msg.priority, msg.status.value, msg.reply_to, msg.depth,
                        json.dumps(msg.metadata, ensure_ascii=False), fp,
                    ),
                )
                self._conn.executemany(
                    """INSERT INTO message_recipients(message_id, agent_id, status)
                       VALUES (?, ?, 'pending')""",
                    [(msg.id, r) for r in msg.recipients],
                )
                self._conn.execute(
                    """UPDATE conversations
                       SET updated_at_ms = ?, message_count = message_count + 1
                       WHERE id = ?""",
                    (ts, msg.conversation_id),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return msg

    def inbox(
        self,
        agent: str,
        *,
        limit: int = 50,
        since_ms: int = 0,
        unread_only: bool = False,
    ) -> list[dict[str, Any]]:
        lim = min(max(int(limit), 1), 500)
        sql = """SELECT m.*, r.status AS recipient_status, r.delivered_at_ms, r.read_at_ms
                 FROM message_recipients r JOIN messages m ON m.id = r.message_id
                 WHERE r.agent_id = ? AND m.timestamp_ms >= ?"""
        params: list[Any] = [agent, int(since_ms)]
        if unread_only:
            sql += " AND r.status = 'delivered'"
        sql += " ORDER BY m.priority DESC, m.timestamp_ms DESC, m.id DESC LIMIT ?"
        params.append(lim)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            d = self._message_from_row(r).to_dict()
            d["recipient_status"] = r["recipient_status"]
            d["delivered_at_ms"] = r["delivered_at_ms"]
            d["read_at_ms"] = r["read_at_ms"]
            out.append(d)
        return out

    def mark(self, message_id: str, agent: str, status: DeliveryStatus, error: str | None = None) -> bool:
        ts = now_ms()
        with self._lock:
            if status is DeliveryStatus.DELIVERED:
                cur = self._conn.execute(
                    """UPDATE message_recipients
                       SET status='delivered', attempts=attempts+1, delivered_at_ms=?, last_error=?
                       WHERE message_id=? AND agent_id=?""",
                    (ts, error, message_id, agent),
                )
            elif status is DeliveryStatus.READ:
                cur = self._conn.execute(
                    """UPDATE message_recipients SET status='read', read_at_ms=?
                       WHERE message_id=? AND agent_id=?""",
                    (ts, message_id, agent),
                )
            else:
                cur = self._conn.execute(
                    """UPDATE message_recipients SET status=?, attempts=attempts+1, last_error=?
                       WHERE message_id=? AND agent_id=?""",
                    (status.value, error, message_id, agent),
                )
            if cur.rowcount:
                self._conn.execute(
                    """UPDATE messages SET status = CASE
                           WHEN EXISTS (SELECT 1 FROM message_recipients
                                        WHERE message_id=? AND status IN ('pending','failed')) THEN 'pending'
                           ELSE 'delivered' END WHERE id=?""",
                    (message_id, message_id),
                )
                return True
            return False

    def pending_for(self, agent: str, limit: int = 50) -> list[AgentMessage]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT m.* FROM message_recipients r JOIN messages m ON m.id = r.message_id
                   WHERE r.agent_id=? AND r.status IN ('pending','failed')
                   ORDER BY m.priority DESC, m.timestamp_ms ASC LIMIT ?""",
                (agent, min(max(limit, 1), 500)),
            ).fetchall()
            return [self._message_from_row(r) for r in rows]

    def find_reply(self, message_id: str, requester: str) -> AgentMessage | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM messages WHERE reply_to=? AND sender<>?
                   ORDER BY timestamp_ms ASC LIMIT 1""",
                (message_id, requester),
            ).fetchone()
            return self._message_from_row(row) if row else None

    # -- guards & housekeeping ---------------------------------------------
    def rate_limited(self, sender: str) -> bool:
        cutoff = now_ms() - 60_000
        with self._lock:
            n = self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE sender=? AND timestamp_ms>=?",
                (sender, cutoff),
            ).fetchone()[0]
        return int(n) >= self.config.rate_limit_per_minute

    def duplicate(self, msg: AgentMessage) -> bool:
        if self.config.duplicate_window_seconds <= 0:
            return False
        cutoff = now_ms() - self.config.duplicate_window_seconds * 1000
        fp = content_fingerprint(msg.sender, msg.content, msg.task_id)
        with self._lock:
            n = self._conn.execute(
                """SELECT COUNT(*) FROM messages
                   WHERE fingerprint=? AND sender=? AND timestamp_ms>=?""",
                (fp, msg.sender, cutoff),
            ).fetchone()[0]
        return int(n) > 0

    def conversation_full(self, conversation_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT message_count FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone()
        return row is None or int(row[0]) >= self.config.max_messages_per_conversation

    def prune(self) -> int:
        """Delete expired/idle conversations and enforce the global cap.
        Returns the number of conversations removed."""
        now = now_ms()
        ttl_cutoff = now - self.config.conversation_ttl_days * 86_400_000
        idle_cutoff = now - self.config.conversation_idle_days * 86_400_000
        with self._lock:
            cur = self._conn.execute(
                """DELETE FROM conversations
                   WHERE created_at_ms < ? OR updated_at_ms < ?""",
                (ttl_cutoff, idle_cutoff),
            )
            removed = cur.rowcount
            count = self._conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            if count > self.config.max_conversations:
                excess = count - self.config.max_conversations
                self._conn.execute(
                    """DELETE FROM conversations WHERE id IN (
                           SELECT id FROM conversations ORDER BY updated_at_ms ASC LIMIT ?)""",
                    (excess,),
                )
                removed += excess
            return int(removed)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            def one(sql: str) -> int:
                return int(self._conn.execute(sql).fetchone()[0])

            return {
                "db_path": str(self.db_path),
                "conversations": one("SELECT COUNT(*) FROM conversations"),
                "messages": one("SELECT COUNT(*) FROM messages"),
                "pending": one("SELECT COUNT(*) FROM message_recipients WHERE status='pending'"),
                "failed": one("SELECT COUNT(*) FROM message_recipients WHERE status='failed'"),
                "schema_version": one("SELECT MAX(version) FROM schema_migrations"),
            }
