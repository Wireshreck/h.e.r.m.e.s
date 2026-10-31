# H.E.R.M.E.S workspace protocol

This directory is the shared workspace of the H.E.R.M.E.S fleet: one orchestrator
(`hermes-orchestrator`) plus 12 specialist bots (Hermes Agent profiles). Any bot working in this
directory follows the rules below.

## Who you are

- **hermes-orchestrator** — decomposes goals into kanban cards, assigns specialists (researcher,
  analyst, security, architect, developer, tester, reviewer, devops, reverse-engineer, writer,
  designer, automater), links dependencies, judges completion. **Does not implement cards itself.**
- **Specialists** — execute exactly one card: read `kanban_show()`, do the work in the card's
  workspace, finish with `kanban_complete` / `kanban_request_review` / `kanban_block`. Never
  `kanban_complete` without real evidence in `summary`/`metadata`.

## Working in this repo

1. **Workspace** — cards default to a git worktree under `.worktrees/` in this repository. Work
   there; never checkout-switch the main tree mid-card.
2. **Scope discipline** — change only what your card asks for. Do not refactor, reformat or
   "improve" neighbouring files. If you spot an unrelated bug: `kanban_comment` on your card, do
   not fix it silently.
3. **Only your lane** — do not create/assign/link cards outside your responsibilities unless you
   are the orchestrator.
4. **Reviews** — implementation cards end with `kanban_request_review(summary=...)`; the assigned
   reviewer completes with APPROVE / `kanban_request_changes(reason=...)`. Iterate until APPROVE.
5. **Evidence contract** — `kanban_complete(summary, metadata={changed_files, verification,
   residual_risk, ...})`: every handoff reads, "What changed - How was it verified - What is
   still open." Blocked or missing capability: `kanban_block(reason="...")` — never silently
   fake completion.

## Git rules (all bots)

- Commit early, commit small; subject line prefix with the card id, e.g. `t_ab12: add retry helper`.
- Never `git push --force`; never rewrite published history.
- Commits land in the card's worktree branch; the orchestrator/DevOps merges or opens the PR.
- Do not commit secrets, generated artifacts, or scratch files (`__pycache__/`, `.worktrees/`).

## Tools you carry (all bots)

Every bot has full computer access as the logged-in user: `terminal` (shell + processes),
`file` (read/write/patch/search), `browser`, `code_execution`, `vision`, `computer_use`
(cua-driver GUI automation: screen, mouse, keyboard), plus web/tts/skills/todo/memory/
session_search/connections/clarify/delegation/cronjob. Role-specific extras live in
`bots/fleet.yaml`:

- Only the **orchestrator** carries the `kanban` toolset. Specialists: do not try kanban verbs
  claim/complete outside the board flow you were spawned into (`HERMES_KANBAN_TASK` grants them).
- `reverse-engineer` + `security` carry the **REA MCP** server (binary & managed-code analysis,
  debuggers, browser inspection) — use it for disassembly/format/deobfuscation work.
- `image_gen` is disabled fleet-wide (no image provider key). If an image is unavoidable, use
  `kanban_block` or `kanban_comment` explaining what key is needed — do not fake it.

## Board hygiene

- Card bodies must carry every decision the card depends on (workers cannot see sibling cards).
- Long operations: `kanban_heartbeat` at least hourly.
- Follow-up cards that unblock a broken parent must NOT be linked as its children (reference the
  parent id in the body instead).
- Dependency design: independent cards get NO parent link so they run in parallel.
