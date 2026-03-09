# Live Google Sheets Adapter

This is the first non-mock ANAC adapter in the repo.

The script at [`scripts/anac_google_sheets_live.py`](../scripts/anac_google_sheets_live.py) runs the existing `SheetApp` ANAC workflow against a real Google Sheet using the Google Sheets API and Drive file metadata.

## What it proves

It does not implement a new workflow. It reuses the existing `SheetApp` manifest and the existing ANAC executor.

That matters because the proof point is not “we can talk to Google Sheets.” The proof point is “the same ANAC workflow structure can drive a real application instead of an in-memory Python dict.”

## Revision model

Google Sheets does not expose per-cell revision IDs through the Sheets API.

This adapter therefore uses a coarse spreadsheet revision signal from the Drive file resource:

- prefer Drive `version` when available
- fall back to Drive `modifiedTime`

Implications:

- any spreadsheet content change counts as a revision change
- stale detection is conservative: unrelated edits elsewhere in the sheet will still invalidate the expected revision
- that is acceptable for the current goal, which is to prove the ANAC stale-read recovery loop against a real API-backed tool

## Dependencies

Install the optional live-adapter dependencies:

```bash
python3 -m pip install -r requirements-google-live.txt
```

## Official setup references

- [Create service accounts](https://cloud.google.com/iam/docs/service-accounts-create)
- [Provide credentials to Application Default Credentials](https://cloud.google.com/docs/authentication/provide-credentials-adc)
- [Google Sheets API `values.update`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/update)
- [Google Sheets API `spreadsheets.batchUpdate`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate)
- [Google Drive API `files` resource](https://developers.google.com/workspace/drive/api/reference/rest/v3/files)

## Recommended test setup

Use a dedicated service account and a throwaway spreadsheet.

That keeps the proof clean:

- narrow credentials
- known test data
- safe trace artifacts for committing back into the repo

Suggested test sheet shape:

- one tab named `Q1 Sales`
- headers in row 1: `Rep`, `Region`, `Jan`, `Feb`, `Mar`, `Total`
- 5 to 10 rows of sample numeric data beneath the header

## Service-account checklist

1. Create or choose a Google Cloud project.
2. Enable the Google Sheets API for that project.
3. Enable the Google Drive API for that project.
4. Create a service account.
5. Create a JSON key for that service account.
6. Save the key locally and point `GOOGLE_APPLICATION_CREDENTIALS` to it.
7. Share the throwaway spreadsheet with the service-account email as an editor.
8. Export the spreadsheet id and target tab name:

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account.json
export ANAC_GOOGLE_SPREADSHEET_ID=<spreadsheet-id>
export ANAC_GOOGLE_SHEET_NAME="Q1 Sales"
```

9. Verify setup:

```bash
python3 scripts/anac_google_sheets_live.py --validate-setup
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

## Capture a commit-ready trace

Use the wrapper at [`scripts/capture_google_sheets_trace.py`](../scripts/capture_google_sheets_trace.py) to save a live run into [`docs/traces/`](traces).

Happy path:

```bash
python3 scripts/capture_google_sheets_trace.py \
  --spreadsheet-id <spreadsheet-id> \
  --sheet-name "Q1 Sales" \
  --scenario happy
```

Forced stale path:

```bash
python3 scripts/capture_google_sheets_trace.py \
  --spreadsheet-id <spreadsheet-id> \
  --sheet-name "Q1 Sales" \
  --scenario stale-recovered \
  --force-stale-step insert_summary_row \
  --force-stale-count 1
```

The capture script stores a JSON artifact under [`docs/traces/`](traces) and redacts the spreadsheet id from the metadata envelope.

## Output

The live adapter emits the same runtime structure as the mock executor:

- `status`
- `outcome`
- `trace`
- `final_context_frame`
- `artifacts`

That means you can compare live and mock runs directly.

## Current limits

- this adapter is not part of CI
- the repo currently has no automated Google API credentials
- the revision model is spreadsheet-wide, not cell-specific
- formatting verification is API-level, not visual
- the capture artifacts should only be generated from throwaway test data
