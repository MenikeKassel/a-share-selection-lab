# v7 → v8 Signal Difference Attribution Audit

One-shot audit comparing the frozen v7 signal/rejection artifacts with a
fresh v8 generation from the upgraded schema-v2 snapshot.

## Run

```bash
# full run (regenerates v8 signals, ~2-2.5h):
uv run python _docs/signal_audit_v7_v8.py

# re-diff only, reusing existing v8 artifacts after a crash:
uv run python _docs/signal_audit_v7_v8.py --reuse-v8
```

## Inputs

| Artifact | Path |
|---|---|
| v7 signals | `data/artifacts/walk-forward/trend-quality-wf-2018-2025-purchased-v7/signals.parquet` |
| v7 rejected | `data/artifacts/walk-forward/trend-quality-wf-2018-2025-purchased-v7/rejected.parquet` |
| Snapshot manifest | `data/raw/imports/ashare-2018-2025-v1/manifest.json` (schema v2) |

The v8 generation reuses the exact data-loading path of
`WalkForwardTaskService._run_from_manifest` (security master →
listing_days override, PIT-validated financials/valuations, state
history, 400-trading-day warm-up chunks, year-by-year replay).

## Outputs (`data/artifacts/signal-audit-v7-v8/`)

| File | Content |
|---|---|
| `summary.json` | all counts, cause attribution, SHA-256 fingerprints, code-computed `audit_passed` |
| `v8-signals.parquet` / `v8-rejected.parquet` | fresh generation |
| `signal-diff.parquet` | full signal diff (added/removed/score_changed/unchanged) |
| `rejected-diff.parquet` | full-outer-join rejection diff (unchanged/added/removed/reason_changed/evidence_changed) + evidence columns |
| `v7-signal-hash.txt` / `v8-signal-hash.txt` | SHA-256 fingerprints |
| `funnel-daily.csv` | per-day candidate funnel (raw → after filters → final) |

## audit_passed conditions (computed in code)

- signal added / removed / score_changed == 0
- rejected unexplained == 0
- PIT future-visible records == 0
- signals missing real list_date == 0
- signals with real listing_days < 60 == 0
- same input regenerates the same hash (verified across two runs)

## Not committed

`signal-diff.parquet`, `rejected-diff.parquet`, `v8-signals.parquet`,
`v8-rejected.parquet`, purchased data and other large intermediates stay
local under `data/artifacts/`; `summary.json` records their SHA-256.

## Result (2026-08-06)

`audit_passed=true`. v8 signals and rejections are byte-identical to v7
(same SHA-256), because the fixes (real listing dates, historical ST,
PIT cutoff) only changed the intermediate pool (678,840 daily rows
rewritten; 183,497 rows under 60 days inside the signal window) while no
such security ever reached the final candidate pool. Frozen hashes:

- v7/v8 signals: `80d028d6ad4a593b92a2646dd673965ab2dda90a32d163da6e9184f7c1d5fecd`
- v7/v8 rejected: `a2ad3e50406a415b60a79c50d39329ce1c2cc1bf69eeafd12517771b9b857e6a`
