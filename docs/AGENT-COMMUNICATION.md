# Agent-to-agent communication (H.E.R.M.E.S.)

This document describes the communication bus that lets the 13 fleet bots
message each other, ask for help, share findings, coordinate on tasks and
delegate work — without hardcoded peer-to-peer links.

## Why this shape

The real agent execution lifecycle lives in **Hermes Agent** (Python), not in
the thin Rust CLI in `.hermes-client/`. The bus therefore plugs into the
runtime through its **MCP server mechanism** — the same mechanism already used
for the REA binary-analysis server. Every profile's `config.yaml` gets an
`mcp_servers.hermes-comms` entry from `bots/fleet.yaml`, so all ten comms tools
appear to the model as `mcp__hermes_comms__<tool>`. Nothing in the upstream
agent installation is modified.

```
 agent A turn ── MCP tool call ──► comms server (stdio JSON-RPC)
                                      │  typed protocol + guards
                                      ▼
                                comms.db (SQLite/WAL)
                                      │  delivery step
                                      ▼
                       hermes -p B chat -q ... -Q --source comms
                                      │
                                      ▼
                       agent B real turn (can call reply_to_message)
```

`hermes -p <profile> chat -q ... -Q` is the runtime's native one-shot turn
entry point: the recipient actually processes the message as an agent turn.
If injection is unavailable, delivery is recorded as failed and retried via
`pending_messages`; the message is always durably in the recipient's inbox.

## Architecture

| Module | Responsibility |
|---|---|
| `comms/protocol.py` | Typed `AgentMessage` / `Conversation`, JSON wire format, validation, ids, fingerprinting |
| `comms/store.py` | SQLite/WAL persistence, numbered migrations, inbox/history queries, guards |
| `comms/delivery.py` | Pluggable delivery: native agent turn (default), generic CLI, inbox-only |
| `comms/registry.py` | Fleet discovery from `bots/fleet.yaml`, capability scoring, live status |
| `comms/tools.py` | The service implementation behind every MCP tool |
| `comms/server.py` | Zero-dependency MCP stdio server (JSON-RPC 2.0) |
| `comms/migrations/` | Forward-only schema migrations |
| `comms/tests/` | Unit + MCP-client integration tests (no paid APIs) |

## Message protocol

Every message carries: `id` (`m_…`), `sender`, `recipients[]`,
`conversation_id` (`c_…`), optional `task_id`, `type`, `content`,
`timestamp_ms`, `priority` (0–3), `status`, `reply_to`, `depth` and free-form
`metadata`. Types: `request`, `response`, `informational`, `delegation`,
`event`, `acknowledgement`. Delivery states: `pending`, `delivered`, `read`,
`failed`, `expired`.

Validation rejects empty/oversized content, malformed ids, invalid types and
bad priorities before anything is written. A SHA-256 fingerprint of
`(sender, content, task)` powers duplicate detection.

## Tools available to agents

After `python scripts/bootstrap_fleet.py`, each bot exposes these MCP tools
(prefixed at runtime with `mcp__hermes_comms__`):

| Tool | Purpose |
|---|---|
| `send_message` | Direct message to one agent (delivered + persisted) |
| `broadcast_message` | To a list, comma-separated ids, or `all` (inbox delivery only) |
| `ask_agent` | Request + bounded wait for a correlated reply |
| `reply_to_message` | Reply to an incoming message (auto-correlated, depth incremented) |
| `get_inbox` | Retrieve incoming messages (marks read by default) |
| `get_conversation` | Read persisted history of a conversation |
| `get_agent_status` | Another agent's known status + pending inbox size |
| `list_agents` | Discover agents by id, role, description, capability (fuzzy) |
| `delegate_task` | Delegate via the **existing kanban dispatcher** (`hermes kanban create --assignee`) |
| `pending_messages` | Retry failed/pending deliveries |

## How agents initiate conversations

1. The model calls `list_agents(query="...")` — no need to know profile ids.
2. It calls `send_message` or `ask_agent`. The bus creates or reuses a
   task-scoped conversation (same `task_id` + same participants ⇒ reuse).
