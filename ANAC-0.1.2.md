# RFC: Agent-Native Application Contract (ANAC)

**Status:** Draft / Working RFC  
**Version:** 0.1.2  
**Date:** 2026-03-09  
**Changelog:** 0.1.1 -> 0.1.2 hardening pass: explicit CEL evaluation environments, split static type access from runtime instance access, formal interpolation rules, scoped `context_frame` subscriptions, clarified `wait` semantics, added conformance walkthrough, aligned spec text with validator-oriented schema terms.

## Thesis

Current LLM-application integrations are strong at tool invocation and weak at application fluency. They expose functions, pixels, or documentation, but they do not consistently expose the state, workflow structure, concurrency boundaries, and evaluation semantics required for reliable multi-step operation inside real software.

ANAC defines a dual-surface contract:

- A human-facing UI
- An agent-facing contract for state, actions, workflows, and results

ANAC Core targets Layers 2-3 of application fluency:

- Layer 2: state-aware semantic access
- Layer 3: behavioral and workflow semantics

ANAC Core does not standardize model-specific compilation artifacts such as LoRAs. Those remain downstream optimizations.

## 1. Design Goals

ANAC Core is designed to be:

- Sufficient for safe, state-aware, multi-step operation without screen scraping
- Incrementally adoptable from tool metadata to revision-safe workflows
- Explicit enough to implement consistently across vendors and runtimes
- Constrained enough to validate mechanically

ANAC Core is not designed to:

- Replace MCP or other transport protocols
- Standardize UI rendering or presentation semantics
- Define a model-specific fine-tuning or adapter format
- Solve collaborative conflict resolution beyond optimistic concurrency

## 2. Core Model

ANAC Core has two normative artifacts:

- `manifest`: static application contract shipped by the vendor
- `context_frame`: runtime state object emitted by the application

The manifest defines:

- Entity types
- Actions
- Workflows
- Emission behavior

The context frame defines:

- Current application state
- In-scope entities
- Available actions
- Active workflows
- Permission tokens
- Recent events, if supported

The core transport assumption is simple: some underlying protocol can deliver the manifest, frames, action invocations, and structured action results. MCP is one plausible transport, not a requirement of the spec.

## 3. Reserved Terms

- `entity type`: a vendor-defined type such as `sheet`, `cell`, `artboard`, or `layer`
- `entity ref`: a stable runtime identifier for one entity instance
- `revision`: an opaque token representing the current version of a revision-tracked entity
- `binding`: a named runtime handle that maps action or workflow data into CEL
- `context scope`: the bounded set of entities and metadata included in a context frame
- `lease`: an advisory workflow runtime token with a TTL
- `permission token`: an opaque string matched by exact equality in core ANAC

Core ANAC assigns no hierarchy or wildcard semantics to permission tokens. If an application wants hierarchical permissions, it must flatten them into exact-match strings at the contract boundary.

## 4. Static Manifest

The manifest contains:

```yaml
anac: "0.1.2"
application:
  name: string
  id: string
  version: string
  tier: "minimal" | "semantic" | "behavioral" | "native"

emission:
  triggers: EmissionTrigger[]
  default_subscription: ContextSubscription
  max_rate: duration
  batch_window: duration

static:
  entities: EntityDefinition[]
  actions: ActionDefinition[]
  workflows: WorkflowDefinition[]
```

### 4.1 Entity Definitions

Each entity definition MUST include:

- `id`
- `name`
- `description`
- `schema`
- `revision_tracked`

Optional fields include:

- `relationships`
- `lifecycle`
- `constraints`

Constraint predicates MUST use CEL and MUST evaluate only over explicitly defined bound symbols.

### 4.2 Action Definitions

Each action definition MUST include:

- `id`
- `name`
- `description`
- `category`
- `parameters`
- `result`
- `reads_types`
- `writes_types`
- `accepts_expected_revision`
- `reversibility`

If an action contains predicates, it MUST also declare `bindings`.

`reads_types` and `writes_types` are static declarations over entity types. They describe what kinds of entities an action may inspect or mutate. They are not instance refs and are not execution traces.

### 4.3 Workflow Definitions

Each workflow definition MUST include:

- `id`
- `name`
- `description`
- `goal`
- `kind: state_machine`
- `entry_point`
- `lease_ttl`
- `steps`

At `behavioral` tier and above, workflows SHOULD declare `max_context_refreshes`.

