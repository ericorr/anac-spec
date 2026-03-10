Show HN: ANAC – State machines and concurrency control for AI agent-application integration

AI agents can call functions. They cannot track state across a multi-step workflow, detect concurrent edits, or follow application-defined recovery paths. Those are different problems, and function lists do not solve them.

ANAC (Agent-Native Application Contract) is a spec for the behavioral layer between agents and applications. An app publishes a manifest describing its entities, actions, and workflows as state machines. An executor runs those workflows against real application state.

I will skip the philosophy and show you what it does.

**The concurrency loop.** The repo has a spreadsheet workflow that inserts a summary row. It reads the table, computes formulas, writes the row. The workflow carries revision state. If another user edits the sheet between the read and the write, the action returns `STALE_REVISION`. The workflow transitions to a re-read step, loops back, retries. If retries are exhausted, the workflow terminates with:

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

The agent did not improvise this. The manifest declared the recovery path. The executor followed it.

**The proof layers.** This is not a schema file with a README. The repo has:

- JSON Schema validation for manifests
- A semantic linter that checks transition targets, revision-tracking consistency, and reference resolution
- Runtime payload validation for context frames, action results, and workflow outcomes
- Five integration scenarios across two adapters (spreadsheet + design tool), covering normal completion, stale recovery, retry exhaustion, non-retryable failure, and async wait
- A live Google Sheets adapter that runs the same workflow against a real spreadsheet, with committed trace artifacts proving the adapter against a real API-backed app

The outcome schema was left informal until a second adapter confirmed the shape. It held. That is when it was formalized.

The retry loop itself is exercised in the mock scenarios. The live traces prove the adapter path, not a successful recovery branch.

**Run it yourself:**

```bash
git clone https://github.com/ericorr/anac-spec
cd anac-spec
python3 scripts/validate_runtime_demo.py
```

That runs all five scenarios and validates every runtime payload.

**What it is not.** It is not MCP (MCP handles tool discovery; ANAC is the behavioral layer on top). It is not a GUI replacement (the agent surface and human UI share the same state). It is not an agent framework (no planning, no prompting, just the application contract).

**Limitations I will save you the trouble of finding:**

- CEL conditions are scope-checked, not grammar-validated
- `confirm` steps auto-approve in the demo
- Rollback semantics for partial failures are not specified
- The live adapter uses spreadsheet-wide revision tracking, not per-range
- The runtime schemas are proven across two adapters, not an ecosystem

**The adoption question.** The hard problem is not the spec. It is whether application vendors will expose behavioral semantics instead of stopping at function lists. That cannot be answered by a repo. It can only be answered by proving, one application at a time, that the behavioral layer makes agents dramatically less destructive.

The concurrency story is the first proof. A real agent framework integration is next.

Repo: https://github.com/ericorr/anac-spec
