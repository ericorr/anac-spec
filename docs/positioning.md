# ANAC: The Missing Layer Between AI Agents and Real Software

AI agents can call tools. They still struggle to use software.

The gap is not intelligence in the abstract. It is missing behavioral structure.

Current integration usually happens at one of two levels:

- pixel-level computer use, where the agent infers state from screenshots and clicks coordinates
- function-level tool calling, where the agent gets a set of typed functions but little guidance about sequencing, concurrency, or failure recovery

Both levels are useful. Neither gives the agent a durable model of how the application behaves.

ANAC is a proposal for that missing layer: the application exposes typed entities, callable actions, workflow state machines, runtime context, and structured outcomes so an orchestrator can execute software behavior against real state instead of guessing from pixels or improvising from a bag of verbs.

## The problem

Using software is not the same thing as calling functions.

A human power user does more than invoke operations. They understand:

- what entities exist and how they relate
- what order of operations is valid
- what side effects a step will trigger
- when state may have changed underneath them
- when to stop and ask for confirmation
- how to recover when one step fails halfway through a workflow

A plain function list does not encode any of that. A screenshot certainly does not.

That is why current agent integrations fail in predictable ways:

- they overwrite stale state because they do not track revisions
- they treat workflows as flat lists of calls instead of state machines
- they guess at error handling instead of following application-defined recovery paths
- they cannot distinguish a retryable failure from a terminal one

## What ANAC is

ANAC, the Agent-Native Application Contract, adds a behavioral layer on top of tool access.

An ANAC manifest describes:

### Entities

The typed objects the application manages: cells, sheets, artboards, layers, export jobs, and so on.

Entities can be revision-tracked so the runtime can detect stale reads and apply optimistic concurrency control.

### Actions

The callable operations the application supports.

Actions declare machine-relevant behavior such as:

- what entity types they read and write
- whether they accept `expected_revisions`
- structured success and failure codes
- reversibility and destructive-ness
- context requirements and permissions

### Workflows

State machines that compose actions into multi-step operations.

ANAC workflows do not just list calls. They route between typed step kinds:

- `observe`
- `mutate`
- `decide`
- `confirm`
- `wait`
- `subflow`

Each step declares transitions such as `success`, `failure`, `stale_revision`, `approved`, `rejected`, or `timeout`.

That means the manifest, not the model, owns the workflow grammar.

### Runtime payloads

The executor and adapter exchange structured runtime payloads:

- `context_frame`: current scoped state
- `action_result`: result of a callable operation
- `outcome`: terminal workflow disposition

These are now backed by separate runtime schemas in [`schema/`](../schema).

## Why this is not just another schema

Three properties matter.

### 1. Temporal awareness

ANAC workflows carry revision state.

A workflow can read an entity at revision `r88`, attempt a write later, and detect that the entity is now `r91`. Instead of overwriting silently, the write returns `STALE_REVISION` and the workflow follows its declared recovery path.

This is optimistic concurrency control applied to agent behavior.

### 2. Behavioral routing

ANAC workflows are state machines, not linear scripts.

The manifest defines what happens when a step succeeds, times out, fails, or becomes stale. The executor follows those transitions instead of inventing recovery behavior ad hoc.

### 3. Structured handoff points

ANAC makes human collaboration explicit.

A workflow can pause at a `confirm` step before a destructive or user-sensitive action, or pause at a `wait` step for async work to finish. This is not left to prompt engineering. It is encoded in the workflow definition.

## What the repo proves

