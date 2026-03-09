Show HN: ANAC – A behavioral contract for AI agents that use real software

AI agents can call tools. They still struggle to use software.

Using software means tracking state across steps, detecting when something changed underneath you, recovering from failures, and knowing when to ask for permission. A flat list of callable functions does not encode any of that.

ANAC (Agent-Native Application Contract) adds the missing behavioral layer. An application publishes a manifest describing its entities, actions, and workflows as state machines. An orchestrator executes those workflows against real application state instead of improvising from a bag of functions.

The part I care about most is concurrency.

The repo includes a spreadsheet example where a workflow adds a summary row. It reads the table, computes formulas, inserts the row, formats it, adds a label. The workflow tracks revisions: if another user edits the sheet between the read and the write, the action returns `STALE_REVISION` instead of silently overwriting. The workflow follows its declared recovery path — re-read state, retry the write — or terminates with a structured failure if retries are exhausted:

```json
{
  "status": "failure",
  "disposition": "failed_retry_exhausted",
  "reason": "max_context_refreshes_exceeded",
  "terminal_step": "refresh_context",
  "terminal_transition": "failure",
  "last_error_code": "STALE_REVISION",
  "context_refresh_count": 2,
  "stale_retry_count": 2
}
```

The agent does not invent this behavior. The manifest defines it, the executor follows it.

This is not just a schema file. The repo has four layers of validation:

1. JSON Schema validation for manifest structure
2. A semantic linter for things the schema cannot express (transition targets, revision-tracking consistency, reference resolution)
3. Runtime payload validation for context frames, action results, and workflow outcomes
4. Integration tests across two adapters (SheetApp, VectorForge) covering five scenarios: happy path, stale-revision recovery, retry exhaustion, non-retryable failure, and async wait

The outcome schema survived both adapters without modification, which is why it was formalized.

The repo also includes a live Google Sheets adapter — same manifest, same executor, real Sheets and Drive APIs. The captured live runs are committed in the repo:

- Happy path: https://github.com/ericorr/anac-spec/blob/main/docs/traces/google-sheets-live-happy-20260309T212638Z.json
- Forced-stale live run: https://github.com/ericorr/anac-spec/blob/main/docs/traces/google-sheets-live-stale-recovered-20260309T212715Z.json

Those traces show the adapter running against a real API-backed spreadsheet. The retry loop itself is exercised and validated in the mock scenarios.

The quickest way through the repo:

```bash
git clone https://github.com/ericorr/anac-spec
cd anac-spec
python3 examples/validate_examples.py
python3 scripts/anac_lint.py --strict examples/*.json
python3 scripts/validate_runtime_demo.py
```

What ANAC is not: it is not MCP (MCP handles discovery and invocation; ANAC is the behavioral layer on top). It is not a GUI replacement (the agent surface and human UI share the same underlying state). It is not an agent framework (it does not specify planning or prompting).

The rough shape is MxN -> M+N: applications publish one behavioral surface, multiple orchestrators consume it.

Known limitations: CEL conditions are scope-checked but not grammar-validated. The demo auto-approves confirm steps. Rollback semantics are not yet specified. The live adapter uses spreadsheet-wide revision tracking, not per-range.

If the core idea is wrong, the interesting attack is not "why not just use tools." The harder question is whether application vendors will expose behavioral semantics at all.

If the core idea is right, the main gap in agent infrastructure is not smarter prompting. It is a better contract between the agent and the application.

Repo: https://github.com/ericorr/anac-spec
