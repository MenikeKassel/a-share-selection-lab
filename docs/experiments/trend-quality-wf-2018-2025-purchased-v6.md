# `trend_quality_v1` Walk-forward experiment record

- Experiment code: `trend-quality-wf-2018-2025-purchased-v6`
- Completed: 2026-08-02
- Status: `succeeded`
- Lifecycle: `experimental`
- Production enabled: `false`
- Research engine: `vectorbt`; formal A-share validation remains required.

## Scope and data boundary

The experiment used the local, read-only purchased snapshot
`ashare-2018-2025-v1`. Raw purchased files, credentials, the SQLite database,
and signal/rejection Parquet files are intentionally not versioned. This
record contains no authorization code and no raw market data.

- Daily rows: 10,221,121 across 5,702 symbols
- Range: 2016-01-04 through 2025-12-31
- Daily coverage audit: 98.1006%; audit valid
- PIT cutoff: 18:30
- Warm-up: 400 trading days; eight annual historical-signal chunks
- Minute confirmation: unavailable; data confidence reduced

## Generated evidence

- Historical signals: 39,252
- Hard-gate rejections: 11,310
- Factor-analysis results: 20, covering composite trend quality, RPS60,
  RPS120, PA score, and risk penalty across four out-of-sample years.
- Four fixed rolling windows; each ran 36 VectorBT parameter combinations.

## Outcome

Every training window had zero parameter sets meeting both predeclared
conditions: positive return after costs and maximum drawdown no greater than
20%. Consequently, no parameter was frozen for formal out-of-sample trading,
and the promotion gates did not pass. This is a valid research conclusion,
not a technical success claim for the strategy.

The compact immutable artifacts committed with this record are:

- [`REPORT.md`](../../data/artifacts/walk-forward/trend-quality-wf-2018-2025-purchased-v6/REPORT.md)
- [`result.json`](../../data/artifacts/walk-forward/trend-quality-wf-2018-2025-purchased-v6/result.json)
- [`manifest.json`](../../data/artifacts/walk-forward/trend-quality-wf-2018-2025-purchased-v6/manifest.json)

The manifest records SHA-256 values for the omitted Parquet evidence as well:

```text
signals.parquet   80d028d6ad4a593b92a2646dd673965ab2dda90a32d163da6e9184f7c1d5fecd
rejected.parquet  a2ad3e50406a415b60a79c50d39329ce1c2cc1bf69eeafd12517771b9b857e6a
```