## 5. Runtime Objects

Core runtime objects are:

- `context_frame`
- `action_result`
- `workflow_runtime`

### 5.1 Context Frame

The context frame is the single runtime state object in ANAC Core.

```yaml
context_frame:
  frame_id: string
  emitted_at: ISO8601
  trigger: string
  subscription_id: string
  scope: ContextScope
  application_state:
    screen: string
    mode: string
  selection: EntityRevisionRef[]
  permissions: string[]
  available_actions: ScopedAction[]
  active_workflows: WorkflowRuntime[]
  entity_snapshots: EntitySnapshot[]
  recent_events: Event[]
  warnings: Warning[]
```

`context_frame` is progressively populated by tier:

- `semantic`: `application_state`, `selection`, `permissions`, `available_actions`, `scope`
- `behavioral`: `semantic` + `active_workflows`, `entity_snapshots`, `warnings`
- `native`: `behavioral` + `recent_events`

### 5.2 Action Result

Every action invocation MUST return an `action_result`.

```yaml
action_result:
  action_id: string
  status: "success" | "partial" | "failure" | "pending"
  timestamp: ISO8601
  data: object
  state_delta:
    created: EntityRevisionRef[]
    modified: EntityRevisionRef[]
    deleted: EntityRevisionRef[]
  user_visible_effect: string
  error:
    code: string
    message: string
    retryable: boolean
    recovery_options:
      - action_id: string
        description: string
    stale_entities: EntityRevisionRef[]
  warnings: string[]
  undo_token: string | null
```

`error` MUST be omitted on successful actions and MUST be present on failures.

## 6. CEL Evaluation Environments

All predicates in ANAC Core use CEL. No normative predicate may be plain natural language.

This section defines the full symbol table by location. Any identifier not listed here or introduced through bindings is invalid.

### 6.1 Common Rules

All CEL environments:

- MUST be side-effect free
- MUST reject undefined symbols
- MUST reject unknown fields
- MUST treat vendor refs and revisions as opaque strings

Reserved root symbols:

- `context`
- `bindings`
- `entity`
- `params`
- `steps`
- `inputs`
- `current`
- `workflow`
- `watch`

Not every root symbol is available in every location.

### 6.2 Action Preconditions and Postconditions

Available symbols:

- `context`: the latest context frame
- `params`: the typed action invocation parameters
- `bindings`: named bound entities declared by the action definition

Action definitions that use predicates MUST declare bindings explicitly:

```yaml
bindings:
  cell:
    entity_type: "cell"
    from: "param"
    path: "address"
  sheet:
    entity_type: "sheet"
    from: "param"
    path: "sheet"
```

Example:

```yaml
preconditions:
  - predicate: "bindings.cell.data.is_locked == false"
    description: "Target cell must be unlocked"
    severity: "hard"
```

### 6.3 Entity Constraints

Available symbols:

- `entity`: the candidate entity snapshot being validated
- `context`: the latest context frame

Entity constraints MUST NOT reference `params`, `steps`, or `workflow`.

### 6.4 Workflow Step Predicates

Available symbols depend on step kind:

- All step predicates may use `context` and `workflow`
- `decide` may also use `steps`
- `observe` and `mutate` input interpolation may use `steps`
- `subflow` with `foreach` may also use `current`
- Subflows may use `inputs`
- `wait` predicates may use `watch`

### 6.5 Wait Step Environment

Wait steps MUST declare a `watch_binding`:

```yaml
watch_binding:
  entity_type: "export_job"
  ref_from: "step_output"
  path: "steps.start_export.export_job_ref"
```

Available symbols:

- `context`
- `workflow`
- `watch`

`watch` is the latest snapshot or event-projected state for the watched ref. If the watched ref is unavailable, the wait step MUST fail.

## 7. Interpolation Semantics

ANAC uses `${...}` interpolation in workflow inputs and runtime refs. The expression inside `${...}` is CEL.

### 7.1 Evaluation Order

Interpolation proceeds in this order:

1. Resolve the current CEL environment
2. Evaluate each interpolation expression exactly once
3. Materialize the final value
4. Invoke the action or emit the resolved step payload

Interpolation is not recursive.

### 7.2 Typed vs String Interpolation

If a scalar value consists of exactly one interpolation token, the resolved value MUST preserve its native type.

Examples:

- `before_row: "${steps.read_table.last_data_row + 1}"` -> integer
- `enabled: "${current.should_export}"` -> boolean

