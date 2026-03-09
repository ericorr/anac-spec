# Post-Run Checklist

After you capture the first real Google Sheets traces, do these steps in order.

## 1. Capture the matched pair

Run both traces against the same throwaway spreadsheet in the same session.

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

## 2. Apply the wording change

Use the two generated trace paths as inputs to the wording script:

```bash
python3 scripts/apply_live_trace_wording.py \
  --happy docs/traces/google-sheets-live-happy-<timestamp>.json \
  --stale docs/traces/google-sheets-live-stale-recovered-<timestamp>.json
```

This updates [`docs/positioning.md`](../positioning.md) by:

- replacing "experimental live adapter" with validated wording
- linking the two committed trace artifacts
- removing the credential-availability limitation from the current boundaries section
- changing the next-step bullet to focus on adapter expansion / finer-grained revisions

## 3. Review and commit

```bash
git diff -- docs/positioning.md docs/traces/
git add docs/positioning.md docs/traces/
git commit -m "Add live Google Sheets traces"
git push
```

## 4. Optional follow-up

If the traces look clean, also tighten the builder-facing language in [`docs/google-sheets-live.md`](../google-sheets-live.md) from "experimental" to "validated".
