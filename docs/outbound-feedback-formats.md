# Outbound Feedback Formats

Three versions of the same ask, calibrated for different channels.

These are intentionally framed around a **runtime convention**, not ANAC as a full protocol.

Recommended sequence:

1. MCP GitHub discussion
2. short cross-post
3. direct DM/email follow-up

## 1. MCP GitHub Discussion

Recommended venue:

- main MCP discussions: [modelcontextprotocol/modelcontextprotocol/discussions](https://github.com/modelcontextprotocol/modelcontextprotocol/discussions)
- likely category: `Ideas - General`

Suggested title:

`Convention for structured tool results with revision tracking and retry semantics?`

Suggested body:

I've been working on a project exploring what happens when MCP tools mutate stateful applications — spreadsheets, design tools, anything where the user might edit the same object the agent is writing to.

The core problem: most tool results are unstructured blobs. The model gets back "success" or an error string and has to figure out what happened, whether to retry, and what to tell the user. That works for simple lookups but breaks down for multi-step mutations where state can change between calls.

I've been prototyping a structured result convention that any MCP tool could adopt:

**`action_result`** — returned by every tool call:
- `status` (`success` / `failure` / `partial`)
- `state_delta` (what was created / modified / deleted)
- `user_visible_effect` (one-line summary the model can relay to the user)
- `error.code` + `error.retryable` (so the model knows whether to retry or stop)
- `error.stale_entities` (if the failure was a stale write, which entities changed)

**`expected_revision`** — passed with mutation requests. If the object's revision changed since the last read, the tool returns `STALE_REVISION` with `retryable: true` instead of silently overwriting.

**`outcome`** — for multi-step sequences, a terminal summary: did the overall operation complete, complete after retry, exhaust retries, or hit a non-retryable failure?

I've validated the runtime shape against two mock adapters and a live Google Sheets adapter path. The same result shape held across stale-retry, retry-exhaustion, and non-retryable failure scenarios.

The draft is here: https://github.com/ericorr/anac-spec/blob/main/docs/specs/runtime.md

The repo also includes:

- mock scenarios that exercise retry and retry exhaustion
- a live Google Sheets adapter path showing the same runtime shape against a real API-backed app

I'm not proposing a new protocol. I'm asking whether this kind of structured result convention would be useful as an MCP extension or best practice, so tools that mutate state can give models better signal than a raw blob.

Specific questions:

- Is the `action_result` shape roughly right, or does it conflict with how you structure tool responses?
- Is `expected_revision` / `STALE_REVISION` useful, or is optimistic concurrency too niche for most MCP tools?
- Is `user_visible_effect` actually helpful, or would models do better inferring the summary from the data?
- What's missing that you'd need for a real production MCP server?

Happy to hear that this is solving a non-problem too. That's useful feedback.

## 2. DM / Email

Suggested message:

Hey — I’ve been working on a structured result convention for MCP-style tools that mutate state. The idea is: instead of returning a raw blob, tools return a standard shape with status, what changed, whether failure is retryable, and a one-line summary the model can relay to the user.

I also added an `expected_revision` field so tools can detect stale writes and fail explicitly with `STALE_REVISION` instead of silently overwriting.

I validated the runtime shape against two mock adapters and a live Google Sheets adapter path. The same shape held across stale-retry, retry-exhaustion, and non-retryable failure scenarios.

Short draft: https://github.com/ericorr/anac-spec/blob/main/docs/specs/runtime.md

The question I’m trying to answer: is this useful as a convention, or is it solving a problem people don’t actually have? Would appreciate 5 minutes of honest feedback if you have it.

## 3. Short Post

Suggested text:

I’ve been prototyping a structured result convention for MCP-style tools that mutate state.

Problem: most tool results are blobs. The model gets "success" or an error string and has to guess what happened, whether to retry, and what to tell the user.

Proposed convention:

-> Every tool returns `action_result` with: status, what changed (`state_delta`), whether failure is retryable, and a one-line `user_visible_effect` the model can relay directly.

-> Mutation requests can include `expected_revision`. If the object changed since last read, the tool returns `STALE_REVISION` with `retryable: true` instead of silently overwriting.

-> Multi-step sequences get an `outcome`: did the operation complete, retry, exhaust retries, or hit a terminal error?

I tested the runtime shape against two mock adapters and a live Google Sheets adapter path. Same shape worked across stale-retry, retry-exhaustion, and non-retryable failure scenarios.

Draft: https://github.com/ericorr/anac-spec/blob/main/docs/specs/runtime.md

Question: is this useful, or am I solving a problem that doesn’t actually bite people in practice? What’s missing?
