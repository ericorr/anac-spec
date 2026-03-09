# Live Google Sheets Adapter

This is the first non-mock ANAC adapter in the repo.

The script at [`scripts/anac_google_sheets_live.py`](../scripts/anac_google_sheets_live.py) runs the existing `SheetApp` ANAC workflow against a real Google Sheet using the Google Sheets API and Drive file metadata.

## What it proves

It does not implement a new workflow. It reuses the existing `SheetApp` manifest and the existing ANAC executor.

That matters because the proof point is not “we can talk to Google Sheets.” The proof point is “the same ANAC workflow structure can drive a real application instead of an in-memory Python dict.”

## Revision model

Google Sheets does not expose per-cell revision IDs through the Sheets API.

This adapter therefore uses the Drive file `modifiedTime` as a coarse spreadsheet revision signal.

Implications:

- any spreadsheet content change counts as a revision change
- stale detection is conservative: unrelated edits elsewhere in the sheet will still invalidate the expected revision
- that is acceptable for the current goal, which is to prove the ANAC stale-read recovery loop against a real API-backed tool

## Dependencies

Install the optional live-adapter dependencies:

```bash
python3 -m pip install -r requirements-google-live.txt
```

## Authentication

Two auth modes are supported:

1. service account via `GOOGLE_APPLICATION_CREDENTIALS`
2. Application Default Credentials if your environment already has them configured

For service-account use, share the target spreadsheet with the service-account email.

## Required configuration

Set either CLI flags or environment variables:

- `--spreadsheet-id` or `ANAC_GOOGLE_SPREADSHEET_ID`
- `--sheet-name` or `ANAC_GOOGLE_SHEET_NAME`
- optional: `--selection` or `ANAC_GOOGLE_SELECTION`

Check setup without running the workflow:

```bash
python3 scripts/anac_google_sheets_live.py --validate-setup --spreadsheet-id <spreadsheet-id> --sheet-name <tab-name>
```

## Run the live adapter

Happy path:

```bash
python3 scripts/anac_google_sheets_live.py \
  --spreadsheet-id <spreadsheet-id> \
  --sheet-name "Q1 Sales"
```

Forced stale path against a real sheet:

```bash
python3 scripts/anac_google_sheets_live.py \
  --spreadsheet-id <spreadsheet-id> \
  --sheet-name "Q1 Sales" \
  --force-stale-step insert_summary_row \
  --force-stale-count 1
```

The stale path works by writing a marker to `ZZ1` before the target mutate step. That changes the spreadsheet revision without disturbing the summary-table region.

## Output

The script emits the same runtime structure as the mock executor:

- `status`
- `outcome`
- `trace`
- `final_context_frame`
- `artifacts`

That means you can compare live and mock runs directly.

## Current limits

- this adapter is not part of CI
- the repo currently has no automated test credentials for Google APIs
- the revision model is spreadsheet-wide, not cell-specific
- formatting verification is API-level, not visual
