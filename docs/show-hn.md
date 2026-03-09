# Show HN Draft

Suggested title:

`Show HN: ANAC, a spec for agent-facing application behavior (with a live Google Sheets adapter)`

Draft text:

AI agents can call tools. They still struggle to use software.

That gap is what this repo is about.

Today most agent-application integration happens at one of two levels:

- pixel-level computer use, where the model infers state from screenshots and clicks coordinates
- function-level tool calling, where the model gets typed calls but little guidance about sequencing, concurrency, or failure recovery

Both are useful. Neither gives the agent a durable behavioral model of the application.

The missing layer is behavioral semantics: typed entities, stateful workflows, optimistic concurrency, explicit failure routing, and structured runtime outcomes.

This repo is a working draft for that layer. I’m calling it **ANAC**: the Agent-Native Application Contract.

Repo: [github.com/ericorr/anac-spec](https://github.com/ericorr/anac-spec)

The core idea is simple:

- the app exposes typed entities
- the app exposes callable actions
- the app exposes workflows as state machines instead of leaving orchestration implicit
- the runtime exchanges structured payloads like `context_frame`, `action_result`, and `outcome`

That means an orchestrator can execute against real application state instead of guessing from pixels or improvising from a bag of verbs.

The concurrency story is the part I care about most.

The spreadsheet example in the repo has a workflow that adds a summary row. It reads table state, inserts the row, writes formulas, formats the row, and adds the label. The workflow uses optimistic concurrency control: reads happen against a revision, writes include the expected revision, and stale writes return `STALE_REVISION` instead of silently overwriting.

That recovery loop is not just in the prose spec. It is implemented and exercised.

When the workflow hits a stale write on `insert_summary_row`, it follows the declared path:

1. `read_table`
2. `decide_has_numeric_columns`
3. `insert_summary_row`
4. `STALE_REVISION`
5. `refresh_context`
6. back to `read_table`
7. retry the write
8. continue through the rest of the workflow

If the stale condition keeps happening, the workflow eventually terminates with a structured outcome:

```json
{
  "status": "failure",
  "disposition": "failed_retry_exhausted",
  "reason": "max_context_refreshes_exceeded",
  "terminal_step": "refresh_context",
  "last_error_code": "STALE_REVISION",
  "context_refresh_count": 2,
  "stale_retry_count": 2
}
```

The important point is that the model does not invent this behavior at runtime. The manifest defines it, and the executor follows it.

The repo has a few layers of proof, not just a schema file:

1. Static manifest schema validation
2. Semantic linting for things JSON Schema cannot express
3. Runtime validation for `context_frame`, `action_result`, and `outcome`
4. Integration scenarios against two adapters

Right now the runtime covers:

- `SheetApp` happy path
- `SheetApp` stale revision recovered
- `SheetApp` stale revision exhausted
- `VectorForge` happy path
- `VectorForge` non-retryable `PERMISSION_DENIED`

That second adapter matters because it exercises a different shape of software. It has `confirm` and `wait` steps, and it fails for a non-concurrency reason. The same top-level `outcome` shape held across both adapters, which is why I formalized it.

I also wanted one real application, not just mock adapters.

So the repo includes a live Google Sheets adapter that reuses the same `SheetApp` manifest and executor shape against an actual throwaway spreadsheet. It uses Drive metadata as a coarse revision signal, which is conservative but enough to prove the stale-read recovery loop against a real API-backed app.

Those live traces are committed here:

- [happy path trace](traces/google-sheets-live-happy-20260309T212638Z.json)
- [stale recovery trace](traces/google-sheets-live-stale-recovered-20260309T212715Z.json)

That is the point where this stopped being just “an interesting spec idea” for me. The same workflow structure now runs:

- against mock adapters
- against a real Google Sheet
- with both normal completion and stale-read recovery

What ANAC is not:

- It is not MCP. MCP handles discovery and invocation; ANAC is the behavioral layer on top.
- It is not a GUI replacement. The ANAC surface and the human UI are parallel views over the same state.
- It is not an agent framework. It does not specify planning or prompting. It specifies what the application exposes and how runtime interaction is structured.

The analogy I have in mind is “OpenAPI plus state/workflow semantics for agentic software.” If this kind of layer existed broadly, application builders would publish one behavioral surface and multiple agent frameworks could consume it, instead of every framework having to learn every application independently.

Current boundaries are straightforward:

- CEL scope is checked, but full CEL grammar validation is not done yet
- the demo executor auto-approves `confirm`
- rollback semantics are not fully specified
- the live adapter’s revision model is spreadsheet-wide, not range-aware

If you want the shortest path through the repo:

1. Read the builder-facing overview: [docs/positioning.md](positioning.md)
2. Read the normative draft: [ANAC-0.1.2.md](../ANAC-0.1.2.md)
3. Run the validators:

```bash
python3 examples/validate_examples.py
python3 scripts/anac_lint.py --strict examples/*.json
python3 scripts/validate_runtime_demo.py
```

If the core idea is wrong, the interesting place to attack it is not “why not just use tools?” The harder question is whether application vendors will actually expose behavioral semantics instead of stopping at tool lists. That is the adoption risk.

If the core idea is right, then the main missing piece in current agent infrastructure is not more clever prompting. It is a better contract between the agent and the application.
