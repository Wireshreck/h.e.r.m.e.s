# H.E.R.M.E.S. Desktop — Interactive Agent Canvas

A real desktop window (Electron) over the actual H.E.R.M.E.S. runtime. The
frontend never fabricates agents, messages or activity: every node, edge and
feed item is derived from the Hermes CLI, the kanban database and `comms.db`.

## Run

```sh
npm install
npm start
```

If npm 11 blocks install scripts (`install-scripts` warning), run
`npm install-scripts approve electron` first; if the binary is still missing,
`node node_modules/electron/install.js` or download the matching
`electron-v<version>-win32-x64.zip` from the Electron releases into
`node_modules/electron/dist/`.

Requires Python 3 on PATH (with PyYAML), Hermes Agent installed, and the fleet
bootstrapped (`python scripts/bootstrap_fleet.py`). Override with
`HERMES_DESKTOP_PYTHON` and `HERMES_DESKTOP_PORT` (default `8787`).

The Electron main process starts `backend.py`, waits for `/api/health`, then
opens the window. Closing the window terminates the backend (and its child
processes on Windows).

## Architecture

```
Electron window (main.js)  ── loads ──>  http://127.0.0.1:8787
                                            │
                                      Python backend
                                     (backend.py, stdlib)
                              ┌──────────────┼───────────────┐
                        Hermes CLI      kanban.db      comms.db via comms/
                     (orchestrator       (tasks,        (CommsService:
                      turns, list)       events)         send/ask/history)
```

- **Backend** is Python because the runtime and the communication system are
  Python. It reuses `comms/tools.py` directly — no duplicated message logic.
- **Shell** is Electron (a real window, not a browser tab). Frontend code has
  no model/provider keys and no direct SQLite access.
- The UI polls `/api/snapshot` every 2.5 s (near-real-time, bounded cost) and
  writes only through `/api/tasks`, `/api/messages`, `/api/ask`, `/api/jobs/*`.

## What the canvas shows

- Central orchestrator node + 13 agent nodes from `bots/fleet.yaml`.
- Hub edges and communication edges; an edge animates **only** for a message
  genuinely seen within the last 15 seconds.
- Status derived from real data: `working` (running kanban card), `failed`
  (blocked/last failure), `communicating` (pending comms), `waiting` (queued
  card), `idle`. No invented "offline"/activity states.
- Sparse ASCII glyph texture drawn on a canvas, stable under pan/zoom.
- Pan (drag empty space), wheel zoom, node drag (layout persists in
  localStorage), FIT / RESET, hover details, click-to-inspect with real
  actions (`MESSAGE` / `ASK` through the comms MCP service, `TASKS` filter).
- Bottom-centre command input: Enter sends a task to
  `hermes -p hermes-orchestrator chat -q … -Q --source desktop`, polls the job,
  renders the response, and supports CANCEL.
- Left-edge history drawer (Alt+H): orchestrator job history and kanban tasks,
  with search and context restore.
- Right-edge comms drawer (Alt+C): real messages from `comms.db`, filters
  (all/task/agent/conversation) and canvas highlighting on selection.
- Edge drawers reveal on cursor proximity with delays; Escape closes them;
  reduced-motion preferences disable transitions and the edge animation.

## API

| Route | Purpose |
|---|---|
| `GET /api/health` | Runtime / DB connectivity (never claims connected when not) |
| `GET /api/snapshot` | Cached combined state (agents, tasks, events, messages, conversations, jobs) |
| `GET /api/agents`, `/api/tasks`, `/api/messages`, `/api/conversations`, `/api/jobs` | Individual reads |
| `POST /api/tasks` | Submit a task to the orchestrator (returns job id) |
| `POST /api/jobs/<id>/cancel` | Cancel a running job |
| `POST /api/messages`, `/api/ask` | Send a message / ask an agent via `comms` |

Static files are served from `ui/`; all state is `Cache-Control: no-store`.

## Tests

```sh
python -m unittest discover -s desktop/tests -p 'test_*.py' -v   # 9 backend tests
npm test                                                        # 6 UI/syntax tests
```

Backend tests use a temporary `comms.db` and a patched CLI runner — no paid
API, no model call. An HTTP round-trip test exercises the real handler.

## Known limitations

- The runtime has no push subscription for comms events; the UI polls at 2.5 s.
- Orchestrator replies depend on the configured free model being available;
  provider 404s surface as job errors in the chat panel (not hidden).
- "Offline" agent status is deliberately not shown: profile liveness is not
  distinguishable from an idle agent with the runtime's public data.
- The separate Command Palette `Overlays/ResultOverlay` is preserved and not
  wired into this window; it remains a toast-style surface for palette tasks.
- A desktop installer/package is not produced here; run from the repo.
