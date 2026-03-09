#!/usr/bin/env python3
"""Flip the positioning document from pre-trace wording to post-trace wording.

Usage:
  python3 scripts/apply_live_trace_wording.py \
    --happy docs/traces/google-sheets-live-happy-20260309T220000Z.json \
    --stale docs/traces/google-sheets-live-stale-recovered-20260309T220300Z.json
"""

from __future__ import annotations

import argparse
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
POSITIONING_PATH = ROOT_DIR / "docs" / "positioning.md"

OLD_LIVE_PARAGRAPH = (
    "There is also now an experimental live adapter for Google Sheets at "
    "[`scripts/anac_google_sheets_live.py`](../scripts/anac_google_sheets_live.py). "
    "It is intentionally outside CI because this repo does not carry Google API credentials, "
    "but it uses the same `SheetApp` manifest and executor shape instead of a separate integration path."
)

OLD_BOUNDARY_LINE = (
    "- the live Google Sheets adapter is setup-verified but not CI-executed in this repo because credentials are not available"
)

OLD_NEXT_STEP_LINE = (
    "- harden and verify the experimental Google Sheets live adapter against a real credentialed test sheet"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--happy", required=True, help="Path to the happy-path live trace JSON, relative to the repo root")
    parser.add_argument("--stale", required=True, help="Path to the stale-recovered live trace JSON, relative to the repo root")
    return parser.parse_args()


def must_exist(path_str: str) -> Path:
    path = (ROOT_DIR / path_str).resolve()
    try:
        path.relative_to(ROOT_DIR.resolve())
    except ValueError as exc:
        raise SystemExit(f"Trace path must stay inside the repo: {path_str}") from exc
    if not path.exists():
        raise SystemExit(f"Trace file does not exist: {path_str}")
    return path


def main() -> int:
    args = parse_args()
    happy_path = must_exist(args.happy)
    stale_path = must_exist(args.stale)

    text = POSITIONING_PATH.read_text(encoding="utf-8")

    if OLD_LIVE_PARAGRAPH not in text:
        raise SystemExit("Expected pre-launch live-adapter paragraph not found in docs/positioning.md")
    if OLD_BOUNDARY_LINE not in text:
        raise SystemExit("Expected pre-launch boundary line not found in docs/positioning.md")
    if OLD_NEXT_STEP_LINE not in text:
        raise SystemExit("Expected pre-launch next-step line not found in docs/positioning.md")

    happy_rel = happy_path.relative_to(ROOT_DIR).as_posix()
    stale_rel = stale_path.relative_to(ROOT_DIR).as_posix()

    new_live_paragraph = (
        "The repository also includes a live Google Sheets adapter at "
        "[`scripts/anac_google_sheets_live.py`](../scripts/anac_google_sheets_live.py), "
        "validated against a real throwaway spreadsheet using the same `SheetApp` manifest and executor shape. "
        f"The captured live traces are committed at [`{happy_rel}`](../{happy_rel}) and [`{stale_rel}`](../{stale_rel})."
    )

    new_next_step_line = (
        "- extend the live-adapter coverage beyond Google Sheets or tighten the revision model from spreadsheet-wide to range-aware"
    )

    text = text.replace(OLD_LIVE_PARAGRAPH, new_live_paragraph, 1)
    text = text.replace(OLD_BOUNDARY_LINE + "\n", "", 1)
    text = text.replace(OLD_NEXT_STEP_LINE, new_next_step_line, 1)

    POSITIONING_PATH.write_text(text, encoding="utf-8")
    print(f"Updated {POSITIONING_PATH} with live trace links:")
    print(f"  happy: {happy_rel}")
    print(f"  stale: {stale_rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
