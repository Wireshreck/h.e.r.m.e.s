# H.E.R.M.E.S v0.2.0 — Desktop Agent Canvas

The interactive desktop application is now installable with a wizard.

## Install the desktop app

1. Download **`HERMES-Desktop-Setup.exe`** and run it.
2. Choose **This user** or **All users** (all-users asks for admin permission once).
3. Tick **Add to Desktop** and/or **Add to Start Menu** — shortcuts launch the agent canvas.
4. Click **INSTALL**.

The wizard downloads `HERMES-Desktop.zip` from this release, extracts it, checks Python/PyYAML and the Hermes CLI, and writes a log to `%LOCALAPPDATA%\hermes\desktop-install.log`.

Unattended: `HERMES-Desktop-Setup.exe -Scope User -Silent -Desktop -StartMenu`

## Fresh machine? Install the fleet first

Run **`HERMES-Fleet-Setup.exe`** from v0.1.0 (or the PowerShell installer there) to install Hermes Agent, the 13 free-model bots, the REA MCP server, keys and the kanban board. The desktop window without it shows a disconnected state — it never pretends to be connected.

## What's in the app

- Obsidian-style canvas: central orchestrator + 13 agent nodes, live hub/communication edges.
- Direct orchestrator chat (Enter to send, Shift+Enter newline, cancel jobs).
- ASCII glyph background, pan/zoom/drag, FIT/RESET, persistent layout, inspector.
- Left-edge history drawer (Alt+H) and right-edge live comms drawer (Alt+C), backed by comms.db.
- Statuses derived from real kanban/comms data; no fake activity.

## Assets

| File | Purpose |
|------|---------|
| `HERMES-Desktop-Setup.exe` | Installer wizard (signed by "Hermes Fleet") — run this |
| `HERMES-Desktop.zip` | Prebuilt desktop app + comms + fleet config (downloaded by the wizard) |
| `HERMES-Fleet-Setup.exe` | One-shot installer for the 13-bot fleet (from v0.1.0) |
| `install-hermes-fleet.ps1` | Same fleet installer as a script |
| `HermesCmdPalExtension.msix` / `.cer` | Signed Command Palette extension |

Source: https://github.com/Wireshreck/h.e.r.m.e.s