If interpolation appears inside surrounding text, all segments are coerced to string and concatenated.

Example:

- `address: "A${steps.insert_summary_row.summary_row_number}"` -> string

### 7.3 Escaping

The literal sequence `${` MUST be escaped as `$${`.

### 7.4 Failure Semantics

If interpolation fails due to an undefined symbol, type error, or invalid CEL expression, the step MUST fail before invoking its action.

## 8. Context Scope and Subscription Rules

ANAC frames MUST be scoped. Vendors MUST NOT dump unbounded application state into `context_frame`.

### 8.1 Context Subscription

Applications MUST support a subscription descriptor:

```yaml
ContextSubscription:
  subscription_id: string
  mode: "default" | "selection" | "workflow" | "explicit"
  root_refs: string[]
  entity_types: string[]
  include_neighbors: boolean
  include_recent_events: boolean
  max_entities: integer
```

`default_subscription` in the manifest defines the application's fallback runtime scope.

### 8.2 Scope Descriptor

Each frame MUST include:

```yaml
ContextScope:
  mode: "default" | "selection" | "workflow" | "explicit"
  root_refs: string[]
  entity_count: integer
  truncated: boolean
  next_cursor: string | null
```

### 8.3 In-Scope Entities

An entity is in scope if it satisfies at least one of:

- It is currently selected
- It is named in an active workflow binding
- It is directly referenced by an available action binding
- It is reachable from a root ref through declared relationships and allowed by the subscription

If the frame exceeds `max_entities`, the application MUST set `truncated: true` and provide `next_cursor`.

### 8.4 Behavioral Tier Requirement

At `behavioral` tier, `entity_snapshots` MUST contain only in-scope entities, not the full working set of the application.

## 9. Action and Workflow Execution Semantics

### 9.1 Static vs Runtime Access

ANAC uses different fields for static and runtime access:

- `reads_types` / `writes_types`: static action declarations over entity types
- `reads_refs` / `writes_refs`: runtime workflow declarations over entity refs

These MUST NOT be used interchangeably.

### 9.2 Mutate Steps

Mutate steps MUST include:

- `action`
- `inputs`
- `on.success`
- `on.failure`
- `on.stale_revision`

If the action supports optimistic concurrency and the workflow relies on prior observed state, the mutate step SHOULD include `expected_revisions`.

### 9.3 Stale Revision

If a mutate action encounters a revision mismatch, it MUST fail with:

- `error.code: STALE_REVISION`
- `error.retryable: true`
- `error.stale_entities` populated with current refs and revisions

### 9.4 Workflow Lease

Each workflow instance MUST receive:

- `lease_id`
- `started_at`
- `expires_at`
- `context_refresh_count`

Leases are advisory only. They do not block human edits.

## 10. Typed Workflow Steps

### 10.1 Observe

Observe steps read state or invoke a read-only action.

Required fields:

- `id`
- `kind: observe`
- `description`
- `on`

Optional fields:

- `action`
- `inputs`
- `reads_refs`
- `bindings`
- `emits`
- `predicate`

If `action` is present, the referenced action MUST declare `writes_types: []`.

### 10.2 Mutate

Mutate steps change application state.

Required fields:

- `id`
- `kind: mutate`
- `action`
- `inputs`
- `on.success`
- `on.failure`
- `on.stale_revision`

Optional fields:

- `reads_refs`
- `writes_refs`
- `expected_revisions`
- `bindings`
- `emits`

### 10.3 Decide

Decide steps branch on a CEL predicate.

Required fields:

- `id`
- `kind: decide`
- `predicate`
- `on_true`
- `on_false`

### 10.4 Confirm

Confirm steps require human approval.

Required fields:

- `id`
- `kind: confirm`
- `prompt`
- `on.approved`
- `on.rejected`

Optional fields:

- `payload`

### 10.5 Wait

Wait steps observe a watched ref until a predicate succeeds or times out.

Required fields:

- `id`
- `kind: wait`
- `watch_binding`
- `until`
- `timeout`
- `on.success`
- `on.timeout`
- `on.failure`

### 10.6 Subflow

Subflow steps invoke a reusable workflow fragment.

Required fields:

- `id`
- `kind: subflow`
- `workflow_ref`
- `on.success`
- `on.failure`

Optional fields:

- `foreach`
- `inputs`
- `continue_on_error`
- `on.partial`
- `on.stale_revision`

