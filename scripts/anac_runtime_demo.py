#!/usr/bin/env python3
"""Run a toy ANAC executor against the bundled demo manifests."""

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
CELL_REF_PATTERN = re.compile(r"([A-Z]+)(\d+)$")


class Box(dict):
    """Dictionary wrapper with attribute access for CEL-like evaluation."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


@dataclass
class ActionExecution:
    result: dict[str, Any] | None
    emissions: dict[str, Any]


class BaseDemoAdapter:
    def __init__(self, denied_permissions: set[str] | None = None) -> None:
        self.denied_permissions = denied_permissions or set()

    def build_context_frame(self, workflow_runtime: dict[str, Any] | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def observe_step(
        self,
        step_id: str,
        resolved_reads: list[str],
        resolved_inputs: dict[str, Any],
        context_frame: dict[str, Any],
    ) -> ActionExecution:
        return ActionExecution(result=None, emissions={})

    def invoke_action(
        self,
        step_id: str,
        action_id: str,
        params: dict[str, Any],
        expected_revisions: list[dict[str, Any]],
        context_frame: dict[str, Any],
    ) -> ActionExecution:
        raise NotImplementedError

    def simulate_external_change(self, step_id: str, expected_revisions: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "kind": "simulated_external_edit",
            "step_id": step_id,
            "timestamp": iso_now(),
            "mutated": [],
        }

    def resolve_watch_snapshot(self, entity_type: str, ref: str) -> dict[str, Any]:
        raise NotImplementedError

    def advance_async(self, entity_type: str, ref: str) -> dict[str, Any] | None:
        return None

    def confirm_step(
        self,
        step_id: str,
        prompt: str,
        payload: dict[str, Any] | None,
        context_frame: dict[str, Any],
    ) -> str:
        del step_id, prompt, payload, context_frame
        return "approved"

    def build_artifacts(self, outputs: dict[str, dict[str, Any]], context_frame: dict[str, Any]) -> dict[str, Any]:
        del outputs, context_frame
        return {}


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
                return index + 1
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


def make_failure_result(
    action_id: str,
    code: str,
    message: str,
    *,
    retryable: bool,
    stale_entities: list[dict[str, Any]] | None = None,
    recovery_options: list[dict[str, Any]] | None = None,
) -> ActionExecution:
    return ActionExecution(
        result={
            "action_id": action_id,
            "status": "failure",
            "timestamp": iso_now(),
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "recovery_options": recovery_options or [],
                "stale_entities": stale_entities or [],
            },
            "warnings": [],
            "undo_token": None,
        },
        emissions={},
    )


def make_success_result(
    action_id: str,
    data: dict[str, Any],
    *,
    created: list[dict[str, Any]] | None = None,
    modified: list[dict[str, Any]] | None = None,
    deleted: list[dict[str, Any]] | None = None,
    user_visible_effect: str | None = None,
) -> dict[str, Any]:
    result = {
        "action_id": action_id,
        "status": "success",
        "timestamp": iso_now(),
        "data": data,
        "warnings": [],
        "undo_token": None,
    }
    if created is not None or modified is not None or deleted is not None:
        result["state_delta"] = {
            "created": created or [],
            "modified": modified or [],
            "deleted": deleted or [],
        }
    if user_visible_effect is not None:
        result["user_visible_effect"] = user_visible_effect
    return result


class MockSheetAppAdapter(BaseDemoAdapter):
    def __init__(self, denied_permissions: set[str] | None = None) -> None:
        super().__init__(denied_permissions)
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

    def _permissions(self) -> list[str]:
        return [perm for perm in ["sheet.edit", "sheet.format"] if perm not in self.denied_permissions]

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

        entity_snapshots = [
            self.resolve_watch_snapshot("sheet", f"sheet:{self.sheet_name}"),
            self.resolve_watch_snapshot("cell", self.selection),
        ]
        return {
            "frame_id": f"f-{self.frame_counter}",
            "emitted_at": iso_now(),
            "trigger": workflow_runtime.get("trigger", "system_event"),
            "subscription_id": "sub-default",
            "scope": {
                "mode": "selection",
                "root_refs": [f"sheet:{self.sheet_name}"],
                "entity_count": len(entity_snapshots),
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
                    "revision": self.resolve_watch_snapshot("cell", self.selection)["revision"],
                }
            ],
            "permissions": self._permissions(),
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
            "entity_snapshots": entity_snapshots,
            "warnings": [],
            "recent_events": [],
        }

    def resolve_watch_snapshot(self, entity_type: str, ref: str) -> dict[str, Any]:
        if entity_type == "sheet" or ref.startswith("sheet:"):
            return {
                "entity_type": "sheet",
                "ref": f"sheet:{self.sheet_name}",
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
        if entity_type == "cell" or ref.startswith("cell:"):
            address = ref.split(":", 1)[1]
            cell = self.cells[address]
            return {
                "entity_type": "cell",
                "ref": ref,
                "revision": cell["revision"],
                "data": copy.deepcopy(cell["data"]),
            }
        raise KeyError(f"Unsupported ref: {ref}")

    def observe_step(
        self,
        step_id: str,
        resolved_reads: list[str],
        resolved_inputs: dict[str, Any],
        context_frame: dict[str, Any],
    ) -> ActionExecution:
        del resolved_inputs, context_frame
        if step_id == "read_table":
            snapshot = self.resolve_watch_snapshot("sheet", f"sheet:{self.sheet_name}")
            return ActionExecution(
                result=None,
                emissions={
                    "sheet_name": snapshot["data"]["name"],
                    "sheet_revision": snapshot["revision"],
                    "last_data_row": snapshot["data"]["last_data_row"],
                    "has_header": snapshot["data"]["has_header_row"],
                    "column_types": snapshot["data"]["column_types"],
                },
            )
        if step_id == "read_summary_row_for_formatting":
            refs = [self.resolve_watch_snapshot("cell", ref) for ref in resolved_reads]
            return ActionExecution(
                result=None,
                emissions={
                    "rev_a": refs[0]["revision"],
                    "rev_b": refs[1]["revision"],
                    "rev_c": refs[2]["revision"],
                    "rev_d": refs[3]["revision"],
                    "rev_e": refs[4]["revision"],
                    "rev_f": refs[5]["revision"],
                },
            )
        if step_id == "read_label_target":
            return ActionExecution(
                result=None,
                emissions={"label_cell_revision": self.resolve_watch_snapshot("cell", resolved_reads[0])["revision"]},
            )
        if step_id == "read_formula_target":
            return ActionExecution(
                result=None,
                emissions={"target_cell_revision": self.resolve_watch_snapshot("cell", resolved_reads[0])["revision"]},
            )
        if step_id == "refresh_context":
            return ActionExecution(result=None, emissions={"refreshed": True})
        return ActionExecution(result=None, emissions={})

    def invoke_action(
        self,
        step_id: str,
        action_id: str,
        params: dict[str, Any],
        expected_revisions: list[dict[str, Any]],
        context_frame: dict[str, Any],
    ) -> ActionExecution:
        del step_id, context_frame
        if action_id == "insert_row":
            return self._insert_row(params, expected_revisions)
        if action_id == "set_cell_value":
            return self._set_cell_value(params, expected_revisions)
        if action_id == "format_cells":
            return self._format_cells(params, expected_revisions)
        raise KeyError(f"Unsupported action: {action_id}")

    def simulate_external_change(self, step_id: str, expected_revisions: list[dict[str, Any]]) -> dict[str, Any]:
        event = {
            "kind": "simulated_external_edit",
            "step_id": step_id,
            "timestamp": iso_now(),
            "mutated": [],
        }
        if not expected_revisions:
            return event

        ref = expected_revisions[0]["ref"]
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

        return event

    def _check_expected_revisions(self, expected_revisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        stale = []
        for item in expected_revisions:
            current = self.resolve_watch_snapshot(item["entity_type"], item["ref"])
            if current["revision"] != item["revision"]:
                stale.append(
                    {
                        "entity_type": current["entity_type"],
                        "ref": current["ref"],
                        "revision": current["revision"],
                    }
                )
        return stale

    def _insert_row(self, params: dict[str, Any], expected_revisions: list[dict[str, Any]]) -> ActionExecution:
        stale = self._check_expected_revisions(expected_revisions)
        if stale:
            return make_failure_result(
                "insert_row",
                "STALE_REVISION",
                "Sheet changed before row insertion",
                retryable=True,
                stale_entities=stale,
                recovery_options=[
                    {
                        "action_id": "refresh_context",
                        "description": "Re-read state and recompute workflow inputs",
                    }
                ],
            )

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

        result = make_success_result(
            "insert_row",
            {
                "inserted_range": f"{before_row}:{before_row}",
                "new_last_row": self._last_used_row(),
            },
            modified=[self.resolve_watch_snapshot("sheet", f"sheet:{self.sheet_name}")],
            user_visible_effect=f"Inserted row {before_row} in {self.sheet_name}",
        )
        return ActionExecution(result=result, emissions={"summary_row_number": before_row})

    def _set_cell_value(self, params: dict[str, Any], expected_revisions: list[dict[str, Any]]) -> ActionExecution:
        stale = self._check_expected_revisions(expected_revisions)
        if stale:
            return make_failure_result(
                "set_cell_value",
                "STALE_REVISION",
                "Target cell changed before write",
                retryable=True,
                stale_entities=stale,
                recovery_options=[
                    {
                        "action_id": "refresh_context",
                        "description": "Re-read state and recompute workflow inputs",
                    }
                ],
            )

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

        result = make_success_result(
            "set_cell_value",
            {
                "address": address,
                "computed_value": cell["data"]["value"],
                "affected_cells": 1,
            },
            modified=[self.resolve_watch_snapshot("cell", f"cell:{address}")],
            user_visible_effect=f"Updated {address}",
        )
        return ActionExecution(result=result, emissions={})

    def _format_cells(self, params: dict[str, Any], expected_revisions: list[dict[str, Any]]) -> ActionExecution:
        stale = self._check_expected_revisions(expected_revisions)
        if stale:
            return make_failure_result(
                "format_cells",
                "STALE_REVISION",
                "Target range changed before formatting",
                retryable=True,
                stale_entities=stale,
                recovery_options=[
                    {
                        "action_id": "refresh_context",
                        "description": "Re-read state and recompute workflow inputs",
                    }
                ],
            )

        start, end = params["range"].split(":")
        modified_refs = []
        for address in self._expand_range(start, end):
            cell = self.cells[address]
            cell["data"]["format"].update(copy.deepcopy(params["format"]))
            cell["revision"] = self._next_revision()
            modified_refs.append(self.resolve_watch_snapshot("cell", f"cell:{address}"))

        result = make_success_result(
            "format_cells",
            {
                "range": params["range"],
                "applied_properties": sorted(params["format"].keys()),
            },
            modified=modified_refs,
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
        start_col, start_row = CELL_REF_PATTERN.match(start).groups()
        end_col, end_row = CELL_REF_PATTERN.match(end).groups()
        addresses = []
        for row in range(int(start_row), int(end_row) + 1):
            for column in range(ord(start_col), ord(end_col) + 1):
                addresses.append(f"{chr(column)}{row}")
        return addresses

    def _row_sort_key(self, address: str) -> tuple[int, str]:
        return (self._row_number(address), address)

    def _row_number(self, address: str) -> int:
        return int(CELL_REF_PATTERN.search(address).group(2))

    def _shift_address(self, address: str, delta: int) -> str:
        match = CELL_REF_PATTERN.match(address)
        return f"{match.group(1)}{int(match.group(2)) + delta}"

    def _last_used_row(self) -> int:
        return max(self._row_number(address) for address in self.cells)

    def build_artifacts(self, outputs: dict[str, dict[str, Any]], context_frame: dict[str, Any]) -> dict[str, Any]:
        del context_frame
        row = outputs.get("insert_summary_row", {}).get("summary_row_number")
        if row is None:
            return {"summary_row": None}
        return {
            "summary_row": {
                f"{column}{row}": copy.deepcopy(self.cells[f"{column}{row}"]["data"])
                for column in "ABCDEF"
                if f"{column}{row}" in self.cells
            }
        }


class MockVectorForgeAdapter(BaseDemoAdapter):
    def __init__(self, denied_permissions: set[str] | None = None) -> None:
        super().__init__(denied_permissions)
        self.frame_counter = 200
        self.revision_counter = 500
        self.job_counter = 1
        self.artboard_ref = "artboard:home-hero"
        self.group_ref = "layer:icon-cluster"
        self.artboard = {
            "revision": "r41",
            "data": {
                "name": "Home Hero",
                "width": 1440,
                "height": 900,
                "layer_count": 3,
            },
        }
        self.layers: dict[str, dict[str, Any]] = {
            "layer:icon-cluster": {
                "revision": "r9",
                "data": {
                    "name": "Icon Cluster",
                    "kind": "group",
                    "visible": True,
                    "locked": False,
                    "x": 713,
                    "y": 402,
                    "width": 240,
                    "height": 64,
                },
            },
            "layer:headline": {
                "revision": "r10",
                "data": {
                    "name": "Headline",
                    "kind": "text",
                    "visible": True,
                    "locked": False,
                    "x": 120,
                    "y": 180,
                    "width": 820,
                    "height": 88,
                    "contrast_ratio": 3.1,
                    "style_token": "text/default",
                },
            },
            "layer:subhead": {
                "revision": "r11",
                "data": {
                    "name": "Subhead",
                    "kind": "text",
                    "visible": True,
                    "locked": False,
                    "x": 120,
                    "y": 290,
                    "width": 760,
                    "height": 42,
                    "contrast_ratio": 4.8,
                    "style_token": "text/default",
                },
            },
        }
        self.selection = [self.artboard_ref, self.group_ref]
        self.export_jobs: dict[str, dict[str, Any]] = {}
        self.published_refs = ["asset:home-hero:svg:v1", "asset:home-hero:png:v1"]
        self.allowed_tokens = {
            "text/on-surface/high-contrast": 4.8,
            "text/accent/high-contrast": 4.6,
        }

    def _permissions(self) -> list[str]:
        base = ["design.edit", "asset.export", "asset.publish"]
        return [perm for perm in base if perm not in self.denied_permissions]

    def _next_revision(self) -> str:
        self.revision_counter += 1
        return f"r{self.revision_counter}"

    def _job_revision(self) -> str:
        self.revision_counter += 1
        return f"j{self.revision_counter}"

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

        entity_snapshots = [
            self.resolve_watch_snapshot("artboard", self.artboard_ref),
            self.resolve_watch_snapshot("layer", self.group_ref),
        ]
        if self.export_jobs:
            latest_ref = sorted(self.export_jobs.keys())[-1]
            entity_snapshots.append(self.resolve_watch_snapshot("export_job", latest_ref))

        permissions = self._permissions()
        artboard_snapshot = self.resolve_watch_snapshot("artboard", self.artboard_ref)
        group_snapshot = self.resolve_watch_snapshot("layer", self.group_ref)
        return {
            "frame_id": f"vf-{self.frame_counter}",
            "emitted_at": iso_now(),
            "trigger": workflow_runtime.get("trigger", "system_event"),
            "subscription_id": "sub-default",
            "scope": {
                "mode": "selection",
                "root_refs": list(self.selection),
                "entity_count": len(entity_snapshots),
                "truncated": False,
                "next_cursor": None,
            },
            "application_state": {
                "screen": "canvas",
                "mode": "design",
            },
            "selection": [
                {
                    "entity_type": artboard_snapshot["entity_type"],
                    "ref": artboard_snapshot["ref"],
                    "revision": artboard_snapshot["revision"],
                },
                {
                    "entity_type": group_snapshot["entity_type"],
                    "ref": group_snapshot["ref"],
                    "revision": group_snapshot["revision"],
                },
            ],
            "permissions": permissions,
            "available_actions": [
                {
                    "action_id": "analyze_contrast",
                    "relevance": "primary",
                    "reason": "Artboard is selected; accessibility audit is available",
                    "preconditions_met": True,
                },
                {
                    "action_id": "apply_style_token",
                    "relevance": "primary",
                    "reason": "Text layers are present on the selected artboard",
                    "preconditions_met": True,
                },
                {
                    "action_id": "snap_to_grid",
                    "relevance": "primary",
                    "reason": "A group layer is selected",
                    "preconditions_met": True,
                },
                {
                    "action_id": "export_asset",
                    "relevance": "primary",
                    "reason": "Artboard is selected and export is available",
                    "preconditions_met": "asset.export" in permissions,
                },
                {
                    "action_id": "publish_asset",
                    "relevance": "secondary",
                    "reason": "Publishing typically follows export",
                    "preconditions_met": "asset.publish" in permissions,
                },
            ],
            "active_workflows": active_workflows,
            "entity_snapshots": entity_snapshots,
            "warnings": [],
            "recent_events": [],
        }

    def resolve_watch_snapshot(self, entity_type: str, ref: str) -> dict[str, Any]:
        if entity_type == "artboard" or ref.startswith("artboard:"):
            return {
                "entity_type": "artboard",
                "ref": self.artboard_ref,
                "revision": self.artboard["revision"],
                "data": copy.deepcopy(self.artboard["data"]),
            }
        if entity_type == "layer" or ref.startswith("layer:"):
            layer = self.layers[ref]
            return {
                "entity_type": "layer",
                "ref": ref,
                "revision": layer["revision"],
                "data": copy.deepcopy(layer["data"]),
            }
        if entity_type == "export_job" or ref.startswith("export_job:"):
            job = self.export_jobs[ref]
            return {
                "entity_type": "export_job",
                "ref": ref,
                "revision": job["revision"],
                "data": copy.deepcopy(job["data"]),
            }
        raise KeyError(f"Unsupported ref: {ref}")

    def observe_step(
        self,
        step_id: str,
        resolved_reads: list[str],
        resolved_inputs: dict[str, Any],
        context_frame: dict[str, Any],
    ) -> ActionExecution:
        del resolved_reads, resolved_inputs, context_frame
        if step_id == "validate_selection":
            return ActionExecution(
                result=None,
                emissions={
                    "artboard_ref": self.artboard_ref,
                    "artboard_revision": self.artboard["revision"],
                    "group_ref": self.group_ref,
                    "group_revision": self.layers[self.group_ref]["revision"],
                },
            )
        if step_id == "refresh_context":
            return ActionExecution(result=None, emissions={"refreshed": True})
        return ActionExecution(result=None, emissions={})

    def invoke_action(
        self,
        step_id: str,
        action_id: str,
        params: dict[str, Any],
        expected_revisions: list[dict[str, Any]],
        context_frame: dict[str, Any],
    ) -> ActionExecution:
        del step_id, context_frame
        if action_id == "analyze_contrast":
            return self._analyze_contrast(params)
        if action_id == "apply_style_token":
            return self._apply_style_token(params, expected_revisions)
        if action_id == "snap_to_grid":
            return self._snap_to_grid(params, expected_revisions)
        if action_id == "export_asset":
            return self._export_asset(params, expected_revisions)
        if action_id == "publish_asset":
            return self._publish_asset(params)
        raise KeyError(f"Unsupported action: {action_id}")

    def simulate_external_change(self, step_id: str, expected_revisions: list[dict[str, Any]]) -> dict[str, Any]:
        event = {
            "kind": "simulated_external_edit",
            "step_id": step_id,
            "timestamp": iso_now(),
            "mutated": [],
        }
        if not expected_revisions:
            return event
        ref = expected_revisions[0]["ref"]
        if ref.startswith("layer:"):
            layer = self.layers[ref]
            layer["data"]["x"] += 3
            layer["revision"] = self._next_revision()
            event["mutated"].append(
                {
                    "entity_type": "layer",
                    "ref": ref,
                    "revision": layer["revision"],
                    "note": "Moved the layer to simulate a concurrent designer edit",
                }
            )
            return event
        if ref.startswith("artboard:"):
            self.artboard["revision"] = self._next_revision()
            event["mutated"].append(
                {
                    "entity_type": "artboard",
                    "ref": ref,
                    "revision": self.artboard["revision"],
                    "note": "Bumped artboard revision to simulate concurrent edits",
                }
            )
        return event

    def advance_async(self, entity_type: str, ref: str) -> dict[str, Any] | None:
        if entity_type != "export_job":
            return None
        job = self.export_jobs[ref]
        if job["data"]["status"] == "running":
            job["data"]["status"] = "completed"
            job["data"]["output_refs"] = [
                f"asset:home-hero:svg:v{self.job_counter}",
                f"asset:home-hero:png:v{self.job_counter}",
            ]
            job["revision"] = self._job_revision()
        return self.resolve_watch_snapshot("export_job", ref)

    def build_artifacts(self, outputs: dict[str, dict[str, Any]], context_frame: dict[str, Any]) -> dict[str, Any]:
        del context_frame
        export_job_ref = outputs.get("start_export", {}).get("export_job_ref")
        export_job = self.resolve_watch_snapshot("export_job", export_job_ref) if export_job_ref else None
        return {
            "published_refs": copy.deepcopy(self.published_refs),
            "group_position": {
                "x": self.layers[self.group_ref]["data"]["x"],
                "y": self.layers[self.group_ref]["data"]["y"],
            },
            "applied_tokens": {
                ref: layer["data"].get("style_token")
                for ref, layer in self.layers.items()
                if layer["data"]["kind"] == "text"
            },
            "export_job": export_job,
        }

    def _check_expected_revisions(self, expected_revisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        stale = []
        for item in expected_revisions:
            current = self.resolve_watch_snapshot(item["entity_type"], item["ref"])
            if current["revision"] != item["revision"]:
                stale.append(
                    {
                        "entity_type": current["entity_type"],
                        "ref": current["ref"],
                        "revision": current["revision"],
                    }
                )
        return stale

    def _analyze_contrast(self, params: dict[str, Any]) -> ActionExecution:
        del params
        failing = []
        passing_count = 0
        for ref, layer in self.layers.items():
            if layer["data"]["kind"] != "text":
                continue
            ratio = layer["data"].get("contrast_ratio", 0)
            if ratio < 4.5:
                failing.append(
                    {
                        "layer_ref": ref,
                        "layer_revision": layer["revision"],
                        "current_ratio": ratio,
                        "required_ratio": 4.5,
                        "recommended_token": "text/on-surface/high-contrast",
                    }
                )
            else:
                passing_count += 1
        if not failing and passing_count == 0:
            return make_failure_result(
                "analyze_contrast",
                "NO_TEXT_LAYERS",
                "Artboard contains no text layers",
                retryable=False,
            )
        result = make_success_result(
            "analyze_contrast",
            {
                "failing_layers": failing,
                "passing_count": passing_count,
            },
            created=[],
            modified=[],
            deleted=[],
            user_visible_effect="Ran contrast audit on the selected artboard",
        )
        return ActionExecution(
            result=result,
            emissions={
                "failing_text_layers": failing,
                "passing_count": passing_count,
            },
        )

    def _apply_style_token(self, params: dict[str, Any], expected_revisions: list[dict[str, Any]]) -> ActionExecution:
        stale = self._check_expected_revisions(expected_revisions)
        if stale:
            return make_failure_result(
                "apply_style_token",
                "STALE_REVISION",
                "Layer changed before token application",
                retryable=True,
                stale_entities=stale,
                recovery_options=[
                    {
                        "action_id": "refresh_context",
                        "description": "Re-read state and recompute workflow inputs",
                    }
                ],
            )

        token = params["token"]
        if token not in self.allowed_tokens:
            return make_failure_result(
                "apply_style_token",
                "TOKEN_NOT_FOUND",
                "The specified design token does not exist",
                retryable=False,
            )

        ref = params["layer_ref"]
        layer = self.layers[ref]
        layer["data"]["style_token"] = token
        layer["data"]["contrast_ratio"] = self.allowed_tokens[token]
        layer["revision"] = self._next_revision()
        snapshot = self.resolve_watch_snapshot("layer", ref)
        result = make_success_result(
            "apply_style_token",
            {
                "layer_ref": ref,
                "new_revision": snapshot["revision"],
                "applied_token": token,
                "new_contrast_ratio": snapshot["data"]["contrast_ratio"],
            },
            modified=[snapshot],
            user_visible_effect=f"Applied {token} to {snapshot['data']['name']}",
        )
        return ActionExecution(result=result, emissions={})

    def _snap_to_grid(self, params: dict[str, Any], expected_revisions: list[dict[str, Any]]) -> ActionExecution:
        stale = self._check_expected_revisions(expected_revisions)
        if stale:
            return make_failure_result(
                "snap_to_grid",
                "STALE_REVISION",
                "Layer moved before grid snap",
                retryable=True,
                stale_entities=stale,
                recovery_options=[
                    {
                        "action_id": "refresh_context",
                        "description": "Re-read state and recompute workflow inputs",
                    }
                ],
            )

        ref = params["ref"]
        layer = self.layers[ref]
        grid = int(params["grid_size"])
        old_position = {"x": layer["data"]["x"], "y": layer["data"]["y"]}
        layer["data"]["x"] = round(layer["data"]["x"] / grid) * grid
        layer["data"]["y"] = round(layer["data"]["y"] / grid) * grid
        layer["revision"] = self._next_revision()
        snapshot = self.resolve_watch_snapshot("layer", ref)
        result = make_success_result(
            "snap_to_grid",
            {
                "ref": ref,
                "new_revision": snapshot["revision"],
                "old_position": old_position,
                "new_position": {"x": snapshot["data"]["x"], "y": snapshot["data"]["y"]},
            },
            modified=[snapshot],
            user_visible_effect=f"Snapped {snapshot['data']['name']} to the {grid}px grid",
        )
        return ActionExecution(result=result, emissions={"new_group_revision": snapshot["revision"]})

    def _export_asset(self, params: dict[str, Any], expected_revisions: list[dict[str, Any]]) -> ActionExecution:
        stale = self._check_expected_revisions(expected_revisions)
        if stale:
            return make_failure_result(
                "export_asset",
                "STALE_REVISION",
                "Artboard changed before export started",
                retryable=True,
                stale_entities=stale,
                recovery_options=[
                    {
                        "action_id": "refresh_context",
                        "description": "Re-read state and recompute workflow inputs",
                    }
                ],
            )

        self.job_counter += 1
        export_job_id = f"job-{self.job_counter}"
        export_job_ref = f"export_job:{export_job_id}"
        self.export_jobs[export_job_ref] = {
            "revision": self._job_revision(),
            "data": {
                "status": "running",
                "formats": copy.deepcopy(params["formats"]),
                "output_refs": [],
            },
        }
        snapshot = self.resolve_watch_snapshot("export_job", export_job_ref)
        result = make_success_result(
            "export_asset",
            {
                "export_job_id": export_job_id,
                "export_job_ref": export_job_ref,
                "status": snapshot["data"]["status"],
            },
            created=[snapshot],
            user_visible_effect="Started staged export for the selected artboard",
        )
        return ActionExecution(
            result=result,
            emissions={
                "export_job_id": export_job_id,
                "export_job_ref": export_job_ref,
            },
        )

    def _publish_asset(self, params: dict[str, Any]) -> ActionExecution:
        if "asset.publish" not in self._permissions():
            return make_failure_result(
                "publish_asset",
                "PERMISSION_DENIED",
                "User lacks publish permission",
                retryable=False,
            )

        export_job_ref = f"export_job:{params['export_job_id']}"
        job = self.export_jobs[export_job_ref]
        if job["data"]["status"] != "completed":
            return make_failure_result(
                "publish_asset",
                "JOB_NOT_COMPLETE",
                "Export job has not finished",
                retryable=True,
                recovery_options=[
                    {
                        "action_id": "wait_for_export",
                        "description": "Wait for the export job to finish before publishing",
                    }
                ],
            )

        self.published_refs = copy.deepcopy(job["data"]["output_refs"])
        result = make_success_result(
            "publish_asset",
            {
                "published_refs": copy.deepcopy(self.published_refs),
                "previous_version_archived": True,
            },
            created=[],
            modified=[],
            deleted=[],
            user_visible_effect="Replaced the live asset with the staged export",
        )
        return ActionExecution(result=result, emissions={})


class WorkflowExecutor:
    """Tiny state-machine executor for the bundled demo manifests."""

    def __init__(
        self,
        manifest: dict[str, Any],
        adapter: BaseDemoAdapter,
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
        artifacts = self.adapter.build_artifacts(outcome["outputs"], outcome["context_frame"])
        return {
            "application_id": self.manifest["application"]["id"],
            "workflow_id": workflow_id,
            "status": outcome["status"],
            "outcome": outcome["outcome"],
            "trace": outcome["trace"],
            "final_context_frame": outcome["context_frame"],
            "artifacts": artifacts,
            "summary_row": artifacts.get("summary_row"),
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
            resolved_inputs: dict[str, Any] = {}
            resolved_expected = resolve_interpolations(step.get("expected_revisions", []), env)
            simulated_event = None
            step_result: dict[str, Any] | None = None
            emissions: dict[str, Any] = {}

            if step["kind"] == "observe":
                if "action" in step:
                    resolved_inputs = resolve_interpolations(step.get("inputs", {}), env)
                    execution = self.adapter.invoke_action(step_id, step["action"], resolved_inputs, [], context_frame)
                    step_result = execution.result
                    emissions = {
                        **(unwrap(step_result.get("data", {})) if step_result else {}),
                        **unwrap(execution.emissions),
                    }
                    local_outputs[step_id] = emissions
                    if step_result and step_result["status"] == "failure":
                        transition = "failure"
                        self._record_failure_outcome(outcome_info, step_id, transition, step_result)
                    else:
                        eval_env = self._build_env(context_frame, runtime, local_outputs, inputs, current)
                        success = True
                        if "predicate" in step:
                            success = bool(evaluate_cel(step["predicate"], eval_env))
                        transition = "success" if success else "failure"
                    next_step = step["on"][transition]
                else:
                    resolved_inputs = resolve_interpolations(step.get("inputs", {}), env)
                    execution = self.adapter.observe_step(step_id, resolved_reads, resolved_inputs, context_frame)
                    step_result = execution.result
                    emissions = execution.emissions
                    local_outputs[step_id] = emissions
                    eval_env = self._build_env(context_frame, runtime, local_outputs, inputs, current)
                    success = True
                    if "predicate" in step:
                        success = bool(evaluate_cel(step["predicate"], eval_env))
                    transition = "success" if success else "failure"
                    next_step = step["on"][transition]
                    if step_id == "refresh_context" and transition == "failure":
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

            elif step["kind"] == "decide":
                decision = bool(evaluate_cel(step["predicate"], env))
                emissions = {"decision": decision}
                local_outputs[step_id] = emissions
                transition = "on_true" if decision else "on_false"
                next_step = step[transition]

            elif step["kind"] == "mutate":
                resolved_inputs = resolve_interpolations(step.get("inputs", {}), env)
                if self._forced_remaining.get(step_id, 0) > 0:
                    simulated_event = self.adapter.simulate_external_change(step_id, resolved_expected)
                    self._forced_remaining[step_id] -= 1
                execution = self.adapter.invoke_action(step_id, step["action"], resolved_inputs, resolved_expected, context_frame)
                step_result = execution.result
                emissions = {
                    **(unwrap(step_result.get("data", {})) if step_result else {}),
                    **unwrap(execution.emissions),
                }
                local_outputs[step_id] = emissions
                if step_result and step_result["status"] == "failure" and step_result["error"]["code"] == "STALE_REVISION":
                    transition = "stale_revision"
                    runtime["context_refresh_count"] += 1
                    outcome_info["last_error_code"] = "STALE_REVISION"
                    outcome_info["stale_retry_count"] = runtime["context_refresh_count"]
                elif step_result and step_result["status"] == "failure":
                    transition = "failure"
                    self._record_failure_outcome(outcome_info, step_id, transition, step_result)
                else:
                    transition = "success"
                next_step = step["on"][transition]
                context_frame = self.adapter.build_context_frame({**runtime, "trigger": "agent_action"})

            elif step["kind"] == "subflow":
                items = [None]
                if "foreach" in step:
                    items = unwrap(evaluate_cel(step["foreach"], env))
                subflow = next(item for item in workflow["subflows"] if item["id"] == step["workflow_ref"])
                sub_results = []
                had_failure = False
                saw_stale = False
                first_failure: dict[str, Any] | None = None
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
                        if outcome["outcome"].get("last_error_code") == "STALE_REVISION":
                            saw_stale = True
                        if first_failure is None:
                            first_failure = outcome["outcome"]
                        if not step.get("continue_on_error", False):
                            break
                emissions = {"results": sub_results}
                local_outputs[step_id] = emissions
                if had_failure and saw_stale and "stale_revision" in step["on"]:
                    transition = "stale_revision"
                elif had_failure and step.get("continue_on_error", False):
                    transition = "partial"
                elif had_failure:
                    transition = "failure"
                    if first_failure is not None and outcome_info["disposition"] is None:
                        outcome_info.update(first_failure)
                else:
                    transition = "success"
                next_step = step["on"][transition]

            elif step["kind"] == "confirm":
                resolved_inputs = resolve_interpolations(step.get("inputs", {}), env)
                resolved_payload = resolve_interpolations(step.get("payload"), env)
                transition = self.adapter.confirm_step(step_id, step["prompt"], resolved_payload, context_frame)
                emissions = {"decision": transition}
                local_outputs[step_id] = emissions
                step_result = {"status": transition}
                next_step = step["on"][transition]
                if transition == "rejected" and outcome_info["disposition"] is None:
                    outcome_info.update(
                        {
                            "status": "failure",
                            "disposition": "cancelled_by_user",
                            "reason": "confirmation_rejected",
                            "terminal_step": step_id,
                            "terminal_transition": transition,
                            "last_error_code": None,
                        }
                    )

            elif step["kind"] == "wait":
                watch_ref = self._resolve_watch_ref(step["watch_binding"], env)
                watch_snapshot = self.adapter.resolve_watch_snapshot(step["watch_binding"]["entity_type"], watch_ref)
                wait_env = {**self._build_env(context_frame, runtime, local_outputs, inputs, current), "watch": wrap(watch_snapshot)}
                polls = 0
                while not bool(evaluate_cel(step["until"], wait_env)) and polls < 3:
                    polls += 1
                    advanced = self.adapter.advance_async(step["watch_binding"]["entity_type"], watch_ref)
                    if advanced is None:
                        break
                    watch_snapshot = advanced
                    context_frame = self.adapter.build_context_frame({**runtime, "trigger": "system_event"})
                    wait_env = {**self._build_env(context_frame, runtime, local_outputs, inputs, current), "watch": wrap(watch_snapshot)}
                emissions = {
                    "watch_ref": watch_ref,
                    "watch_status": watch_snapshot["data"].get("status"),
                    "poll_count": polls,
                }
                local_outputs[step_id] = emissions
                if bool(evaluate_cel(step["until"], wait_env)):
                    transition = "failure" if watch_snapshot["data"].get("status") == "failed" else "success"
                else:
                    transition = "timeout"
                next_step = step["on"][transition]
                step_result = {
                    "status": transition,
                    "watch": watch_snapshot,
                }
                if transition == "timeout" and outcome_info["disposition"] is None:
                    outcome_info.update(
                        {
                            "status": "failure",
                            "disposition": "failed_timeout",
                            "reason": "wait_timeout",
                            "terminal_step": step_id,
                            "terminal_transition": transition,
                            "last_error_code": None,
                        }
                    )
                elif transition == "failure" and outcome_info["disposition"] is None:
                    outcome_info.update(
                        {
                            "status": "failure",
                            "disposition": "failed_non_retryable",
                            "reason": "watched_entity_failed",
                            "terminal_step": step_id,
                            "terminal_transition": transition,
                            "last_error_code": watch_snapshot["data"].get("error_code"),
                        }
                    )

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
                    "simulated_external_event": simulated_event,
                    "frame_id": context_frame["frame_id"],
                    "next_step": next_step,
                }
            )
            step_id = next_step

        if outcome_info["disposition"] is None:
            recovered = runtime["context_refresh_count"] > 0
            outcome_info.update(
                {
                    "status": "success",
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

    def _record_failure_outcome(
        self,
        outcome_info: dict[str, Any],
        step_id: str,
        transition: str,
        step_result: dict[str, Any],
    ) -> None:
        if outcome_info["disposition"] is not None:
            return
        outcome_info.update(
            {
                "status": "failure",
                "disposition": "failed_non_retryable" if not step_result["error"]["retryable"] else "failed_retryable",
                "reason": "action_failure",
                "terminal_step": step_id,
                "terminal_transition": transition,
                "last_error_code": step_result["error"]["code"],
            }
        )

    def _resolve_watch_ref(self, binding: dict[str, Any], env: dict[str, Any]) -> str:
        return str(unwrap(evaluate_cel(binding["path"], env)))

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


def build_adapter(manifest: dict[str, Any], denied_permissions: set[str] | None = None) -> BaseDemoAdapter:
    app_id = manifest["application"]["id"]
    if app_id == "com.example.sheetapp":
        return MockSheetAppAdapter(denied_permissions)
    if app_id == "com.example.vectorforge":
        return MockVectorForgeAdapter(denied_permissions)
    raise ValueError(f"No demo adapter for application {app_id}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Path to the ANAC manifest to execute")
    parser.add_argument("--workflow", help="Workflow id to execute (defaults to the manifest's first workflow)")
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
    parser.add_argument(
        "--deny-permission",
        action="append",
        default=[],
        help="Remove a permission from the adapter's context frame for this run",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = load_manifest(Path(args.manifest))
    workflow_id = args.workflow or manifest["static"]["workflows"][0]["id"]
    adapter = build_adapter(manifest, set(args.deny_permission))
    executor = WorkflowExecutor(
        manifest,
        adapter,
        force_stale_step=args.force_stale_step,
        force_stale_count=args.force_stale_count,
    )
    result = executor.run(workflow_id)
    payload = result["trace"] if args.trace_only else result
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
