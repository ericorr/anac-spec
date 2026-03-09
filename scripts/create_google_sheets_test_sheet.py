#!/usr/bin/env python3
"""Create and seed a throwaway Google Sheets spreadsheet for ANAC live tests."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from anac_google_sheets_live import MissingDependencyError, load_google_client_modules


DEFAULT_TITLE = "ANAC Test Sheet"
DEFAULT_TAB = "Q1 Sales"
DEFAULT_ROWS = [
    ["Rep", "Region", "Jan", "Feb", "Mar", "Total"],
    ["Alex", "North", 100, 110, 120, "=SUM(C2:E2)"],
    ["Blair", "South", 90, 105, 115, "=SUM(C3:E3)"],
    ["Casey", "East", 130, 120, 125, "=SUM(C4:E4)"],
    ["Drew", "West", 95, 100, 108, "=SUM(C5:E5)"],
    ["Evan", "Central", 112, 118, 121, "=SUM(C6:E6)"],
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", default=os.environ.get("ANAC_GOOGLE_TEST_TITLE", DEFAULT_TITLE))
    parser.add_argument("--sheet-name", default=os.environ.get("ANAC_GOOGLE_SHEET_NAME", DEFAULT_TAB))
    parser.add_argument(
        "--spreadsheet-id",
        default=os.environ.get("ANAC_GOOGLE_SPREADSHEET_ID"),
        help="Optional existing spreadsheet ID. If provided, the script seeds that spreadsheet instead of creating one.",
    )
    parser.add_argument(
        "--credentials-file",
        default=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
        help="Service-account JSON file. Falls back to GOOGLE_APPLICATION_CREDENTIALS.",
    )
    parser.add_argument(
        "--share-with",
        default=os.environ.get("ANAC_GOOGLE_SHARE_WITH"),
        help="Optional Google email to grant writer access so you can open the test sheet in your account.",
    )
    parser.add_argument(
        "--dump-json",
        action="store_true",
        help="Emit the created spreadsheet metadata as JSON only.",
    )
    return parser.parse_args()


def build_services(credentials_file: str | None) -> tuple[Any, Any, str]:
    google_auth, service_account, build = load_google_client_modules()
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    try:
        if credentials_file:
            creds = service_account.Credentials.from_service_account_file(credentials_file, scopes=scopes)
            service_account_email = getattr(creds, "service_account_email", "") or ""
        else:
            creds, _project = google_auth.default(scopes=scopes)
            service_account_email = ""
    except Exception as exc:
        raise RuntimeError(
            "Could not load Google credentials. Set GOOGLE_APPLICATION_CREDENTIALS to a service-account JSON file "
            "or pass --credentials-file."
        ) from exc
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    return sheets, drive, service_account_email


def create_spreadsheet(sheets: Any, title: str, sheet_name: str) -> dict[str, Any]:
    body = {
        "properties": {"title": title},
        "sheets": [{"properties": {"title": sheet_name}}],
    }
    return sheets.spreadsheets().create(
        body=body,
        fields="spreadsheetId,spreadsheetUrl,properties.title,sheets(properties(sheetId,title))",
    ).execute()


def get_spreadsheet(sheets: Any, spreadsheet_id: str) -> dict[str, Any]:
    return sheets.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="spreadsheetId,spreadsheetUrl,properties.title,sheets(properties(sheetId,title))",
    ).execute()


def ensure_sheet_exists(sheets: Any, spreadsheet_id: str, sheet_name: str) -> int:
    existing = get_spreadsheet(sheets, spreadsheet_id)
    for sheet in existing.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("title") == sheet_name and "sheetId" in props:
            return int(props["sheetId"])
    response = sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]},
    ).execute()
    replies = response.get("replies", [])
    if replies:
        props = replies[0].get("addSheet", {}).get("properties", {})
        if "sheetId" in props:
            return int(props["sheetId"])
    raise RuntimeError(f"Could not create tab {sheet_name!r} in spreadsheet {spreadsheet_id!r}")


def seed_sheet(sheets: Any, spreadsheet_id: str, sheet_name: str, sheet_id: int) -> None:
    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1:F{len(DEFAULT_ROWS)}",
        valueInputOption="USER_ENTERED",
        body={"values": DEFAULT_ROWS},
    ).execute()
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 0,
                            "endRowIndex": 1,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "textFormat": {"bold": True},
                                "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9},
                            }
                        },
                        "fields": "userEnteredFormat(textFormat,backgroundColor)",
                    }
                },
                {
                    "updateSheetProperties": {
                        "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                        "fields": "gridProperties.frozenRowCount",
                    }
                },
            ]
        },
    ).execute()


def share_spreadsheet(drive: Any, spreadsheet_id: str, email: str) -> None:
    drive.permissions().create(
        fileId=spreadsheet_id,
        supportsAllDrives=True,
        sendNotificationEmail=False,
        body={
            "type": "user",
            "role": "writer",
            "emailAddress": email,
        },
        fields="id",
    ).execute()


def main() -> int:
    args = parse_args()
    try:
        sheets, drive, service_account_email = build_services(args.credentials_file)
    except MissingDependencyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.spreadsheet_id:
        spreadsheet_id = args.spreadsheet_id
        created = get_spreadsheet(sheets, spreadsheet_id)
        sheet_id = ensure_sheet_exists(sheets, spreadsheet_id, args.sheet_name)
    else:
        created = create_spreadsheet(sheets, args.title, args.sheet_name)
        spreadsheet_id = created["spreadsheetId"]
        matching_sheet = next(
            (
                sheet.get("properties", {})
                for sheet in created.get("sheets", [])
                if sheet.get("properties", {}).get("title") == args.sheet_name
            ),
            None,
        )
        if not matching_sheet or "sheetId" not in matching_sheet:
            print("Created spreadsheet but could not resolve the new tab's sheetId.", file=sys.stderr)
            return 1
        sheet_id = int(matching_sheet["sheetId"])
    seed_sheet(sheets, spreadsheet_id, args.sheet_name, sheet_id)
    if args.share_with:
        share_spreadsheet(drive, spreadsheet_id, args.share_with)

    payload = {
        "spreadsheet_id": spreadsheet_id,
        "spreadsheet_url": created["spreadsheetUrl"],
        "title": args.title,
        "sheet_name": args.sheet_name,
        "service_account_email": service_account_email or None,
        "shared_with": args.share_with or None,
    }
    if args.dump_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Created spreadsheet: {created['spreadsheetUrl']}")
        print(f"Spreadsheet ID: {spreadsheet_id}")
        print(f"Tab: {args.sheet_name}")
        if service_account_email:
            print(f"Service account: {service_account_email}")
        if args.share_with:
            print(f"Shared with: {args.share_with}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
