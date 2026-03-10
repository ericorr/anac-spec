# Most agent-tool integrations return blobs

A model calls a tool, gets back `success` or an error string, and then has to guess:

- what changed
- whether failure is retryable
- whether the write went stale
- what to tell the user next

This repo explores a better runtime contract for stateful tools and applications.

The current focus is deliberately narrow:

- structured `action_result`
- structured `outcome`
- `expected_revision` / `STALE_REVISION`
- retryable vs non-retryable failures
- minimal user-facing summaries like `user_visible_effect`

Project name: **ANAC**. But if you are arriving from an MCP discussion, the main thing here is the runtime draft.

## Start Here

Read this first:

- [docs/specs/runtime.md](docs/specs/runtime.md)

That is the 10-minute document this repo is currently asking external engineers to react to.

## See It Work

```bash
git clone https://github.com/ericorr/anac-spec
cd anac-spec
python3 -m pip install jsonschema
python3 scripts/validate_runtime_demo.py
```

That runs the mock runtime scenarios, including:

- stale revision recovered
- stale revision exhausted
- non-retryable failure
- async wait path

Live evidence is also committed:

- [docs/traces/google-sheets-live-happy-20260309T212638Z.json](docs/traces/google-sheets-live-happy-20260309T212638Z.json)
- [docs/traces/google-sheets-live-stale-recovered-20260309T212715Z.json](docs/traces/google-sheets-live-stale-recovered-20260309T212715Z.json)

Those live traces prove the adapter path against a real API-backed application. The retry loop itself is demonstrated in the mock scenarios.

## Core

| What | Why it matters |
|---|---|
| [docs/specs/runtime.md](docs/specs/runtime.md) | Start here. The smallest useful ANAC layer. |
| [scripts/validate_runtime_demo.py](scripts/validate_runtime_demo.py) | One command to exercise the runtime shapes and scenarios. |
| [schema/anac-action-result-0.1.2.schema.json](schema/anac-action-result-0.1.2.schema.json) | Current runtime schema for `action_result`. |
| [schema/anac-outcome-0.1.2.schema.json](schema/anac-outcome-0.1.2.schema.json) | Current runtime schema for `outcome`. |
| [docs/traces/google-sheets-live-happy-20260309T212638Z.json](docs/traces/google-sheets-live-happy-20260309T212638Z.json) | Real API-backed run through the live Google Sheets adapter. |
| [scripts/anac_google_sheets_live.py](scripts/anac_google_sheets_live.py) | The live adapter implementation. |

## Background

| What | Why you might care |
|---|---|
| [ANAC-0.1.2.md](ANAC-0.1.2.md) | The full integrated draft before the current narrowing. |
| [docs/anac-0.2-plan.md](docs/anac-0.2-plan.md) | The current restructuring direction: Runtime / Capability / Workflow. |
| [docs/positioning.md](docs/positioning.md) | Broader explanation of the problem and the original architecture. |
| [docs/google-sheets-live.md](docs/google-sheets-live.md) | Setup and limitations for the first live adapter. |
| [examples/example-sheetapp-0.1.2.json](examples/example-sheetapp-0.1.2.json) | Procedural spreadsheet example. |
| [examples/example-vectorforge-0.1.2.json](examples/example-vectorforge-0.1.2.json) | Spatial/creative-tool example. |

## Tooling

| What | Purpose |
|---|---|
| [examples/validate_examples.py](examples/validate_examples.py) | Static schema validation for the example manifests. |
| [scripts/anac_lint.py](scripts/anac_lint.py) | Semantic checks beyond JSON Schema. |
| [scripts/anac_runtime_demo.py](scripts/anac_runtime_demo.py) | Runtime executor with mock adapters. |
| [scripts/validate_runtime_demo.py](scripts/validate_runtime_demo.py) | Runtime scenario validation. |
| [schema/](schema) | Static and runtime schemas. |
| [.github/workflows/validate-anac.yml](.github/workflows/validate-anac.yml) | CI entry point. |

## Feedback Wanted

The question is narrow:

If your tool or MCP server returned results in the shape described in [docs/specs/runtime.md](docs/specs/runtime.md), would that materially improve agent reliability?

The most useful feedback is:

1. Which fields are actually necessary?
2. Which fields are noise?
3. What is missing for a real production tool or MCP server?
4. Would you adopt this as a convention without adopting a larger spec?
