# ANAC Runtime Draft

Status: working draft  
Audience: engineers building agent-tool integrations  
Goal: standardize the execution-time contract before asking anyone to adopt the broader ANAC stack

## Purpose

This document defines the smallest useful part of ANAC:

- structured action results
- structured workflow outcomes
- optimistic concurrency conventions
- retryable vs non-retryable failure semantics

It is intentionally narrower than [ANAC-0.1.2.md](/Users/ericorr/Documents/Legal%20LLM%20AWS/FunStuff/ANAC-0.1.2.md).

It does **not** require:

- a full capability manifest
- a workflow authoring language
- a specific transport protocol
- a specific predicate language

This is the piece of ANAC most likely to be adoptable on its own, including as an MCP-compatible result convention.

## What Problem This Solves

Most tool integrations tell the model only whether a call succeeded or failed.

That is not enough for real software.

A useful runtime contract also needs to answer:

- what changed?
- is this failure retryable?
- did the write fail because the target state changed underneath me?
- what should the orchestrator tell the user now?
- did the workflow complete, retry, or give up?

## Scope

This runtime draft standardizes four things:

1. `expected_revision`
2. `STALE_REVISION`
3. `action_result`
4. `outcome`

Everything else is optional or out of scope.

## Revision-Safe Writes

If a tool mutates revision-tracked state, it may accept an `expected_revision` with the write request.

If the current revision no longer matches the expected revision, the tool should fail explicitly instead of silently overwriting state.

Recommended error code:

```json
{
  "code": "STALE_REVISION",
  "retryable": true
}
```

This gives the orchestrator a clean branch:

- re-read state
- re-plan if needed
- retry if appropriate

## Action Result

Every tool invocation should return a structured `action_result`.

Minimum useful shape:

```json
{
  "action_id": "set_cell_value",
  "status": "success",
  "timestamp": "2026-03-09T21:27:00Z",
  "data": {},
  "state_delta": {
    "created": [],
    "modified": [],
    "deleted": []
  },
  "user_visible_effect": "Updated cell C15"
}
```

On failure:

```json
{
  "action_id": "set_cell_value",
  "status": "failure",
  "timestamp": "2026-03-09T21:27:00Z",
  "error": {
    "code": "STALE_REVISION",
    "message": "Cell was modified since last read",
    "retryable": true,
    "stale_entities": [
      {
        "entity_type": "cell",
        "ref": "cell:C15",
        "revision": "r113"
      }
    ]
  },
  "warnings": []
}
```

### Required fields

- `action_id`
- `status`
- `timestamp`

### Strongly recommended fields

- `data`
- `state_delta`
- `user_visible_effect`
- `error.code`
- `error.retryable`

## Outcome

`outcome` is the workflow-level termination object.

It answers a different question than `action_result`.

- `action_result`: what happened on this call?
- `outcome`: how did the overall execution end?

Current shape validated in this repo:

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

Minimum useful fields:

- `status`
- `disposition`
- `reason`

Strongly recommended:

- `terminal_step`
- `terminal_transition`
- `last_error_code`

Useful accounting fields:

- `context_refresh_count`
- `stale_retry_count`

## Retryable vs Non-Retryable

This distinction should be explicit.

Examples:

- `STALE_REVISION` -> retryable
- `JOB_NOT_COMPLETE` -> retryable
- `PERMISSION_DENIED` -> non-retryable
- `DIMENSION_MISMATCH` -> usually non-retryable

Do not force the model to infer retryability from an English error string.

## What the User Should Be Told

Even this runtime-only layer should help with user communication.

The minimum useful field is:

- `user_visible_effect`

This is not a full presentation model. It is a small bridge between execution and explanation.

Examples:

- `Inserted a new summary row at row 15`
- `Export started; waiting for completion`
- `Could not publish because the current user lacks publish permission`

That one field is often enough to prevent the model from inventing a poor summary of what happened.

## What This Draft Does Not Standardize

Not yet:

- full application context frames as a universal requirement
- workflow authoring
- presentation ranking / salience
- confirmation policy
- rollback policy
- predicate language

Those may matter later. They are not required to test whether the runtime layer is useful on its own.

## Evidence in This Repo

The runtime shape here is not purely theoretical.

It is exercised by:

- mock runtime scenarios in [scripts/validate_runtime_demo.py](/Users/ericorr/Documents/Legal%20LLM%20AWS/FunStuff/scripts/validate_runtime_demo.py)
- a live Google Sheets adapter in [scripts/anac_google_sheets_live.py](/Users/ericorr/Documents/Legal%20LLM%20AWS/FunStuff/scripts/anac_google_sheets_live.py)

Useful artifacts:

- runtime schema for `action_result`: [schema/anac-action-result-0.1.2.schema.json](/Users/ericorr/Documents/Legal%20LLM%20AWS/FunStuff/schema/anac-action-result-0.1.2.schema.json)
- runtime schema for `outcome`: [schema/anac-outcome-0.1.2.schema.json](/Users/ericorr/Documents/Legal%20LLM%20AWS/FunStuff/schema/anac-outcome-0.1.2.schema.json)
- live Sheets run: [google-sheets-live-happy-20260309T212638Z.json](/Users/ericorr/Documents/Legal%20LLM%20AWS/FunStuff/docs/traces/google-sheets-live-happy-20260309T212638Z.json)
- mock retry exhaustion path: produced by [scripts/validate_runtime_demo.py](/Users/ericorr/Documents/Legal%20LLM%20AWS/FunStuff/scripts/validate_runtime_demo.py)

## The Question for External Review

The question is deliberately narrow:

If your tool or MCP server returned results in this shape, with explicit stale-write and retry semantics, would that materially improve agent reliability?

More specifically:

- which fields are necessary?
- which fields are noise?
- what is missing?
- would you adopt this without adopting a larger protocol?

If the answer is no, that should be learned before any broader ANAC restructuring.
