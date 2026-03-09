# Live Trace Artifacts

This directory is for credentialed runtime traces captured from the live Google Sheets adapter.

Each trace file should be a JSON artifact emitted by [`scripts/capture_google_sheets_trace.py`](../../scripts/capture_google_sheets_trace.py).

## Naming convention

```text
google-sheets-live-<scenario>-<timestamp>.json
```

Examples:

- `google-sheets-live-happy-20260309T220000Z.json`
- `google-sheets-live-stale-recovered-20260309T220300Z.json`

## Capture commands

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

## What to commit

Commit only traces generated from a throwaway test spreadsheet.

The capture script redacts the spreadsheet id from the metadata envelope, but the runtime payload may still contain:

- sheet names
- row values
- formulas
- trace timing

Use non-sensitive test data.
