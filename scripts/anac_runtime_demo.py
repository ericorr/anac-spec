#!/usr/bin/env python3
"""Run a toy ANAC executor against the bundled SheetApp example."""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT_DIR / "examples" / "example-sheetapp-0.1.2.json"

INTERPOLATION_PATTERN = re.compile(r"\$\{([^{}]+)\}")
STRING_LITERAL_PATTERN = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')


class Box(dict):
    """Dictionary wrapper with attribute access for CEL-like evaluation."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def wrap(value: Any) -> Any:
    if isinstance(value, dict):
        return Box({key: wrap(item) for key, item in value.items()})
    if isinstance(value, list):
        return [wrap(item) for item in value]
    return value


def unwrap(value: Any) -> Any:
    if isinstance(value, Box):
        return {key: unwrap(item) for key, item in value.items()}
    if isinstance(value, list):
        return [unwrap(item) for item in value]
    return value


def strip_string_literals(expr: str) -> str:
    return STRING_LITERAL_PATTERN.sub("", expr)


def find_matching_paren(expr: str, open_index: int) -> int:
    depth = 0
    quote: str | None = None
    for index in range(open_index, len(expr)):
        char = expr[index]
        if quote:
            if char == quote and expr[index - 1] != "\\":
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError(f"Unmatched parenthesis in expression: {expr}")


def find_receiver_start(expr: str, dot_index: int) -> int:
    depth = 0
    quote: str | None = None
    delimiters = set(" \t\n,+-*/%<>=!&|")
    index = dot_index - 1
    while index >= 0:
        char = expr[index]
        if quote:
            if char == quote and (index == 0 or expr[index - 1] != "\\"):
                quote = None
            index -= 1
            continue
        if char in {"'", '"'}:
            quote = char
            index -= 1
            continue
        if char in "])}":
            depth += 1
        elif char in "[({":
            if depth > 0:
                depth -= 1
            else:
                return index
        elif depth == 0 and char in delimiters:
            return index + 1
        index -= 1
    return 0


def split_macro_args(arg_string: str) -> tuple[str, str]:
    depth = 0
    quote: str | None = None
    for index, char in enumerate(arg_string):
        if quote:
            if char == quote and arg_string[index - 1] != "\\":
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            return arg_string[:index].strip(), arg_string[index + 1 :].strip()
    raise ValueError(f"Could not split macro args: {arg_string}")


def transform_cel(expr: str) -> str:
    transformed = expr.strip()
    transformed = transform_cel_macros(transformed)
    transformed = transformed.replace("&&", " and ")
    transformed = transformed.replace("||", " or ")
    transformed = re.sub(r"(?<![=!<>])!(?!=)", " not ", transformed)
    transformed = re.sub(r"\btrue\b", "True", transformed)
    transformed = re.sub(r"\bfalse\b", "False", transformed)
    transformed = re.sub(r"\bnull\b", "None", transformed)
    transformed = re.sub(r"\bsize\s*\(", "len(", transformed)
    return transformed


def transform_cel_macros(expr: str) -> str:
    result = expr
    while True:
        match = re.search(r"\.(exists|filter)\s*\(", result)
        if not match:
            return result
        macro = match.group(1)
        open_index = match.end() - 1
        close_index = find_matching_paren(result, open_index)
        receiver_start = find_receiver_start(result, match.start())
        receiver = result[receiver_start : match.start()]
        var_name, body = split_macro_args(result[open_index + 1 : close_index])
        replacement = f"cel_{macro}({transform_cel(receiver)}, lambda {var_name}: {transform_cel(body)})"
        result = result[:receiver_start] + replacement + result[close_index + 1 :]


def cel_exists(collection: list[Any], predicate) -> bool:
    return any(predicate(item) for item in collection)


def cel_filter(collection: list[Any], predicate) -> list[Any]:
    return [item for item in collection if predicate(item)]


def evaluate_cel(expr: str, env: dict[str, Any]) -> Any:
    compiled = transform_cel(expr)
    safe_globals = {
        "__builtins__": {},
        "len": len,
        "cel_exists": cel_exists,
        "cel_filter": cel_filter,
    }
    return eval(compiled, {**safe_globals, **env}, {})


def resolve_interpolations(value: Any, env: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: resolve_interpolations(item, env) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_interpolations(item, env) for item in value]
    if not isinstance(value, str):
        return value

    matches = list(INTERPOLATION_PATTERN.finditer(value))
    if not matches:
        return value
    if len(matches) == 1 and matches[0].span() == (0, len(value)):
        return unwrap(evaluate_cel(matches[0].group(1), env))

    parts: list[str] = []
    cursor = 0
    for match in matches:
        parts.append(value[cursor : match.start()])
        parts.append(str(unwrap(evaluate_cel(match.group(1), env))))
        cursor = match.end()
    parts.append(value[cursor:])
    return "".join(parts)


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class ActionExecution:
    result: dict[str, Any]
    emissions: dict[str, Any]


class MockSheetAppAdapter:
    """Small in-memory adapter that executes the SheetApp workflow."""

    def __init__(self) -> None:
        self.sheet_name = "Q1 Sales"
        self.headers = ["Rep", "Region", "Jan", "Feb", "Mar", "Total"]
        self.sheet_revision = "r88"
        self.frame_counter = 100
        self.revision_counter = 200
        self.sheet = {
            "name": self.sheet_name,
            "used_range": "A1:F14",
            "is_protected": False,
            "has_header_row": True,
            "column_types": {
                "A": "text",
                "B": "text",
                "C": "number",
                "D": "number",
                "E": "number",
                "F": "formula",
            },
        }
        self.selection = "cell:D7"
        self.cells: dict[str, dict[str, Any]] = {}
        self._seed_sheet()

    def _seed_sheet(self) -> None:
        header_values = {
            "A1": "Rep",
            "B1": "Region",
            "C1": "Jan",
            "D1": "Feb",
            "E1": "Mar",
            "F1": "Total",
        }
        for address, value in header_values.items():
            self.cells[address] = self._make_cell(address, value=value)
        reps = [
            ("Alex", "North"),
            ("Blair", "South"),
            ("Casey", "East"),
            ("Drew", "West"),
            ("Evan", "North"),
            ("Finley", "South"),
            ("Gray", "East"),
            ("Harper", "West"),
            ("Indy", "North"),
            ("Jules", "South"),
            ("Kai", "East"),
            ("Logan", "West"),
            ("Morgan", "North"),
        ]
        for row, (rep, region) in enumerate(reps, start=2):
            jan = 900 + row * 10
            feb = 1000 + row * 12
            mar = 1100 + row * 14
            total = jan + feb + mar
            self.cells[f"A{row}"] = self._make_cell(f"A{row}", value=rep)
            self.cells[f"B{row}"] = self._make_cell(f"B{row}", value=region)
            self.cells[f"C{row}"] = self._make_cell(f"C{row}", value=jan)
            self.cells[f"D{row}"] = self._make_cell(f"D{row}", value=feb)
            self.cells[f"E{row}"] = self._make_cell(f"E{row}", value=mar)
            self.cells[f"F{row}"] = self._make_cell(f"F{row}", value=total, formula=f"=SUM(C{row}:E{row})")

    def _make_cell(self, address: str, *, value: Any = None, formula: str | None = None) -> dict[str, Any]:
        return {
            "revision": self._next_revision(),
            "data": {
                "address": address,
                "value": value,
                "formula": formula,
                "is_locked": False,
                "dependencies": [],
                "format": {},
            },
        }

    def _next_revision(self) -> str:
        self.revision_counter += 1
        return f"r{self.revision_counter}"

    def _bump_sheet_revision(self) -> None:
        self.sheet_revision = self._next_revision()

    def build_context_frame(self, workflow_runtime: dict[str, Any] | None = None) -> dict[str, Any]:
        workflow_runtime = workflow_runtime or {}
        self.frame_counter += 1
        active_workflows = []
        if workflow_runtime:
            active_workflows.append(
                {
                    "workflow_id": workflow_runtime["workflow_id"],
                    "lease_id": workflow_runtime["lease_id"],
                    "current_step": workflow_runtime["current_step"],
                    "progress": workflow_runtime["progress"],
                    "next_action_hint": workflow_runtime.get("next_action_hint"),
                    "can_rollback": True,
                    "context_refresh_count": workflow_runtime["context_refresh_count"],
                }
            )

        return {
            "frame_id": f"f-{self.frame_counter}",
            "emitted_at": iso_now(),
            "trigger": workflow_runtime.get("trigger", "system_event"),
            "subscription_id": "sub-default",
            "scope": {
                "mode": "selection",
                "root_refs": [f"sheet:{self.sheet_name}"],
                "entity_count": 2,
                "truncated": False,
                "next_cursor": None,
            },
            "application_state": {
                "screen": "sheet_view",
                "mode": "editing",
                "active_sheet": self.sheet_name,
            },
            "selection": [
                {
                    "entity_type": "cell",
                    "ref": self.selection,
                    "revision": self._snapshot_for_ref(self.selection)["revision"],
                }
            ],
            "permissions": ["sheet.edit", "sheet.format"],
            "available_actions": [
                {
                    "action_id": "set_cell_value",
                    "relevance": "primary",
                    "reason": "A cell is selected in edit mode",
                    "preconditions_met": True,
                },
                {
                    "action_id": "insert_row",
                    "relevance": "primary",
                    "reason": "Selection is within a data table",
                    "preconditions_met": True,
                },
                {
                    "action_id": "format_cells",
                    "relevance": "secondary",
                    "reason": "Formatting is available for selected ranges",
                    "preconditions_met": True,
                },
            ],
            "active_workflows": active_workflows,
            "entity_snapshots": [
                self._snapshot_for_ref(f"sheet:{self.sheet_name}"),
                self._snapshot_for_ref(self.selection),
            ],
            "warnings": [],
            "recent_events": [],
        }

    def _snapshot_for_ref(self, ref: str) -> dict[str, Any]:
        if ref.startswith("sheet:"):
            return {
                "entity_type": "sheet",
                "ref": ref,
                "revision": self.sheet_revision,
                "data": {
                    "name": self.sheet_name,
                    "used_range": self.sheet["used_range"],
                    "is_protected": self.sheet["is_protected"],
                    "last_data_row": self._last_used_row(),
                    "has_header_row": self.sheet["has_header_row"],
                    "column_types": self.sheet["column_types"],
                },
            }
        if ref.startswith("cell:"):
            address = ref.split(":", 1)[1]
            cell = self.cells[address]
            return {
                "entity_type": "cell",
                "ref": ref,
                "revision": cell["revision"],
                "data": copy.deepcopy(cell["data"]),
            }
        raise KeyError(f"Unsupported ref: {ref}")

    def observe_step(self, step_id: str, resolved_reads: list[str], resolved_inputs: dict[str, Any]) -> dict[str, Any]:
        if step_id == "read_table":
            snapshot = self._snapshot_for_ref(f"sheet:{self.sheet_name}")
            return {
                "sheet_name": snapshot["data"]["name"],
                "sheet_revision": snapshot["revision"],
                "last_data_row": snapshot["data"]["last_data_row"],
                "has_header": snapshot["data"]["has_header_row"],
                "column_types": snapshot["data"]["column_types"],
            }
        if step_id == "read_summary_row_for_formatting":
            refs = [self._snapshot_for_ref(ref) for ref in resolved_reads]
            return {
                "rev_a": refs[0]["revision"],
                "rev_b": refs[1]["revision"],
                "rev_c": refs[2]["revision"],
                "rev_d": refs[3]["revision"],
                "rev_e": refs[4]["revision"],
                "rev_f": refs[5]["revision"],
            }
        if step_id == "read_label_target":
            return {"label_cell_revision": self._snapshot_for_ref(resolved_reads[0])["revision"]}
        if step_id == "read_formula_target":
            return {"target_cell_revision": self._snapshot_for_ref(resolved_reads[0])["revision"]}
        if step_id == "refresh_context":
            return {"refreshed": True}
        return {}

    def invoke_action(
        self,
        step_id: str,
        action_id: str,
        params: dict[str, Any],
        expected_revisions: list[dict[str, Any]],
        context_frame: dict[str, Any],
    ) -> ActionExecution:
        if action_id == "insert_row":
            return self._insert_row(params, expected_revisions)
        if action_id == "set_cell_value":
            return self._set_cell_value(params, expected_revisions)
        if action_id == "format_cells":
            return self._format_cells(params, expected_revisions)
        raise KeyError(f"Unsupported action: {action_id}")

    def simulate_external_change(self, step_id: str, expected_revisions: list[dict[str, Any]]) -> dict[str, Any]:
        """Mutate underlying state without updating the current frame to force stale revisions."""
        event = {
            "kind": "simulated_external_edit",
            "step_id": step_id,
            "timestamp": iso_now(),
            "mutated": [],
        }
        if not expected_revisions:
            return event

        first = expected_revisions[0]
        ref = first["ref"]
        if ref.startswith("sheet:"):
            target = "D14"
            self.cells[target]["data"]["value"] += 1
            self.cells[target]["revision"] = self._next_revision()
            event["mutated"].append(
                {
                    "entity_type": "cell",
                    "ref": f"cell:{target}",
                    "revision": self.cells[target]["revision"],
                    "note": "Incremented a sales value to simulate a concurrent user edit",
                }
            )
            self._bump_sheet_revision()
            event["mutated"].append(
                {
                    "entity_type": "sheet",
                    "ref": ref,
                    "revision": self.sheet_revision,
                    "note": "Bumped sheet revision after concurrent edit",
                }
            )
            return event

        if ref.startswith("cell:"):
            address = ref.split(":", 1)[1]
            cell = self.cells[address]
            if isinstance(cell["data"]["value"], (int, float)):
                cell["data"]["value"] += 1
            elif cell["data"]["value"] is None:
                cell["data"]["value"] = "external-edit"
            else:
                cell["data"]["value"] = f"{cell['data']['value']}*"
            cell["revision"] = self._next_revision()
            event["mutated"].append(
                {
                    "entity_type": "cell",
                    "ref": ref,
                    "revision": cell["revision"],
                    "note": "Mutated cell to invalidate expected revision",
                }
            )
            return event

        raise KeyError(f"Unsupported simulated ref: {ref}")

    def _check_expected_revisions(self, expected_revisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        stale: list[dict[str, Any]] = []
        for item in expected_revisions:
            current = self._snapshot_for_ref(item["ref"])
            if current["revision"] != item["revision"]:
                stale.append(
                    {
                        "entity_type": current["entity_type"],
                        "ref": current["ref"],
                        "revision": current["revision"],
                    }
                )
        return stale

    def _failure(self, action_id: str, code: str, message: str, stale_entities: list[dict[str, Any]] | None = None) -> ActionExecution:
        return ActionExecution(
            result={
                "action_id": action_id,
                "status": "failure",
                "timestamp": iso_now(),
                "error": {
                    "code": code,
                    "message": message,
                    "retryable": code == "STALE_REVISION",
                    "recovery_options": [
                        {
                            "action_id": "refresh_context",
                            "description": "Re-read state and recompute workflow inputs",
                        }
                    ]
                    if code == "STALE_REVISION"
                    else [],
                    "stale_entities": stale_entities or [],
                },
                "warnings": [],
                "undo_token": None,
            },
            emissions={},
        )

    def _success(self, action_id: str, data: dict[str, Any], modified_refs: list[str], user_visible_effect: str) -> dict[str, Any]:
        return {
            "action_id": action_id,
            "status": "success",
            "timestamp": iso_now(),
            "data": data,
            "state_delta": {
                "created": [],
                "modified": [self._snapshot_for_ref(ref) for ref in modified_refs],
                "deleted": [],
            },
            "user_visible_effect": user_visible_effect,
            "warnings": [],
            "undo_token": None,
        }

    def _insert_row(self, params: dict[str, Any], expected_revisions: list[dict[str, Any]]) -> ActionExecution:
        stale = self._check_expected_revisions(expected_revisions)
        if stale:
            return self._failure("insert_row", "STALE_REVISION", "Sheet changed before row insertion", stale)

        before_row = int(params["before_row"])
        shifted: dict[str, dict[str, Any]] = {}
        for address in sorted(self.cells.keys(), key=self._row_sort_key, reverse=True):
            row = self._row_number(address)
            if row >= before_row:
                new_address = self._shift_address(address, 1)
                shifted[new_address] = self.cells[address]
                shifted[new_address]["data"]["address"] = new_address
            else:
                shifted[address] = self.cells[address]
        self.cells = shifted
        for column in "ABCDEF":
            address = f"{column}{before_row}"
            self.cells[address] = self._make_cell(address)
        self.sheet["used_range"] = f"A1:F{self._last_used_row()}"
        self._bump_sheet_revision()

        result = self._success(
            "insert_row",
            data={"inserted_range": f"{before_row}:{before_row}", "new_last_row": self._last_used_row()},
            modified_refs=[f"sheet:{self.sheet_name}"],
            user_visible_effect=f"Inserted row {before_row} in {self.sheet_name}",
        )
        return ActionExecution(result=result, emissions={"summary_row_number": before_row})

    def _set_cell_value(self, params: dict[str, Any], expected_revisions: list[dict[str, Any]]) -> ActionExecution:
        stale = self._check_expected_revisions(expected_revisions)
        if stale:
            return self._failure("set_cell_value", "STALE_REVISION", "Target cell changed before write", stale)

        address = params["address"]
        cell = self.cells.setdefault(address, self._make_cell(address))
        value = params["value"]
        if isinstance(value, str) and value.startswith("="):
            cell["data"]["formula"] = value
            cell["data"]["value"] = self._evaluate_formula(value)
        else:
            cell["data"]["formula"] = None
            cell["data"]["value"] = value
        cell["revision"] = self._next_revision()
        self.sheet["used_range"] = f"A1:F{self._last_used_row()}"

        result = self._success(
            "set_cell_value",
            data={
                "address": address,
                "computed_value": cell["data"]["value"],
                "affected_cells": 1,
            },
            modified_refs=[f"cell:{address}"],
            user_visible_effect=f"Updated {address}",
        )
        return ActionExecution(result=result, emissions={})

    def _format_cells(self, params: dict[str, Any], expected_revisions: list[dict[str, Any]]) -> ActionExecution:
        stale = self._check_expected_revisions(expected_revisions)
        if stale:
            return self._failure("format_cells", "STALE_REVISION", "Target range changed before formatting", stale)

        start, end = params["range"].split(":")
        refs = []
        for address in self._expand_range(start, end):
            cell = self.cells[address]
            cell["data"]["format"].update(copy.deepcopy(params["format"]))
            cell["revision"] = self._next_revision()
            refs.append(f"cell:{address}")

        result = self._success(
            "format_cells",
            data={"range": params["range"], "applied_properties": sorted(params["format"].keys())},
            modified_refs=refs,
            user_visible_effect=f"Formatted range {params['range']}",
        )
        return ActionExecution(result=result, emissions={})

    def _evaluate_formula(self, formula: str) -> Any:
        match = re.fullmatch(r"=SUM\(([A-Z]+)(\d+):([A-Z]+)(\d+)\)", formula)
        if not match:
            return formula
        start_col, start_row, end_col, end_row = match.groups()
        total = 0
        for row in range(int(start_row), int(end_row) + 1):
            for column in range(ord(start_col), ord(end_col) + 1):
                address = f"{chr(column)}{row}"
                total += self.cells[address]["data"]["value"] or 0
        return total

    def _expand_range(self, start: str, end: str) -> list[str]:
        start_col, start_row = start[0], int(start[1:])
        end_col, end_row = end[0], int(end[1:])
        addresses = []
        for row in range(start_row, end_row + 1):
            for column in range(ord(start_col), ord(end_col) + 1):
                addresses.append(f"{chr(column)}{row}")
        return addresses

    def _row_sort_key(self, address: str) -> tuple[int, str]:
        return (self._row_number(address), address)

    def _row_number(self, address: str) -> int:
        return int(re.search(r"(\d+)$", address).group(1))

    def _shift_address(self, address: str, delta: int) -> str:
        column = re.match(r"([A-Z]+)", address).group(1)
        row = self._row_number(address)
        return f"{column}{row + delta}"

    def _last_used_row(self) -> int:
        return max(self._row_number(address) for address in self.cells)

    def summary_row_snapshot(self, row: int | None = None) -> dict[str, Any]:
        if row is None:
            return {}
        return {
            f"{column}{row}": copy.deepcopy(self.cells[f"{column}{row}"]["data"])
            for column in "ABCDEF"
            if f"{column}{row}" in self.cells
        }


class WorkflowExecutor:
    """Tiny state-machine executor for the bundled demo manifest."""

    def __init__(
        self,
        manifest: dict[str, Any],
        adapter: MockSheetAppAdapter,
        force_stale_step: str | None = None,
        force_stale_count: int = 1,
    ) -> None:
        self.manifest = manifest
        self.adapter = adapter
        self.actions = {action["id"]: action for action in manifest["static"]["actions"]}
        self.force_stale_step = force_stale_step
        self.force_stale_count = max(force_stale_count, 0)
        self._forced_remaining: dict[str, int] = {}
        if self.force_stale_step and self.force_stale_count:
            self._forced_remaining[self.force_stale_step] = self.force_stale_count

    def run(self, workflow_id: str) -> dict[str, Any]:
        workflow = next(item for item in self.manifest["static"]["workflows"] if item["id"] == workflow_id)
        runtime = {
            "workflow_id": workflow["id"],
            "lease_id": "lease-demo",
            "started_at": iso_now(),
            "expires_at": iso_now(),
            "context_refresh_count": 0,
            "max_context_refreshes": workflow.get("max_context_refreshes", 0),
            "current_step": workflow["entry_point"],
            "progress": 0.0,
        }
        context_frame = self.adapter.build_context_frame(runtime)
        outcome = self._run_step_machine(
            workflow=workflow,
            context_frame=context_frame,
            runtime=runtime,
            inputs={},
            current=None,
        )
        summary_row_number = outcome["outputs"].get("insert_summary_row", {}).get("summary_row_number")
        return {
            "workflow_id": workflow_id,
            "status": outcome["status"],
            "outcome": outcome["outcome"],
            "trace": outcome["trace"],
            "final_context_frame": outcome["context_frame"],
            "summary_row": self.adapter.summary_row_snapshot(summary_row_number) if summary_row_number else None,
        }

    def _run_step_machine(
        self,
        workflow: dict[str, Any],
        context_frame: dict[str, Any],
        runtime: dict[str, Any],
        inputs: dict[str, Any],
        current: Any,
    ) -> dict[str, Any]:
        step_map = {step["id"]: step for step in workflow["steps"]}
        local_outputs: dict[str, dict[str, Any]] = {}
        trace: list[dict[str, Any]] = []
        step_order = list(step_map.keys())
        step_id = workflow["entry_point"]
        terminal_status = "success"
        outcome_info: dict[str, Any] = {
            "status": None,
            "disposition": None,
            "reason": None,
            "terminal_step": None,
            "terminal_transition": None,
            "last_error_code": None,
            "context_refresh_count": 0,
            "stale_retry_count": 0,
        }

        while step_id != "end":
            step = step_map[step_id]
            runtime["current_step"] = step_id
            runtime["progress"] = step_order.index(step_id) / max(len(step_order), 1)
            env = self._build_env(context_frame, runtime, local_outputs, inputs, current)

            resolved_reads = resolve_interpolations(step.get("reads_refs", []), env)
            resolved_inputs = resolve_interpolations(step.get("inputs", {}), env)
            resolved_expected = resolve_interpolations(step.get("expected_revisions", []), env)

            if step["kind"] == "observe":
                emissions = self.adapter.observe_step(step_id, resolved_reads, resolved_inputs)
                local_outputs[step_id] = emissions
                eval_env = self._build_env(context_frame, runtime, local_outputs, inputs, current)
                success = True
                if "predicate" in step:
                    success = bool(evaluate_cel(step["predicate"], eval_env))
                transition = "success" if success else "failure"
                next_step = step["on"][transition]
                step_result = None

            elif step["kind"] == "decide":
                decision = bool(evaluate_cel(step["predicate"], env))
                local_outputs[step_id] = {"decision": decision}
                transition = "on_true" if decision else "on_false"
                next_step = step[transition]
                step_result = None

            elif step["kind"] == "mutate":
                simulated_event = None
                if self._forced_remaining.get(step_id, 0) > 0:
                    simulated_event = self.adapter.simulate_external_change(step_id, resolved_expected)
                    self._forced_remaining[step_id] -= 1
                execution = self.adapter.invoke_action(step_id, step["action"], resolved_inputs, resolved_expected, context_frame)
                step_result = execution.result
                local_outputs[step_id] = {
                    **unwrap(step_result.get("data", {})),
                    **unwrap(execution.emissions),
                }
                if step_result["status"] == "failure" and step_result["error"]["code"] == "STALE_REVISION":
                    transition = "stale_revision"
                    runtime["context_refresh_count"] += 1
                    outcome_info["last_error_code"] = "STALE_REVISION"
                    outcome_info["stale_retry_count"] = runtime["context_refresh_count"]
                    terminal_status = "stale_revision"
                elif step_result["status"] == "failure":
                    transition = "failure"
                    terminal_status = "failure"
                    if outcome_info["disposition"] is None:
                        outcome_info.update(
                            {
                                "status": "failure",
                                "disposition": "failed_non_retryable",
                                "reason": "action_failure",
                                "terminal_step": step_id,
                                "terminal_transition": transition,
                                "last_error_code": step_result["error"]["code"],
                            }
                        )
                else:
                    transition = "success"
                    terminal_status = step_result["status"]
                next_step = step["on"][transition]
                context_frame = self.adapter.build_context_frame({**runtime, "trigger": "agent_action"})

            elif step["kind"] == "subflow":
                items = [None]
                if "foreach" in step:
                    items = unwrap(evaluate_cel(step["foreach"], env))
                subflow = next(item for item in workflow["subflows"] if item["id"] == step["workflow_ref"])
                sub_results = []
                had_failure = False
                for item in items:
                    sub_inputs = resolve_interpolations(step.get("inputs", {}), self._build_env(context_frame, runtime, local_outputs, inputs, item))
                    outcome = self._run_step_machine(
                        workflow=subflow,
                        context_frame=context_frame,
                        runtime=runtime,
                        inputs=sub_inputs,
                        current=item,
                    )
                    sub_results.append(outcome)
                    context_frame = outcome["context_frame"]
                    if outcome["status"] != "success":
                        had_failure = True
                        if not step.get("continue_on_error", False):
                            break
                local_outputs[step_id] = {"results": sub_results}
                if had_failure and step.get("continue_on_error", False):
                    transition = "partial"
                elif had_failure:
                    transition = "failure"
                    terminal_status = "failure"
                else:
                    transition = "success"
                next_step = step["on"][transition]
                step_result = None

            elif step["kind"] == "confirm":
                transition = "approved"
                next_step = step["on"][transition]
                step_result = {"status": "approved"}
                local_outputs[step_id] = {"approved": True}

            elif step["kind"] == "wait":
                transition = "success"
                next_step = step["on"][transition]
                step_result = {"status": "ready"}
                local_outputs[step_id] = {"ready": True}

            else:
                raise NotImplementedError(step["kind"])

            trace.append(
                {
                    "step_id": step_id,
                    "kind": step["kind"],
                    "transition": transition,
                    "resolved_reads": resolved_reads,
                    "resolved_inputs": resolved_inputs,
                    "expected_revisions": resolved_expected,
                    "emissions": local_outputs.get(step_id, {}),
                    "action_result": step_result,
                    "simulated_external_event": simulated_event if step["kind"] == "mutate" else None,
                    "frame_id": context_frame["frame_id"],
                    "next_step": next_step,
                }
            )

            if step["kind"] == "observe" and step_id == "refresh_context" and transition == "failure" and outcome_info["disposition"] is None:
                outcome_info.update(
                    {
                        "status": "failure",
                        "disposition": "failed_retry_exhausted",
                        "reason": "max_context_refreshes_exceeded",
                        "terminal_step": step_id,
                        "terminal_transition": transition,
                        "last_error_code": outcome_info["last_error_code"] or "STALE_REVISION",
                    }
                )
                terminal_status = "failure"
            step_id = next_step

        if outcome_info["disposition"] is None:
            recovered = runtime["context_refresh_count"] > 0
            outcome_info.update(
                {
                    "status": "success" if terminal_status != "failure" else terminal_status,
                    "disposition": "completed_after_retry" if recovered else "completed",
                    "reason": "workflow_reached_end_after_retry" if recovered else "workflow_reached_end",
                    "terminal_step": trace[-1]["step_id"] if trace else workflow["entry_point"],
                    "terminal_transition": trace[-1]["transition"] if trace else None,
                }
            )
        outcome_info["context_refresh_count"] = runtime["context_refresh_count"]

        return {
            "status": outcome_info["status"],
            "outcome": outcome_info,
            "trace": trace,
            "context_frame": context_frame,
            "outputs": local_outputs,
        }

    def _build_env(
        self,
        context_frame: dict[str, Any],
        runtime: dict[str, Any],
        steps: dict[str, dict[str, Any]],
        inputs: dict[str, Any],
        current: Any,
    ) -> dict[str, Any]:
        return {
            "context": wrap(context_frame),
            "workflow": wrap(runtime),
            "steps": wrap(steps),
            "inputs": wrap(inputs),
            "current": wrap(current),
        }


def load_manifest(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Path to the ANAC manifest to execute")
    parser.add_argument("--workflow", default="add_summary_row", help="Workflow id to execute")
    parser.add_argument("--trace-only", action="store_true", help="Print only the step trace")
    parser.add_argument(
        "--force-stale-step",
        help="Inject one concurrent external edit before the named mutate step executes",
    )
    parser.add_argument(
        "--force-stale-count",
        type=int,
        default=1,
        help="How many times to inject a concurrent external edit before the named step executes",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = load_manifest(Path(args.manifest))
    adapter = MockSheetAppAdapter()
    executor = WorkflowExecutor(
        manifest,
        adapter,
        force_stale_step=args.force_stale_step,
        force_stale_count=args.force_stale_count,
    )
    result = executor.run(args.workflow)
    payload = result["trace"] if args.trace_only else result
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
