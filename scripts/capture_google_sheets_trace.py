#!/usr/bin/env python3
"""Run the live Google Sheets adapter and save a commit-ready trace artifact."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
LIVE_ADAPTER = ROOT_DIR / "scripts" / "anac_google_sheets_live.py"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "docs" / "traces"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spreadsheet-id", required=True, help="Target Google spreadsheet id")
    parser.add_argument("--sheet-name", required=True, help="Target sheet/tab name")
    parser.add_argument("--selection", default="cell:D7", help="Initial selected cell ref")
    parser.add_argument("--credentials-file", help="Service-account JSON file; if omitted, the adapter uses ADC")
    parser.add_argument("--scenario", default="happy", help="Short scenario label used in the output filename")
    parser.add_argument("--workflow", default="add_summary_row", help="Workflow id to execute")
    parser.add_argument("--force-stale-step", help="Step name to stale before executing")
    parser.add_argument("--force-stale-count", type=int, default=1, help="How many stale injections to perform")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory to write the trace artifact into")
    parser.add_argument("--print-path-only", action="store_true", help="Print only the output path after writing")
    return parser.parse_args()


def timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_command(args: argparse.Namespace) -> list[str]:
    command = [
        "python3",
        str(LIVE_ADAPTER),
        "--spreadsheet-id",
        args.spreadsheet_id,
        "--sheet-name",
        args.sheet_name,
        "--selection",
        args.selection,
        "--workflow",
        args.workflow,
    ]
    if args.credentials_file:
        command.extend(["--credentials-file", args.credentials_file])
    if args.force_stale_step:
        command.extend(["--force-stale-step", args.force_stale_step, "--force-stale-count", str(args.force_stale_count)])
    return command


def run_live_adapter(command: list[str]) -> dict[str, Any]:
    raw = subprocess.check_output(command, text=True)
    return json.loads(raw)


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    command = build_command(args)
    payload = run_live_adapter(command)
    artifact = {
        "captured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "adapter": "google_sheets_live",
        "scenario": args.scenario,
        "spreadsheet": {
            "sheet_name": args.sheet_name,
            "selection": args.selection,
            "spreadsheet_id_redacted": True,
        },
        "command": {
            "workflow": args.workflow,
            "force_stale_step": args.force_stale_step,
            "force_stale_count": args.force_stale_count if args.force_stale_step else 0,
        },
        "result": payload,
    }

    filename = f"google-sheets-live-{args.scenario}-{timestamp_slug()}.json"
    output_path = output_dir / filename
    output_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")

    if args.print_path_only:
        print(output_path)
        return 0

    print(json.dumps(
        {
            "output_path": str(output_path),
            "status": payload.get("status"),
            "disposition": payload.get("outcome", {}).get("disposition"),
            "terminal_step": payload.get("outcome", {}).get("terminal_step"),
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