3. Delivery starts a real one-shot turn in the recipient profile. The injected
   text includes the message id and, for requests, an instruction to answer
   with `reply_to_message`.
4. The recipient answers with `reply_to_message`; the request's `ask_agent`
   call returns as soon as the correlated response is found (or times out,
   leaving the request in the inbox for later).
5. Any participant can read the full history with `get_conversation` or
   continue with `reply_to_message` up to the configured hop limit.

Messages from another agent are **untrusted input**: they are delivered as
ordinary text into the recipient's own session with the recipient's own
toolset. No tool privilege, provider or routing changes are inherited from
the sender. Model selection stays with each profile's configured free model
and fallback chain.

## Persistence

`comms.db` lives in the Hermes home directory (`%LOCALAPPDATA%\hermes\comms.db`
on Windows, `~/.hermes/comms.db` elsewhere) with WAL, `synchronous=NORMAL`,
10 s busy timeout and foreign keys. Tables: `conversations`,
`conversation_participants`, `messages`, `message_recipients`,
`schema_migrations`; indexes cover conversation/time, sender/time, task,
reply-to, fingerprint and recipient status. Applied migrations are tracked by
version and are forward-only. A dedicated DB is used so upstream `state.db`
and `kanban.db` are never structurally altered.

## Configuration and limits

Environment variables (read at server startup; defaults shown):

| Variable | Default | Meaning |
|---|---|---|
| `HERMES_COMMS_DB` | `<hermes home>/comms.db` | Database path |
| `HERMES_COMMS_DELIVERY` | `auto` | `auto` (turn → CLI → inbox), `turn`, `cli`, `inbox` |
| `HERMES_COMMS_DELIVERY_CMD` | `hermes peer dm {agent} {message}` | CLI template for `cli` mode |
| `HERMES_COMMS_MAX_BYTES` | 16384 | Max message content size |
| `HERMES_COMMS_MAX_MESSAGES` | 500 | Max messages per conversation |
| `HERMES_COMMS_MAX_CONVERSATIONS` | 2000 | Global conversation cap |
| `HERMES_COMMS_TTL_DAYS` / `IDLE_DAYS` | 30 / 7 | Conversation expiry |
| `HERMES_COMMS_MAX_DEPTH` | 6 | Agent-to-agent hop limit |
| `HERMES_COMMS_RATE_LIMIT` | 30 | Messages per sender per minute |
| `HERMES_COMMS_DEDUPE_SECONDS` | 60 | Duplicate-message window |
| `HERMES_COMMS_TIMEOUT` | 30 | Max `ask_agent` wait (seconds) |
| `HERMES_COMMS_STRICT_DELIVERY` | false | Don't fall back to inbox on failed injection |

Guards: self-message rejection, rate limit, duplicate fingerprint, depth cap,
conversation cap, broadcast recipient cap (50), bounded waits, delivery
timeouts and automatic pruning. Failures are logged and recorded per
recipient; they never crash the caller's turn.

## Logging

Structured JSON lines go to stderr (stdout is reserved for MCP): events
`message_created`, `message_delivered`, `message_delivery_failed`,
`delivery_fallback`, `request_answered`, `request_timeout`,
`delegation_kanban`, including message/conversation/task/agent ids. Content is
not logged.

## Testing

```sh
python -m unittest discover -s comms/tests -v
```

26 tests cover protocol validation/persistence, task-scoped reuse, concurrent
writes, migrations, discovery, direct/broadcast messaging, request-response,
depth/loop guards, rate limits, duplicates, delivery failure + retry, and a
real MCP stdio round-trip using the official `mcp` client when installed. No
paid API or live model call is needed.

## Applying / repairing

```sh
python scripts/bootstrap_fleet.py            # registers hermes-comms on all 13 profiles
python scripts/bootstrap_fleet.py --verify-only
```

`bots/fleet.yaml` → `mcp_base.hermes-comms` is the source of truth; the
`@REPO@` token is expanded to this checkout's absolute path by the bootstrap,
so the config is portable across machines.
