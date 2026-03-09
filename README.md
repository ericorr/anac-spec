# ANAC Spec

This repository contains a working draft of the Agent-Native Application Contract (`ANAC`) plus supporting schema, examples, and validation tooling.

## Repository Layout

- [`ANAC-0.1.2.md`](/Users/ericorr/Documents/Legal%20LLM%20AWS/FunStuff/ANAC-0.1.2.md): current normative draft
- [`schema/anac-core-0.1.2.schema.json`](/Users/ericorr/Documents/Legal%20LLM%20AWS/FunStuff/schema/anac-core-0.1.2.schema.json): JSON Schema for the static ANAC core manifest
- [`schema/anac-context-frame-0.1.2.schema.json`](/Users/ericorr/Documents/Legal%20LLM%20AWS/FunStuff/schema/anac-context-frame-0.1.2.schema.json): draft runtime schema for emitted `context_frame` payloads
- [`schema/anac-action-result-0.1.2.schema.json`](/Users/ericorr/Documents/Legal%20LLM%20AWS/FunStuff/schema/anac-action-result-0.1.2.schema.json): draft runtime schema for emitted `action_result` payloads
- [`examples/example-sheetapp-0.1.2.json`](/Users/ericorr/Documents/Legal%20LLM%20AWS/FunStuff/examples/example-sheetapp-0.1.2.json): procedural spreadsheet example
- [`examples/example-vectorforge-0.1.2.json`](/Users/ericorr/Documents/Legal%20LLM%20AWS/FunStuff/examples/example-vectorforge-0.1.2.json): spatial/creative-tool pressure test
- [`examples/validate_examples.py`](/Users/ericorr/Documents/Legal%20LLM%20AWS/FunStuff/examples/validate_examples.py): schema-only validation for the bundled examples
- [`scripts/anac_lint.py`](/Users/ericorr/Documents/Legal%20LLM%20AWS/FunStuff/scripts/anac_lint.py): semantic linting beyond JSON Schema
- [`scripts/validate_runtime_demo.py`](/Users/ericorr/Documents/Legal%20LLM%20AWS/FunStuff/scripts/validate_runtime_demo.py): validates runtime payloads emitted by the toy executor

## What Exists Today

- `0.1.2` is the hardened spec draft focused on implementation clarity.
- The JSON Schema validates manifest structure.
- The semantic linter checks cross-reference integrity and basic workflow consistency.
- The bundled examples are intended to be both schema-valid and lint-clean.

## Validation

Validate the bundled examples against the JSON Schema:

```bash
python3 examples/validate_examples.py
```

Validate any manifest directly:

```bash
python3 - <<'PY'
import json
from pathlib import Path
from jsonschema import validate

schema = json.loads(Path("schema/anac-core-0.1.2.schema.json").read_text())
manifest = json.loads(Path("examples/example-sheetapp-0.1.2.json").read_text())
validate(instance=manifest, schema=schema)
print("ok")
PY
```

## Linting

Run semantic checks that the schema cannot express:

```bash
python3 scripts/anac_lint.py examples/example-sheetapp-0.1.2.json examples/example-vectorforge-0.1.2.json
```

Use `--strict` if you want warnings to fail the run:

```bash
python3 scripts/anac_lint.py --strict examples/example-sheetapp-0.1.2.json
```

The linter currently checks:

- duplicate IDs across entities, actions, workflows, steps, and subflows
- unknown entity, action, workflow, step, and step-output references
- tier-level requirements that JSON Schema cannot express
- `observe` steps that call mutating actions
- revision handling on `mutate` steps
- `watch_binding` and `workflow_ref` resolution
- CEL symbol-scope sanity for predicates and interpolations

It does not yet do full CEL parsing or runtime simulation.

## Toy Runtime Executor

There is also a minimal runtime scaffold for the bundled spreadsheet example:

```bash
python3 scripts/anac_runtime_demo.py
```

This does three things:

- loads the `SheetApp` manifest
- runs the `add_summary_row` workflow against an in-memory mock adapter
- prints a trace containing resolved inputs, step emissions, transitions, and action results

The executor is intentionally small and incomplete. Its main purpose is to surface runtime contract needs empirically, especially:

- how `observe` steps populate `emits`
- what `context_frame` shape the orchestrator actually needs
- how optimistic concurrency affects action calls and results

The current demo supports enough CEL to run the bundled example, not the full language.

The runtime payload now includes a top-level `outcome` object with:

- `status`: coarse terminal status (`success` or `failure` in the current demo)
- `disposition`: terminal mode such as `completed`, `completed_after_retry`, or `failed_retry_exhausted`
- `reason`: why the workflow stopped
- `terminal_step` and `terminal_transition`
- `last_error_code`
- `context_refresh_count`
- `stale_retry_count`

Force a deterministic stale-revision recovery path:

```bash
python3 scripts/anac_runtime_demo.py --force-stale-step insert_summary_row --force-stale-count 1
```

Force a deterministic stale-revision exhaustion path that exceeds `max_context_refreshes`:

```bash
python3 scripts/anac_runtime_demo.py --force-stale-step insert_summary_row --force-stale-count 2
```

Validate the happy-path and stale-path runtime payloads against the draft runtime schemas:

```bash
python3 scripts/validate_runtime_demo.py
```

## Notes

- This repo currently emphasizes the normative/spec side, not the higher-level position paper.
- Research citations from earlier drafts still need a separate verification pass before wider circulation.
