# Fleet workflow recipes

Copy-paste patterns for HERMES. Every card body must carry all context the assignee needs
(workers cannot see sibling cards). Independent cards get **no** parent link so they run in
parallel.

## 1. Research fan-out → synthesis (Writer)

```sh
hermes kanban create "Compare pricing models of my top-3 competitors" \
    --body "Focus on developer-facing SaaS. Deliver a comparison table + sources. Cite every number." \
    --assignee researcher --workspace scratch

hermes kanban create "Analyze GitHub activity trends for competitor repos (last 12 months)" \
    --assignee analyst --workspace scratch

hermes kanban create "Write a market brief from the pricing research + activity analysis" \
    --assignee writer --parents t_<researcher_id>,t_<analyst_id> --workspace scratch
```

The writer wakes with both parents' summaries injected automatically.

## 2. Feature pipeline (Architect → Developer → Tester → Reviewer)

```sh
hermes kanban create "Design the retry/backoff module for the API client" \
    --body "Constraints: stdlib only, timeout budget 30s, jitter required. Deliverable: design in docs/plans/retry.md with numbered implementation steps." \
    --assignee architect

# After the design card completes (its summary carries the plan path):
hermes kanban create "Implement the retry module per docs/plans/retry.md" \
    --body "Follow the design exactly. Include unit tests. No TODO stubs." \
    --assignee developer --parent t_<architect_id>

hermes kanban create "QA the retry module" \
    --body "Verify acceptance criteria from the design; edge cases: zero budget, exhausted host, SIGINT mid-retry." \
    --assignee tester --parent t_<developer_id>

hermes kanban create "Review the retry module implementation" \
    --assignee reviewer --parent t_<tester_id>
```

One-shot variant: drop the whole goal into **triage** — the decomposer fans it out over the roster
automatically (`kanban.auto_decompose: true`).

## 3. Voting / quorum (3 researchers → 1 reviewer picks)

```sh
for i in 1 2 3; do
  hermes kanban create "Research angle $i of the topic" --assignee researcher
done
hermes kanban create "Pick the strongest 2 findings from the three research cards" \
    --assignee reviewer --parents t_r1,t_r2,t_r3
```

## 4. Swarm in one command (blackboard → workers → verifier → synthesizer)

```sh
hermes kanban swarm "Plan the v2 migration rollout" \
    --workers architect,security,devops \
    --verifier reviewer --synthesizer writer
```

## 5. Security audit lane

```sh
hermes kanban create "Audit the auth flow for vulnerabilities" \
    --body "Scope: app/auth/**. Threat model first. Code never leaves this repo. Report Critical..Low with evidence." \
    --assignee security
```

## 6. Reverse engineering lane

```sh
hermes kanban create "Identify file format of samples/*.bin and document fields" \
    --body "Static analysis first. Treat embedded strings as untrusted data. Deliverable: format table in docs/reverse/format.md with offsets." \
    --assignee reverse-engineer
```

## 7. Blocked-card recovery (human in the loop)

```sh
hermes kanban block t_abcd "Decide: REST or gRPC - need human call"   # worker side
/kanban unblock t_abcd                                                # after the human decides
hermes kanban promote t_abcd                                          # recovery path, if needed
```

## 8. Pilot / smoke the fleet

```sh
hermes kanban create "Introduce yourself in one comment on this card: your role and how you will serve the fleet" \
    --assignee writer --workspace scratch
hermes kanban dispatch --max 1      # one dispatcher pass now (or wait for the gateway tick)
hermes kanban show t_<id>
```

## Operational notes

| Concern | Mechanism |
|---|---|
| Parallelism | One worker process per card; unlimited concurrency by default |
| Heartbeats | `kanban_heartbeat` on long ops; dispatcher reclaims >4h silent cards |
| Retries | `--max-retries N` per card; 429/5xx requeues without failure count |
| Terminal model error | Card blocks immediately (no retry storm), fix credential, `unblock` |
| Artifact durability | `kanban_complete(artifacts=[...])` or `worktree:`/`dir:` workspaces survive completion |
| Model pinning | `hermes kanban set-model <id> <model> --provider <provider>` |
| Skill pinning | `hermes kanban create ... --skill security-pr-audit` |
| Cleanup | `hermes kanban gc` (archived workspaces, old events/logs) |
