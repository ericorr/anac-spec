#!/usr/bin/env python3
"""Execute the SheetApp ANAC workflow against a live Google Sheet.

This is an experimental adapter that reuses the existing toy executor and SheetApp
manifest, but binds it to the Google Sheets and Drive APIs instead of an in-memory
mock. The adapter uses Drive file `modifiedTime` as a coarse spreadsheet revision
signal, so any content change to the spreadsheet counts as a stale revision.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anac_runtime_demo import (
    ROOT_DIR,
    ActionExecution,
    BaseDemoAdapter,
    WorkflowExecutor,
    iso_now,
    load_manifest,
    make_failure_result,
    make_success_result,
)


DEFAULT_MANIFEST = ROOT_DIR / "examples" / "example-sheetapp-0.1.2.json"
CELL_REF_PATTERN = re.compile(r"^([A-Z]+)(\d+)$")


class MissingDependencyError(RuntimeError):
    pass


def load_google_client_modules() -> tuple[Any, Any, Any]:
    try:
        import google.auth  # type: ignore
        from google.oauth2 import service_account  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised by setup checks, not CI
        raise MissingDependencyError(
            "Missing Google client libraries. Install optional deps with: "
            "python3 -m pip install -r requirements-google-live.txt"
        ) from exc
    return google.auth, service_account, build


@dataclass
class GoogleServices:
    sheets: Any
    drive: Any


class LiveGoogleSheetsAdapter(BaseDemoAdapter):
    def __init__(
        self,
        *,
        spreadsheet_id: str,
        sheet_name: str,
        selection: str = "cell:D7",
        credentials_file: str | None = None,
        denied_permissions: set[str] | None = None,
    ) -> None:
        super().__init__(denied_permissions)
        self.spreadsheet_id = spreadsheet_id
        self.sheet_name = sheet_name
        self.selection = selection if selection.startswith("cell:") else f"cell:{selection}"
        self.credentials_file = credentials_file
        self.frame_counter = 1000
        self._services: GoogleServices | None = None
        self._sheet_id_cache: int | None = None

    def ensure_services(self) -> GoogleServices:
        if self._services is not None:
            return self._services

        google_auth, service_account, build = load_google_client_modules()
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive.metadata.readonly",
        ]
        try:
            if self.credentials_file:
                creds = service_account.Credentials.from_service_account_file(self.credentials_file, scopes=scopes)
            else:
                creds, _project = google_auth.default(scopes=scopes)
        except Exception as exc:  # pragma: no cover - depends on local credential state
            raise RuntimeError(
                "Could not load Google credentials. Set GOOGLE_APPLICATION_CREDENTIALS to a service-account JSON file "
                "or configure Application Default Credentials before running the live adapter."
            ) from exc
        self._services = GoogleServices(
            sheets=build("sheets", "v4", credentials=creds, cache_discovery=False),
            drive=build("drive", "v3", credentials=creds, cache_discovery=False),
        )
        return self._services

    def _permissions(self) -> list[str]:
        base = ["sheet.edit", "sheet.format"]
        return [perm for perm in base if perm not in self.denied_permissions]

    def _sheet_id(self) -> int:
        if self._sheet_id_cache is not None:
            return self._sheet_id_cache
        sheets = self.ensure_services().sheets
        response = sheets.spreadsheets().get(
            spreadsheetId=self.spreadsheet_id,
            fields="sheets(properties(sheetId,title))",
        ).execute()
        for sheet in response.get("sheets", []):
            props = sheet.get("properties", {})
            if props.get("title") == self.sheet_name:
                self._sheet_id_cache = int(props["sheetId"])
                return self._sheet_id_cache
        raise RuntimeError(f"Sheet {self.sheet_name!r} not found in spreadsheet {self.spreadsheet_id!r}")

    def _spreadsheet_revision(self) -> str:
        drive = self.ensure_services().drive
        file_meta = drive.files().get(
            fileId=self.spreadsheet_id,
            fields="id,name,modifiedTime,version",
            supportsAllDrives=True,
        ).execute()
        version = file_meta.get("version")
        if version:
            return f"v{version}"
        modified = file_meta.get("modifiedTime")
        if modified:
            return modified
        raise RuntimeError("Drive file metadata did not include version or modifiedTime")

    def _values(self, a1_range: str, *, value_render_option: str = "FORMULA") -> list[list[Any]]:
        sheets = self.ensure_services().sheets
        response = sheets.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range=a1_range,
            valueRenderOption=value_render_option,
        ).execute()
        return response.get("values", [])

    def _parse_cell_ref(self, address: str) -> tuple[str, int]:
        match = CELL_REF_PATTERN.fullmatch(address)
        if not match:
            raise ValueError(f"Unsupported cell address: {address!r}")
        return match.group(1), int(match.group(2))

    def _column_to_index(self, column: str) -> int:
        total = 0
        for char in column:
            total = total * 26 + (ord(char) - 64)
        return total - 1

    def _index_to_column(self, index: int) -> str:
        index += 1
        pieces = []
        while index > 0:
            index, remainder = divmod(index - 1, 26)
            pieces.append(chr(65 + remainder))
        return "".join(reversed(pieces))

    def _expand_range(self, range_expr: str) -> list[str]:
        start, end = range_expr.split(":", 1)
        start_col, start_row = self._parse_cell_ref(start)
        end_col, end_row = self._parse_cell_ref(end)
        addresses = []
        for row in range(start_row, end_row + 1):
            for col_index in range(self._column_to_index(start_col), self._column_to_index(end_col) + 1):
                addresses.append(f"{self._index_to_column(col_index)}{row}")
        return addresses

    def _sheet_matrix(self) -> list[list[Any]]:
        return self._values(f"{self.sheet_name}!A:ZZ", value_render_option="FORMULA")

    def _sheet_snapshot(self) -> dict[str, Any]:
        matrix = self._sheet_matrix()
        last_data_row = 0
        for idx, row in enumerate(matrix, start=1):
            if any(cell not in (None, "") for cell in row):
                last_data_row = idx
        header = matrix[0] if matrix else []
        column_types: dict[str, str] = {}
        for col_index in range(min(max(len(header), 6), 26)):
            column = self._index_to_column(col_index)
            samples = [
                row[col_index]
                for row in matrix[1:last_data_row]
                if col_index < len(row) and row[col_index] not in (None, "")
            ]
            if any(isinstance(value, str) and value.startswith("=") for value in samples):
                column_types[column] = "formula"
            elif any(isinstance(value, (int, float)) for value in samples):
                column_types[column] = "number"
            elif samples:
                column_types[column] = "text"
            elif col_index < len(header) and isinstance(header[col_index], str):
                column_types[column] = "text"
        revision = self._spreadsheet_revision()
        return {
            "entity_type": "sheet",
            "ref": f"sheet:{self.sheet_name}",
            "revision": revision,
            "data": {
                "name": self.sheet_name,
                "used_range": f"A1:F{max(last_data_row, 1)}",
                "is_protected": False,
                "last_data_row": last_data_row,
                "has_header_row": bool(matrix),
                "column_types": column_types,
            },
        }

    def _cell_snapshot(self, ref: str) -> dict[str, Any]:
        address = ref.split(":", 1)[1]
        values = self._values(f"{self.sheet_name}!{address}:{address}", value_render_option="FORMULA")
        raw = values[0][0] if values and values[0] else None
        formula = raw if isinstance(raw, str) and raw.startswith("=") else None
        value = None if formula else raw
        return {
            "entity_type": "cell",
            "ref": ref,
            "revision": self._spreadsheet_revision(),
            "data": {
                "address": address,
                "value": value,
                "formula": formula,
                "is_locked": False,
                "dependencies": [],
                "format": {},
            },
        }

    def resolve_watch_snapshot(self, entity_type: str, ref: str) -> dict[str, Any]:
        if entity_type == "sheet" or ref.startswith("sheet:"):
            return self._sheet_snapshot()
        if entity_type == "cell" or ref.startswith("cell:"):
            return self._cell_snapshot(ref)
        raise KeyError(f"Unsupported watch ref {ref!r} for entity type {entity_type!r}")

    def build_context_frame(self, workflow_runtime: dict[str, Any] | None = None) -> dict[str, Any]:
        workflow_runtime = workflow_runtime or {}
        self.frame_counter += 1
        sheet_snapshot = self._sheet_snapshot()
        cell_snapshot = self._cell_snapshot(self.selection)
        active_workflows = []
        if workflow_runtime:
            active_workflows.append(
                {
                    "workflow_id": workflow_runtime["workflow_id"],
                    "lease_id": workflow_runtime["lease_id"],
                    "current_step": workflow_runtime["current_step"],
                    "progress": workflow_runtime["progress"],
                    "next_action_hint": workflow_runtime.get("next_action_hint"),
                    "can_rollback": False,
                    "context_refresh_count": workflow_runtime["context_refresh_count"],
                }
            )
        return {
            "frame_id": f"gs-{self.frame_counter}",
            "emitted_at": iso_now(),
            "trigger": workflow_runtime.get("trigger", "system_event"),
            "subscription_id": "sub-live-google-sheets",
            "scope": {
                "mode": "selection",
                "root_refs": [sheet_snapshot["ref"]],
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
                    "revision": cell_snapshot["revision"],
                }
            ],
            "permissions": self._permissions(),
            "available_actions": [
                {
                    "action_id": "set_cell_value",
                    "relevance": "primary",
                    "reason": "A target sheet is configured and writable",
                    "preconditions_met": True,
                },
                {
                    "action_id": "insert_row",
                    "relevance": "primary",
                    "reason": "The active sheet can accept row insertions",
                    "preconditions_met": True,
                },
                {
                    "action_id": "format_cells",
                    "relevance": "secondary",
                    "reason": "Formatting is supported through spreadsheets.batchUpdate",
                    "preconditions_met": True,
                },
            ],
            "active_workflows": active_workflows,
            "entity_snapshots": [sheet_snapshot, cell_snapshot],
            "warnings": [
                {
                    "severity": "info",
                    "message": "Live adapter uses Drive version, falling back to modifiedTime, as a coarse spreadsheet revision.",
                    "related_entity": {
                        "entity_type": sheet_snapshot["entity_type"],
                        "ref": sheet_snapshot["ref"],
                        "revision": sheet_snapshot["revision"],
                    },
                    "suggested_action": None,
                }
            ],
            "recent_events": [],
        }

    def observe_step(
        self,
        step_id: str,
        resolved_reads: list[str],
        resolved_inputs: dict[str, Any],
        context_frame: dict[str, Any],
    ) -> ActionExecution:
        del resolved_reads, resolved_inputs, context_frame
        if step_id == "read_table":
            snapshot = self._sheet_snapshot()
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
            revision = self._spreadsheet_revision()
            return ActionExecution(
                result=None,
                emissions={
                    "rev_a": revision,
                    "rev_b": revision,
                    "rev_c": revision,
                    "rev_d": revision,
                    "rev_e": revision,
                    "rev_f": revision,
                },
            )
        if step_id == "read_label_target":
            return ActionExecution(result=None, emissions={"label_cell_revision": self._spreadsheet_revision()})
        if step_id == "read_formula_target":
            return ActionExecution(result=None, emissions={"target_cell_revision": self._spreadsheet_revision()})
        if step_id == "refresh_context":
            return ActionExecution(result=None, emissions={"refreshed": True})
        return ActionExecution(result=None, emissions={})

    def _check_expected_revisions(self, expected_revisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not expected_revisions:
            return []
        current_revision = self._spreadsheet_revision()
        stale = []
        for item in expected_revisions:
            if item["revision"] != current_revision:
                stale.append(
                    {
                        "entity_type": item["entity_type"],
                        "ref": item["ref"],
                        "revision": current_revision,
                    }
                )
        return stale

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
        raise KeyError(f"Unsupported action {action_id!r}")

    def _insert_row(self, params: dict[str, Any], expected_revisions: list[dict[str, Any]]) -> ActionExecution:
        stale = self._check_expected_revisions(expected_revisions)
        if stale:
            return make_failure_result(
                "insert_row",
                "STALE_REVISION",
                "Spreadsheet changed before row insertion.",
                retryable=True,
                stale_entities=stale,
                recovery_options=[
                    {
                        "action_id": "refresh_context",
                        "description": "Re-read spreadsheet state and retry the workflow.",
                    }
                ],
            )
        before_row = int(params["before_row"])
        self.ensure_services().sheets.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={
                "requests": [
                    {
                        "insertDimension": {
                            "range": {
                                "sheetId": self._sheet_id(),
                                "dimension": "ROWS",
                                "startIndex": before_row - 1,
                                "endIndex": before_row,
                            },
                            "inheritFromBefore": False,
                        }
                    }
                ]
            },
        ).execute()
        sheet_snapshot = self._sheet_snapshot()
        result = make_success_result(
            "insert_row",
            {
                "inserted_range": f"{before_row}:{before_row}",
                "new_last_row": sheet_snapshot["data"]["last_data_row"],
            },
            modified=[sheet_snapshot],
            user_visible_effect=f"Inserted row {before_row} in {self.sheet_name}",
        )
        return ActionExecution(result=result, emissions={"summary_row_number": before_row})

    def _set_cell_value(self, params: dict[str, Any], expected_revisions: list[dict[str, Any]]) -> ActionExecution:
        stale = self._check_expected_revisions(expected_revisions)
        if stale:
            return make_failure_result(
                "set_cell_value",
                "STALE_REVISION",
                "Spreadsheet changed before the cell write.",
                retryable=True,
                stale_entities=stale,
                recovery_options=[
                    {
                        "action_id": "refresh_context",
                        "description": "Re-read spreadsheet state and retry the workflow.",
                    }
                ],
            )
        address = params["address"]
        value = params["value"]
        body = {"values": [[value]]}
        response = self.ensure_services().sheets.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{self.sheet_name}!{address}",
            valueInputOption="USER_ENTERED",
            includeValuesInResponse=True,
            responseValueRenderOption="UNFORMATTED_VALUE",
            body=body,
        ).execute()
        updated_data = response.get("updatedData", {}).get("values", [[None]])
        computed_value = updated_data[0][0] if updated_data and updated_data[0] else None
        cell_snapshot = self._cell_snapshot(f"cell:{address}")
        result = make_success_result(
            "set_cell_value",
            {
                "address": address,
                "computed_value": computed_value,
                "affected_cells": 1,
            },
            modified=[cell_snapshot],
            user_visible_effect=f"Updated {address} in {self.sheet_name}",
        )
        return ActionExecution(result=result, emissions={})

    def _format_cells(self, params: dict[str, Any], expected_revisions: list[dict[str, Any]]) -> ActionExecution:
        stale = self._check_expected_revisions(expected_revisions)
        if stale:
            return make_failure_result(
                "format_cells",
                "STALE_REVISION",
                "Spreadsheet changed before formatting was applied.",
                retryable=True,
                stale_entities=stale,
                recovery_options=[
                    {
                        "action_id": "refresh_context",
                        "description": "Re-read spreadsheet state and retry the workflow.",
                    }
                ],
            )
        start_addr, end_addr = params["range"].split(":", 1)
        start_col, start_row = self._parse_cell_ref(start_addr)
        end_col, end_row = self._parse_cell_ref(end_addr)
        requests = []
        fields: list[str] = []
        fmt = params["format"]
        user_format: dict[str, Any] = {}
        if fmt.get("bold") is not None:
            user_format.setdefault("textFormat", {})["bold"] = bool(fmt["bold"])
            fields.append("userEnteredFormat.textFormat.bold")
        if fmt.get("border_top"):
            user_format.setdefault("borders", {})["top"] = {
                "style": "SOLID",
            }
            fields.append("userEnteredFormat.borders.top")
        requests.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": self._sheet_id(),
                        "startRowIndex": start_row - 1,
                        "endRowIndex": end_row,
                        "startColumnIndex": self._column_to_index(start_col),
                        "endColumnIndex": self._column_to_index(end_col) + 1,
                    },
                    "cell": {"userEnteredFormat": user_format},
                    "fields": ",".join(fields),
                }
            }
        )
        self.ensure_services().sheets.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"requests": requests},
        ).execute()
        modified = [self._cell_snapshot(f"cell:{address}") for address in self._expand_range(params["range"])]
        result = make_success_result(
            "format_cells",
            {
                "range": params["range"],
                "applied_properties": sorted(fmt.keys()),
            },
            modified=modified,
            user_visible_effect=f"Formatted {params['range']} in {self.sheet_name}",
        )
        return ActionExecution(result=result, emissions={})

    def simulate_external_change(self, step_id: str, expected_revisions: list[dict[str, Any]]) -> dict[str, Any]:
        del expected_revisions
        scratch_cell = "Z1"
        marker = f"anac-stale:{step_id}:{iso_now()}"
        self.ensure_services().sheets.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{self.sheet_name}!{scratch_cell}",
            valueInputOption="RAW",
            body={"values": [[marker]]},
        ).execute()
        return {
            "kind": "simulated_external_edit",
            "step_id": step_id,
            "timestamp": iso_now(),
            "mutated": [
                {
                    "entity_type": "sheet",
                    "ref": f"sheet:{self.sheet_name}",
                    "revision": self._spreadsheet_revision(),
                    "note": f"Wrote a scratch marker to {scratch_cell} to force a stale revision.",
                }
            ],
        }

    def build_artifacts(self, outputs: dict[str, dict[str, Any]], context_frame: dict[str, Any]) -> dict[str, Any]:
        del context_frame
        row = outputs.get("insert_summary_row", {}).get("summary_row_number")
        if row is None:
            return {"summary_row": None}
        values = self._values(f"{self.sheet_name}!A{row}:F{row}", value_render_option="FORMULA")
        row_values = values[0] if values else []
        summary_row = {}
        for idx, column in enumerate("ABCDEF"):
            raw = row_values[idx] if idx < len(row_values) else None
            summary_row[f"{column}{row}"] = {
                "address": f"{column}{row}",
                "value": None if isinstance(raw, str) and raw.startswith("=") else raw,
                "formula": raw if isinstance(raw, str) and raw.startswith("=") else None,
                "is_locked": False,
                "dependencies": [],
                "format": {},
            }
        return {"summary_row": summary_row}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Path to the SheetApp ANAC manifest")
    parser.add_argument("--workflow", default="add_summary_row", help="Workflow id to execute")
    parser.add_argument("--spreadsheet-id", default=os.environ.get("ANAC_GOOGLE_SPREADSHEET_ID"), help="Target Google spreadsheet id")
    parser.add_argument("--sheet-name", default=os.environ.get("ANAC_GOOGLE_SHEET_NAME"), help="Target sheet/tab name")
    parser.add_argument("--selection", default=os.environ.get("ANAC_GOOGLE_SELECTION", "cell:D7"), help="Initial selected cell ref")
    parser.add_argument("--credentials-file", default=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"), help="Service-account credentials JSON file. If omitted, uses Application Default Credentials.")
    parser.add_argument("--trace-only", action="store_true", help="Print only the step trace")
    parser.add_argument("--force-stale-step", help="Force a stale revision before the named mutate step by writing to ZZ1")
    parser.add_argument("--force-stale-count", type=int, default=1, help="How many stale injections to perform")
    parser.add_argument("--validate-setup", action="store_true", help="Check optional dependencies and required config without executing the workflow")
    return parser.parse_args()


def validate_setup(args: argparse.Namespace) -> int:
    try:
        load_google_client_modules()
    except MissingDependencyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    missing = []
    if not args.spreadsheet_id:
        missing.append("--spreadsheet-id or ANAC_GOOGLE_SPREADSHEET_ID")
    if not args.sheet_name:
        missing.append("--sheet-name or ANAC_GOOGLE_SHEET_NAME")
    if missing:
        print("Missing required configuration:", file=sys.stderr)
        for item in missing:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print(json.dumps({
        "google_client_libraries": "ok",
        "spreadsheet_id": args.spreadsheet_id,
        "sheet_name": args.sheet_name,
        "credentials_file": args.credentials_file,
        "uses_adc": args.credentials_file is None,
    }, indent=2))
    return 0


def main() -> int:
    args = parse_args()
    if args.validate_setup:
        return validate_setup(args)
    if not args.spreadsheet_id or not args.sheet_name:
        print("Both --spreadsheet-id and --sheet-name are required unless set via environment.", file=sys.stderr)
        return 2
    manifest = load_manifest(Path(args.manifest))
    adapter = LiveGoogleSheetsAdapter(
        spreadsheet_id=args.spreadsheet_id,
        sheet_name=args.sheet_name,
        selection=args.selection,
        credentials_file=args.credentials_file,
    )
    try:
        result = WorkflowExecutor(
            manifest,
            adapter,
            force_stale_step=args.force_stale_step,
            force_stale_count=args.force_stale_count,
        ).run(args.workflow)
    except MissingDependencyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    payload = result["trace"] if args.trace_only else result
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
