#!/usr/bin/env python3
"""Validate ANAC runtime payloads emitted by the toy executor."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT_DIR = Path(__file__).resolve().parent.parent
RUNTIME_DEMO = ROOT_DIR / "scripts" / "anac_runtime_demo.py"
CONTEXT_SCHEMA_PATH = ROOT_DIR / "schema" / "anac-context-frame-0.1.2.schema.json"
ACTION_SCHEMA_PATH = ROOT_DIR / "schema" / "anac-action-result-0.1.2.schema.json"


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def run_demo(*args: str) -> dict:
    raw = subprocess.check_output(["python3", str(RUNTIME_DEMO), *args], text=True)
    return json.loads(raw)


def validate_instance(validator: Draft202012Validator, instance: dict, label: str) -> list[str]:
    errors = sorted(validator.iter_errors(instance), key=lambda item: list(item.absolute_path))
    return [f"{label}: {error.message}" for error in errors]


def main() -> int:
    context_validator = Draft202012Validator(load_json(CONTEXT_SCHEMA_PATH))
    action_validator = Draft202012Validator(load_json(ACTION_SCHEMA_PATH))

    scenarios = [
        ("happy", []),
        ("stale_recovered", ["--force-stale-step", "insert_summary_row", "--force-stale-count", "1"]),
        ("stale_exhausted", ["--force-stale-step", "insert_summary_row", "--force-stale-count", "2"]),
    ]

    all_errors: list[str] = []

    for name, args in scenarios:
        payload = run_demo(*args)
        print(f"Scenario: {name}")
        print(f"  status: {payload['status']}")
        print(f"  disposition: {payload['outcome']['disposition']}")
        print(f"  trace steps: {len(payload['trace'])}")

        all_errors.extend(validate_instance(context_validator, payload["final_context_frame"], f"{name}:final_context_frame"))

        action_results = []
        for index, entry in enumerate(payload["trace"]):
            action_result = entry.get("action_result")
            if action_result is not None:
                action_results.append(action_result)
                all_errors.extend(validate_instance(action_validator, action_result, f"{name}:trace[{index}].action_result"))

        print(f"  action results: {len(action_results)}")

        if name == "happy":
            if payload["status"] != "success":
                all_errors.append(f"happy: expected success, got {payload['status']!r}")
            if payload["outcome"]["disposition"] != "completed":
                all_errors.append(f"happy: expected disposition 'completed', got {payload['outcome']['disposition']!r}")
            if payload["summary_row"] is None:
                all_errors.append("happy: expected a populated summary_row")

        if name == "stale_recovered":
            saw_stale = any(
                entry.get("action_result", {}).get("error", {}).get("code") == "STALE_REVISION"
                for entry in payload["trace"]
                if entry.get("action_result")
            )
            saw_refresh = any(entry["step_id"] == "refresh_context" for entry in payload["trace"])
            saw_simulated_event = any(entry.get("simulated_external_event") for entry in payload["trace"])
            if not saw_stale:
                all_errors.append("stale_recovered: expected at least one STALE_REVISION action result")
            if not saw_refresh:
                all_errors.append("stale_recovered: expected workflow to execute refresh_context after stale revision")
            if not saw_simulated_event:
                all_errors.append("stale_recovered: expected trace to record a simulated_external_event")
            if payload["status"] != "success":
                all_errors.append(f"stale_recovered: expected overall success after recovery, got {payload['status']!r}")
            if payload["outcome"]["disposition"] != "completed_after_retry":
                all_errors.append(
                    f"stale_recovered: expected disposition 'completed_after_retry', got {payload['outcome']['disposition']!r}"
                )
            if payload["outcome"]["context_refresh_count"] != 1:
                all_errors.append(
                    f"stale_recovered: expected context_refresh_count 1, got {payload['outcome']['context_refresh_count']!r}"
                )

        if name == "stale_exhausted":
            saw_stale = any(
                entry.get("action_result", {}).get("error", {}).get("code") == "STALE_REVISION"
                for entry in payload["trace"]
                if entry.get("action_result")
            )
            saw_refresh = any(entry["step_id"] == "refresh_context" for entry in payload["trace"])
            saw_abort = any(entry["step_id"] == "abort_too_many_refreshes" for entry in payload["trace"])
            if not saw_stale:
                all_errors.append("stale_exhausted: expected at least one STALE_REVISION action result")
            if not saw_refresh:
                all_errors.append("stale_exhausted: expected workflow to execute refresh_context")
            if not saw_abort:
                all_errors.append("stale_exhausted: expected workflow to hit abort_too_many_refreshes")
            if payload["status"] != "failure":
                all_errors.append(f"stale_exhausted: expected overall failure, got {payload['status']!r}")
            if payload["outcome"]["disposition"] != "failed_retry_exhausted":
                all_errors.append(
                    f"stale_exhausted: expected disposition 'failed_retry_exhausted', got {payload['outcome']['disposition']!r}"
                )
            if payload["outcome"]["reason"] != "max_context_refreshes_exceeded":
                all_errors.append(
                    f"stale_exhausted: expected reason 'max_context_refreshes_exceeded', got {payload['outcome']['reason']!r}"
                )
            if payload["summary_row"] is not None:
                all_errors.append("stale_exhausted: expected summary_row to be null when insertion never succeeds")
            if payload["outcome"]["context_refresh_count"] != 2:
                all_errors.append(
                    f"stale_exhausted: expected context_refresh_count 2, got {payload['outcome']['context_refresh_count']!r}"
                )

        print()

    if all_errors:
        print("Runtime validation failed:")
        for error in all_errors:
            print(f"  - {error}")
        return 1

    print("Runtime validation passed for happy, recovered stale, and exhausted stale scenarios.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