The repository at [github.com/ericorr/anac-spec](https://github.com/ericorr/anac-spec) is not just a draft spec. It has four machine-enforced layers.

### Layer 1: Static schema validation

Example manifests validate against [`schema/anac-core-0.1.2.schema.json`](../schema/anac-core-0.1.2.schema.json).

This proves structural correctness.

### Layer 2: Semantic linting

[`scripts/anac_lint.py`](../scripts/anac_lint.py) checks properties JSON Schema cannot express, including:

- transition targets
- entity and action references
- revision-tracking consistency
- `watch_binding` resolution
- basic CEL symbol-scope sanity

This proves internal consistency.

### Layer 3: Runtime payload validation

The demo runtime emits `context_frame`, `action_result`, and `outcome` payloads, and [`scripts/validate_runtime_demo.py`](../scripts/validate_runtime_demo.py) validates them against:

- [`schema/anac-context-frame-0.1.2.schema.json`](../schema/anac-context-frame-0.1.2.schema.json)
- [`schema/anac-action-result-0.1.2.schema.json`](../schema/anac-action-result-0.1.2.schema.json)
- [`schema/anac-outcome-0.1.2.schema.json`](../schema/anac-outcome-0.1.2.schema.json)

These schemas were derived from a working executor rather than designed in the abstract.

### Layer 4: Multi-adapter runtime scenarios

The runtime currently exercises two adapters and five scenarios:

- `SheetApp` happy path
- `SheetApp` stale revision recovered
- `SheetApp` stale revision exhausted
- `VectorForge` happy path
- `VectorForge` non-retryable `PERMISSION_DENIED`

That matters because the `outcome` shape survived both adapters without requiring new top-level fields.

There is also now an experimental live adapter for Google Sheets at [`scripts/anac_google_sheets_live.py`](../scripts/anac_google_sheets_live.py). It is intentionally outside CI because this repo does not carry Google API credentials, but it uses the same `SheetApp` manifest and executor shape instead of a separate integration path.

## The concurrency story, concretely

The clearest example is the `SheetApp` workflow `add_summary_row`.

When the demo forces a concurrent edit during `insert_summary_row`, the actual workflow path is:

1. `read_table`
2. `decide_has_numeric_columns`
3. `insert_summary_row`
4. `STALE_REVISION`
5. `refresh_context`
6. back to `read_table`
7. retry `insert_summary_row`
8. continue through `write_formulas`, `read_summary_row_for_formatting`, `apply_formatting`, `read_label_target`, `add_label`

If the retry also fails and `max_context_refreshes` is exhausted, the workflow terminates with this validated `outcome` payload:

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

The important point is not the demo itself. It is that the recovery behavior is declared in the manifest and enforced by the executor. The model does not invent it at runtime.

## The analogy: LSP for agent-application integration

Before the Language Server Protocol, every editor-language pair required custom integration work. Editors had to learn each language separately.

ANAC aims at the same shape of simplification for agent-application integration.

Without a shared behavioral contract, every agent framework has to learn every application's state model, failure modes, and concurrency semantics independently.

With a shared contract, applications emit a standard behavioral surface and agent frameworks consume it.

That is the M×N problem collapsing toward M+N.

The analogy should not be overstated. ANAC is harder than LSP because application ontologies vary more than programming-language server interfaces. But the integration pattern is the same.

## What ANAC is not

### ANAC is not MCP

MCP handles discovery and invocation. ANAC handles behavioral semantics.

They are complementary, not competing.

### ANAC is not a GUI replacement

The human UI and the ANAC surface are parallel views over the same underlying state.

ANAC does not replace the GUI. It gives the agent a stateful semantic surface instead of forcing it to reverse-engineer the GUI.

### ANAC is not an agent framework

ANAC does not specify planning, reasoning, prompting, or model architecture.

It specifies what the application exposes and what runtime guarantees govern the interaction.

## Current boundaries

Version `0.1.2` is already concrete, but it is not complete.

Current limitations are explicit:

- the linter checks CEL symbol scope, not full CEL grammar
- the demo executor auto-approves `confirm` steps
- the live Google Sheets adapter is setup-verified but not CI-executed in this repo because credentials are not available
- rollback semantics for partially completed workflows are not yet formalized
- the top-level runtime envelope is still executor-defined; the formal runtime schemas currently cover the payload parts rather than the whole wrapper
- the runtime schemas have been proven across two adapters, not across an ecosystem

## Why formalizing `outcome` now is justified

`outcome` was left informal until the second adapter existed.

That threshold has now been met.

The same top-level `outcome` fields describe:

- normal completion
- completion after stale-revision recovery
- retry exhaustion
- non-retryable permission failure

The `VectorForge` path is the key proof because it does not exercise concurrency recovery at all. `context_refresh_count` and `stale_retry_count` stay at zero, which shows those fields are general runtime accounting, not spreadsheet-specific baggage.

## Next steps

The next meaningful steps are practical rather than theoretical:

- harden and verify the experimental Google Sheets live adapter against a real credentialed test sheet
- full CEL parsing once a runtime is chosen
- rollback semantics for partial-failure workflows
- integration with an existing agent framework to test the M+N claim against a real external orchestrator

## Read this with the repo

If you want the builder-facing overview, start here.

If you want the normative details, read [`ANAC-0.1.2.md`](../ANAC-0.1.2.md).

If you want the executable proof, run:

```bash
python3 examples/validate_examples.py
python3 scripts/anac_lint.py --strict examples/*.json
python3 scripts/validate_runtime_demo.py
```
