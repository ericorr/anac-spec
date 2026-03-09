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
OUTCOME_SCHEMA_PATH = ROOT_DIR / "schema" / "anac-outcome-0.1.2.schema.json"
VECTOR_MANIFEST = ROOT_DIR / "examples" / "example-vectorforge-0.1.2.json"


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def run_demo(*args: str) -> dict:
    raw = subprocess.check_output(["python3", str(RUNTIME_DEMO), *args], text=True)
    return json.loads(raw)


def validate_instance(validator: Draft202012Validator, instance: dict, label: str) -> list[str]:
    errors = sorted(validator.iter_errors(instance), key=lambda item: list(item.absolute_path))
    return [f"{label}: {error.message}" for error in errors]


def action_errors(payload: dict) -> list[str]:
    return [
        entry["action_result"]["error"]["code"]
        for entry in payload["trace"]
        if entry.get("action_result") and entry["action_result"].get("error")
    ]


def main() -> int:
    context_validator = Draft202012Validator(load_json(CONTEXT_SCHEMA_PATH))
    action_validator = Draft202012Validator(load_json(ACTION_SCHEMA_PATH))
    outcome_validator = Draft202012Validator(load_json(OUTCOME_SCHEMA_PATH))

    scenarios = [
        ("sheet_happy", []),
        ("sheet_stale_recovered", ["--force-stale-step", "insert_summary_row", "--force-stale-count", "1"]),
        ("sheet_stale_exhausted", ["--force-stale-step", "insert_summary_row", "--force-stale-count", "2"]),
        (
            "vector_happy",
            ["--manifest", str(VECTOR_MANIFEST), "--workflow", "refresh_accessible_asset"],
        ),
        (
            "vector_publish_denied",
            [
                "--manifest",
                str(VECTOR_MANIFEST),
                "--workflow",
                "refresh_accessible_asset",
                "--deny-permission",
                "asset.publish",
            ],
        ),
    ]

    all_errors: list[str] = []

    for name, args in scenarios:
        payload = run_demo(*args)
        print(f"Scenario: {name}")
        print(f"  application: {payload['application_id']}")
        print(f"  status: {payload['status']}")
        print(f"  disposition: {payload['outcome']['disposition']}")
        print(f"  trace steps: {len(payload['trace'])}")

        all_errors.extend(validate_instance(context_validator, payload["final_context_frame"], f"{name}:final_context_frame"))
        all_errors.extend(validate_instance(outcome_validator, payload["outcome"], f"{name}:outcome"))

        action_results = []
        for index, entry in enumerate(payload["trace"]):
            action_result = entry.get("action_result")
            if action_result is not None and {"action_id", "status", "timestamp"}.issubset(action_result):
                action_results.append(action_result)
                all_errors.extend(validate_instance(action_validator, action_result, f"{name}:trace[{index}].action_result"))

        print(f"  action results: {len(action_results)}")

        if name == "sheet_happy":
            if payload["status"] != "success":
                all_errors.append(f"sheet_happy: expected success, got {payload['status']!r}")
            if payload["outcome"]["disposition"] != "completed":
                all_errors.append(
                    f"sheet_happy: expected disposition 'completed', got {payload['outcome']['disposition']!r}"
                )
            if payload["artifacts"].get("summary_row") is None:
                all_errors.append("sheet_happy: expected a populated summary_row artifact")

        if name == "sheet_stale_recovered":
            saw_stale = "STALE_REVISION" in action_errors(payload)
            saw_refresh = any(entry["step_id"] == "refresh_context" for entry in payload["trace"])
            saw_simulated_event = any(entry.get("simulated_external_event") for entry in payload["trace"])
            if not saw_stale:
                all_errors.append("sheet_stale_recovered: expected at least one STALE_REVISION action result")
            if not saw_refresh:
                all_errors.append("sheet_stale_recovered: expected workflow to execute refresh_context after stale revision")
            if not saw_simulated_event:
                all_errors.append("sheet_stale_recovered: expected trace to record a simulated_external_event")
            if payload["status"] != "success":
                all_errors.append(
                    f"sheet_stale_recovered: expected overall success after recovery, got {payload['status']!r}"
                )
            if payload["outcome"]["disposition"] != "completed_after_retry":
                all_errors.append(
                    "sheet_stale_recovered: expected disposition 'completed_after_retry', "
                    f"got {payload['outcome']['disposition']!r}"
                )
            if payload["outcome"]["context_refresh_count"] != 1:
                all_errors.append(
                    "sheet_stale_recovered: expected context_refresh_count 1, "
                    f"got {payload['outcome']['context_refresh_count']!r}"
                )

        if name == "sheet_stale_exhausted":
            saw_stale = "STALE_REVISION" in action_errors(payload)
            saw_refresh = any(entry["step_id"] == "refresh_context" for entry in payload["trace"])
            saw_abort = any(entry["step_id"] == "abort_too_many_refreshes" for entry in payload["trace"])
            if not saw_stale:
                all_errors.append("sheet_stale_exhausted: expected at least one STALE_REVISION action result")
            if not saw_refresh:
                all_errors.append("sheet_stale_exhausted: expected workflow to execute refresh_context")
            if not saw_abort:
                all_errors.append("sheet_stale_exhausted: expected workflow to hit abort_too_many_refreshes")
            if payload["status"] != "failure":
                all_errors.append(f"sheet_stale_exhausted: expected overall failure, got {payload['status']!r}")
            if payload["outcome"]["disposition"] != "failed_retry_exhausted":
                all_errors.append(
                    "sheet_stale_exhausted: expected disposition 'failed_retry_exhausted', "
                    f"got {payload['outcome']['disposition']!r}"
                )
            if payload["outcome"]["reason"] != "max_context_refreshes_exceeded":
                all_errors.append(
                    "sheet_stale_exhausted: expected reason 'max_context_refreshes_exceeded', "
                    f"got {payload['outcome']['reason']!r}"
                )
            if payload["artifacts"].get("summary_row") is not None:
                all_errors.append("sheet_stale_exhausted: expected summary_row to be null when insertion never succeeds")
            if payload["outcome"]["context_refresh_count"] != 2:
                all_errors.append(
                    "sheet_stale_exhausted: expected context_refresh_count 2, "
                    f"got {payload['outcome']['context_refresh_count']!r}"
                )

        if name == "vector_happy":
            trace_steps = {entry["step_id"] for entry in payload["trace"]}
            if payload["status"] != "success":
                all_errors.append(f"vector_happy: expected success, got {payload['status']!r}")
            if payload["outcome"]["disposition"] != "completed":
                all_errors.append(
                    f"vector_happy: expected disposition 'completed', got {payload['outcome']['disposition']!r}"
                )
            if payload["outcome"]["context_refresh_count"] != 0:
                all_errors.append(
                    f"vector_happy: expected context_refresh_count 0, got {payload['outcome']['context_refresh_count']!r}"
                )
            if not {"wait_for_export", "confirm_publish", "publish"}.issubset(trace_steps):
                all_errors.append("vector_happy: expected wait/confirm/publish steps in trace")
            published_refs = payload["artifacts"].get("published_refs", [])
            if len(published_refs) != 2:
                all_errors.append(f"vector_happy: expected 2 published refs, got {published_refs!r}")
            position = payload["artifacts"].get("group_position")
            if position != {"x": 712, "y": 400}:
                all_errors.append(f"vector_happy: expected snapped group position {{'x': 712, 'y': 400}}, got {position!r}")
            if payload["artifacts"].get("export_job", {}).get("data", {}).get("status") != "completed":
                all_errors.append("vector_happy: expected completed export_job artifact")

        if name == "vector_publish_denied":
            trace_steps = {entry["step_id"] for entry in payload["trace"]}
            errors = action_errors(payload)
            if payload["status"] != "failure":
                all_errors.append(f"vector_publish_denied: expected failure, got {payload['status']!r}")
            if payload["outcome"]["disposition"] != "failed_non_retryable":
                all_errors.append(
                    "vector_publish_denied: expected disposition 'failed_non_retryable', "
                    f"got {payload['outcome']['disposition']!r}"
                )
            if payload["outcome"]["reason"] != "action_failure":
                all_errors.append(
                    f"vector_publish_denied: expected reason 'action_failure', got {payload['outcome']['reason']!r}"
                )
            if payload["outcome"]["terminal_step"] != "publish":
                all_errors.append(
                    f"vector_publish_denied: expected terminal_step 'publish', got {payload['outcome']['terminal_step']!r}"
                )
            if payload["outcome"]["last_error_code"] != "PERMISSION_DENIED":
                all_errors.append(
                    "vector_publish_denied: expected last_error_code 'PERMISSION_DENIED', "
                    f"got {payload['outcome']['last_error_code']!r}"
                )
            if payload["outcome"]["context_refresh_count"] != 0:
                all_errors.append(
                    f"vector_publish_denied: expected context_refresh_count 0, got {payload['outcome']['context_refresh_count']!r}"
                )
            if payload["outcome"]["stale_retry_count"] != 0:
                all_errors.append(
                    f"vector_publish_denied: expected stale_retry_count 0, got {payload['outcome']['stale_retry_count']!r}"
                )
            if "STALE_REVISION" in errors:
                all_errors.append("vector_publish_denied: did not expect STALE_REVISION in a permission failure scenario")
            if "PERMISSION_DENIED" not in errors:
                all_errors.append("vector_publish_denied: expected PERMISSION_DENIED action result")
            if not {"wait_for_export", "confirm_publish", "publish"}.issubset(trace_steps):
                all_errors.append("vector_publish_denied: expected wait/confirm/publish steps in trace")
            if payload["artifacts"].get("published_refs") != ["asset:home-hero:svg:v1", "asset:home-hero:png:v1"]:
                all_errors.append("vector_publish_denied: expected published refs to remain unchanged on failure")

        print()

    if all_errors:
        print("Runtime validation failed:")
        for error in all_errors:
            print(f"  - {error}")
        return 1

    print("Runtime validation passed for SheetApp and VectorForge scenarios.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