## 11. Capability Tiers

### 11.1 Minimal

Requires:

- Manifest with entities and actions
- Structured action results

### 11.2 Semantic

Requires minimal plus:

- Context frames
- Action preconditions
- Action bindings
- Scoped available actions
- Emission triggers and default subscription

### 11.3 Behavioral

Requires semantic plus:

- Typed workflows
- Revision-tracked entities where workflows depend on prior reads
- Mutate steps with stale revision handling
- Active workflow runtime objects
- In-scope entity snapshots

### 11.4 Native

Requires behavioral plus:

- Recent events in frames
- Stable frame monotonicity guarantees
- Any additional extensions claimed by the application

## 12. Conformance Walkthrough

This walkthrough is intentionally narrow. It demonstrates one stale-revision recovery cycle without relying on UI-specific behavior.

### 12.1 Initial Frame

```yaml
context_frame:
  frame_id: "f-100"
  emitted_at: "2026-03-09T17:00:00Z"
  trigger: "selection_change"
  subscription_id: "sub-default"
  scope:
    mode: "selection"
    root_refs: ["sheet:Q1 Sales"]
    entity_count: 2
    truncated: false
    next_cursor: null
  application_state:
    screen: "sheet_view"
    mode: "editing"
  selection:
    - entity_type: "cell"
      ref: "cell:D7"
      revision: "r112"
  permissions: ["sheet.edit", "sheet.format"]
  available_actions:
    - action_id: "insert_row"
      relevance: "primary"
      reason: "Selection is inside a table"
      preconditions_met: true
  entity_snapshots:
    - entity_type: "sheet"
      ref: "sheet:Q1 Sales"
      revision: "r88"
      data:
        last_data_row: 14
```

### 12.2 Agent Invocation

The agent invokes `insert_row` with an expected revision:

```yaml
action: "insert_row"
params:
  sheet: "Q1 Sales"
  before_row: 15
  count: 1
expected_revisions:
  - entity_type: "sheet"
    ref: "sheet:Q1 Sales"
    revision: "r88"
```

### 12.3 Concurrent Human Edit

Before the action is applied, a human inserts another row. The sheet revision becomes `r89`.

The application MUST return:

```yaml
action_result:
  action_id: "insert_row"
  status: "failure"
  timestamp: "2026-03-09T17:00:02Z"
  error:
    code: "STALE_REVISION"
    message: "Sheet revision changed before mutation"
    retryable: true
    recovery_options:
      - action_id: "refresh_context"
        description: "Read the latest frame and recompute row target"
    stale_entities:
      - entity_type: "sheet"
        ref: "sheet:Q1 Sales"
        revision: "r89"
  warnings: []
  undo_token: null
```

### 12.4 Refresh and Retry

The agent receives a new frame with the updated snapshot, recomputes `before_row`, and retries. The second invocation succeeds.

This is the minimum behavioral conformance loop:

1. Read scoped frame
2. Invoke mutation with expected revisions
3. Receive `STALE_REVISION` if assumptions are outdated
4. Refresh context
5. Retry or abort

## 13. Core vs Extensions

ANAC Core does not include:

- Rendering hints
- Vocabulary guidance
- Entity display templates
- UI surface layout recommendations
- Compilation targets

Those belong in extension modules such as:

- `anac-ux-ext`
- `anac-compile`
- `anac-auth`
- `anac-multi`

## 14. Remaining Open Questions

- Whether permission tokens should gain optional namespacing without breaking exact-match core semantics
- Whether `wait` should support direct event subscriptions in core or leave them to transport bindings
- Whether `workflow` mode subscriptions need a stronger minimum contract for long-running flows
- Whether agent-authored workflows belong in core or only in an extension

## Appendix A: Practical Migration from 0.1.1

If a 0.1.1 draft used:

- `reads` / `writes` on actions for type-level metadata, rename them to `reads_types` / `writes_types`
- `reads` / `writes` on workflow steps for instance refs, rename them to `reads_refs` / `writes_refs`
- raw symbols such as `cell` or `sheet` in predicates, replace them with `bindings.cell` or `bindings.sheet`
- `${...}` without typed interpolation rules, apply Section 7
- unbounded `entity_snapshots`, add `ContextSubscription` and `ContextScope`

## Appendix B: Reference Status

This 0.1.2 revision is a spec-hardening pass, not a literature audit. Research citations from earlier drafts should be re-verified before external circulation.
