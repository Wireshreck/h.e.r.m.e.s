# H.E.R.M.E.S

> **H**ierarchical **E**nsemble of **R**ole-specialized **M**ulti-agent **E**xperts **S**tack —
> a fleet of 13 specialist AI bots built on the [Hermes Agent](https://hermes-agent.nousresearch.com)
> profiles + kanban orchestration board. **Free models only.**

---

```
                         ┌──────────────────────────────┐
        goal arrives ──► │   HERMES (Main Orchestrator)  │
                         └──────────────┬───────────────┘
                                        │ decomposes → creates cards → links dependencies
                 ┌──────────────────────┼──────────────────────────┐
                 │              KANBAN BOARD (dispatcher)              │
                 │   triage → todo → ready ⚡ running (parallel) → done │
                 └──┬────┬────┬────┬────┬────┬────┬────┬────┬──────────┘
                    │    │    │    │    │    │    │    │    │
   ┌────────────┬────▼─┬──▼──┬─▼───┬▼────▼┬───▼┬───▼┬───▼┬───▼──┬──────────┐
   │ Researcher │Analyst│Sec.│Arch.│ Devel │Test│Rev.│Dops│ R.E. │Writer    │...
   │  (facts)   │ (data)│(threats)│(design)│(code)│(QA)│(PR)│(CI/CD)│(binaries)│(docs)  │
   └────────────┴───────┴────┴────┴──────┴────┴────┴────┴──────┴──────────┘
                              + Designer (UI/UX) + Automater (scripts & integrations)
```

Every bot is an isolated Hermes Agent **profile**: its own model, its own `.env`, its own `SOUL.md`
persona, its own sessions and memory. The orchestrator fans work out on a durable **kanban board**
(`kanban_create` / `kanban_link`), the board's dispatcher claims cards **atomically** and spawns each
specialist as a **full OS worker process**, so independent cards run **concurrently** by design.

## Fleet roster

| # | Bot id | Role | Provider | Free model |
|---|--------|------|----------|------------|
| 0 | `hermes-orchestrator` | Main Orchestrator | OpenRouter | `nvidia/nemotron-3-ultra-550b-a55b:free` |
| 1 | `researcher` | Research & fact-checking | Google AI Studio | `gemini-3.8-flash` |
| 2 | `analyst` | Data analysis & statistics | Google AI Studio | `gemini-3.1-pro-preview` |
| 3 | `security` | Cybersecurity & auditing | OpenRouter | `nvidia/nemotron-3-ultra-550b-a55b:free` |
| 4 | `architect` | System design & planning | OpenRouter | `thinkingmachines/inkling:free` |
| 5 | `developer` | Coding & bug fixing | OpenRouter | `poolside/laguna-s-2.1:free` |
| 6 | `tester` | Testing & QA | OpenRouter | `poolside/laguna-xs-2.1:free` |
| 7 | `reviewer` | Code review | OpenRouter | `poolside/laguna-s-2.1:free` |
| 8 | `devops` | Deployment & infrastructure | Google AI Studio | `gemini-3.5-flash` |
| 9 | `reverse-engineer` | Binary analysis & debugging | OpenRouter | `thinkingmachines/inkling:free` |
| 10 | `writer` | Documentation | Google AI Studio | `gemini-3.8-flash` |
| 11 | `designer` | UI/UX & frontend design | Google AI Studio | `gemini-3.7-flash` |
| 12 | `automater` | Scripts & integrations | OpenRouter | `poolside/laguna-xs-2.1:free` |

Every primary model is a genuinely free tier:

- **OpenRouter `:free` models** — 550B-class Nemotron Ultra (planning/secdev), Thinking Machines'
  Inkling (deep reasoning/design), Poolside Laguna S/XS (code-native models for the coding crew).
- **Google AI Studio free tier** — Gemini Flash family (research, docs, UI, CI/CD) and Pro-class
  reasoning for the analyst.
- **Local Ollama fallbacks** on every coding/ops bot (`qwen3:4b-32k`, `qwen3:8b-lowmem`) so work
  keeps moving — at zero cost and zero quota — when a free remote tier is rate-limited.

Each bot also carries a **fallback chain** (`fallback_providers` in its profile config), tried
automatically when the primary hits rate-limit/overload/connection errors.

## Tools: full computer access, per-role extras

Every bot runs with a **common base of 16 toolsets** — full computer access as the logged-in user —
plus role-specific extras on top. The declarative source of truth is `bots/fleet.yaml` (`tools:`).

| Toolset | What the bots can do with it |
|---------|------------------------------|
| `terminal` | shell commands + process management, run as the normal user (`approvals.mode: off`) |
| `file` | read / write / patch / search files anywhere on the user filesystem |
| `browser` | browser automation: navigate, click, type, screenshots |
| `code_execution` | sandboxed Python with tool RPC (pandas, scripts, quick maths) |
| `vision` | analyze images & screenshots (`vision_analyze`) |
| `computer_use` | **GUI automation via cua-driver**: screen capture, mouse, keyboard, window management |
| `web`, `tts`, `skills`, `todo`, `memory`, `session_search`, `connections`, `clarify`, `delegation`, `cronjob` | supporting layer (search, speech, planning, recall, scheduling….) |

Role-specific capability goes beyond the base:

- **`kanban` toolset** → orchestrator only (`kanban_create` / `kanban_link` / `kanban_watch`). Worker
  bots get `kanban_*` verbs automatically via `HERMES_KANBAN_TASK`, so specialists must NOT carry the
  toolset.
- **REA MCP server** (`mcp_servers.rea`) → `security` + `reverse-engineer` only: 229 binary-analysis
  tools (disassembly, managed/native code, debugger sessions, browser page inspection).
- **`image_gen` is disabled fleet-wide** — no image-provider key (`FAL_KEY` / `DEEPINFRA_API_KEY`)
  is configured. Add a key, then `hermes -p <bot> tools enable image_gen` to switch a bot back on.

Verification / doctoring:

```sh
hermes -p developer tools list          # 16 enabled incl. computer_use; image_gen disabled
hermes -p reverse-engineer tools list   # tail shows "MCP servers: rea — all tools enabled"
hermes computer-use status              # cua-driver binary + runtime contract
hermes computer-use doctor              # UIAutomation + screen-capture check matrix
```

## How the fleet works

- **Routing** — every profile has a one-paragraph `description`; the kanban **decomposer** reads the
  roster and routes decomposed child tasks to the best-fit specialist by that description.
- **Concurrency** — cards with no dependency link run in parallel (`kanban.max_in_progress` unset
  = unlimited). Long operations must `kanban_heartbeat` at least hourly.
- **Handoffs** — `kanban_complete(summary, metadata)` is the machine-readable contract between bots
  (`changed_files`, `verification`, `residual_risk`). A child card created with `parents=[...]`
  inherits its parents' completion summaries when it wakes up.
- **Personas** — `bots/souls/*.md` are deployed to each profile's `SOUL.md`: identity + output
  contract for every specialist. `AGENTS.md` in this repo is auto-injected into any bot working
  in this directory.

## Quick start

Prereqs: [Hermes Agent](https://hermes-agent.nousresearch.com) installed with the two free
providers connected — `OPENROUTER_API_KEY` and `GOOGLE_API_KEY` (check with `hermes auth list`).

```sh
# Clone, then build the fleet (idempotent — safe to re-run):
python scripts/bootstrap_fleet.py                    # 13 bot profiles
python scripts/bootstrap_fleet.py --with-board       # + kanban orchestration board

# Inspect the fleet:
python scripts/bootstrap_fleet.py --verify-only
hermes profile list
```

## Using the fleet

**Human drives, fleet sails.**

```sh
hermes kanban init                                  # one-time (idempotent)
hermes gateway start                                # dispatcher ticks every 60s

# Hand a card to a specialist:
hermes kanban create "Research competitor pricing for feature X" --assignee researcher

# Or talk to the orchestrator and let HERMES decompose:
hermes -p hermes-orchestrator chat
> Ship a docs site for the repo: architect a plan, implement, test, review, publish.

# Watch the fleet:
hermes kanban watch
hermes dashboard            # browser board: https://localhost:9119 → Kanban tab
```

Drop a one-liner goal into **triage** and the built-in decomposer fans it out over the fleet
automatically (routes by profile description). For a graph in one shot:

```sh
hermes kanban swarm "Design a multi-region failover plan" \
    --workers researcher,architect,security \
    --verifier reviewer --synthesizer writer
```

## Concurrency, retries, and safety

- **One card = one worker process.** Workers are spawned per card by the dispatcher; N ready cards
  → N concurrent workers. Per-card retry budgets (`--max-retries`, default `kanban.failure_limit=2`)
  and a respawn guard (rate-limit cooldown, PR-active guard) prevent worker storms.
- **Rate-limit resilience** — worker exit on 429/5xx/timeouts requeues the card (exit 75) without
  counting a failure; a terminal provider error blocks the card on first occurrence so a broken
  credential never thrashes.
- **Git discipline** — cards default to an isolated git **worktree** in this repo; push through a
  reviewed PR (`--completion-contract OWNER/REPO` binds a card to real PR checks). Workers never
  force-push; `default_workdir` keeps the shared checkout clean.

## Repository layout

```
h.e.r.m.e.s/
├── powertoys/
│   └── cmdpal-hermes/       # Windows Command Palette extension: type a task → H.E.R.M.E.S triage
│       └── HermesCmdPalExtension/   # C# MSIX COM server (.NET 10 + cmdpal SDK 0.11)
├── bots/
│   ├── fleet.yaml          # declarative roster: models, fallbacks, toolsets, MCP servers
│   └── souls/              # per-bot persona (identity + output contract) → profile SOUL.md
├── scripts/
│   └── bootstrap_fleet.py  # idempotent installer / verifier for the whole stack
├── docs/WORKFLOWS.md       # copy-paste fleet recipes (fan-out, pipeline, swarm, review loop)
├── AGENTS.md               # auto-injected workspace protocol for any bot working here
└── README.md
```

## Status check

```sh
python scripts/bootstrap_fleet.py --verify-only   # roster state
hermes kanban stats                               # board health
hermes kanban diagnostics                         # dispatcher diagnostics
```

## Command Palette integration (Windows)

A **PowerToys Command Palette extension** puts the fleet one keystroke away. Source lives in
`powertoys/cmdpal-hermes/` (C# MSIX COM server on the `Microsoft.CommandPalette.Extensions` SDK).

With the gateway running (`hermes gateway start`):

1. Press `Win+Alt+Space` (Command Palette hotkey).
2. Type your task as plain text — e.g. `hermes fix the flaky upload test` (or just any text).
3. The fallback item **"Give task to HERMES: \<your text\>"** appears — press Enter.
   The card is queued in **triage**; the dispatcher's decomposer fans it out and the right
   specialist bots pick it up automatically. A toast confirms the queued card id.
4. Typing `hermes` and pressing Open lands on the task page: open the **kanban dashboard**
   (localhost:9119), read **board status**, open the **workspace folder**, or the GitHub repo.

Rebuild + re-register after editing the extension (Developer Mode required, no signing):

```sh
& 'C:\Program Files\dotnet\dotnet.exe' build `
    powertoys/cmdpal-hermes/HermesCmdPalExtension/HermesCmdPalExtension.csproj `
    -c Debug -p:Platform=x64
Add-AppxPackage -Register .\powertoys\cmdpal-hermes\HermesCmdPalExtension\bin\x64\Debug\net10.0-windows10.0.26100.0\win-x64\AppxManifest.xml
```

Then restart the palette (or run `Reload` inside it) to pick up the new build.
Uninstall: `Remove-AppxPackage HermesCmdPalExtension_0.1.0.0_x64__...` (see
`Get-AppxPackage -Name '*Hermes*'`).

## License

[MIT](LICENSE) © 2026 Wireshreck
