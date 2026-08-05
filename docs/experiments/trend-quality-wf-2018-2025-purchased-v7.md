# `trend_quality_v1` Walk-forward experiment record

- Experiment code: `trend-quality-wf-2018-2025-purchased-v7`
- Completed: 2026-08-05
- Status: `succeeded`
- Lifecycle: `experimental`
- Production enabled: `false`
- Research engine: `vectorbt`; formal A-share validation remains required.

## Scope and data boundary

The experiment used the same local, read-only purchased snapshot
`ashare-2018-2025-v1` as v6, with identical four rolling windows, the 36-cell
parameter grid, costs, drawdown limit, factor version, and signal version.
Raw purchased files, credentials, the SQLite database, and signal/rejection
Parquet files are intentionally not versioned. This record contains no
authorization code and no raw market data.

- Daily rows: 10,221,121 across 5,702 symbols
- Range: 2016-01-04 through 2025-12-31
- Daily coverage audit: 98.1006%; audit valid
- PIT cutoff: 18:30
- Warm-up: 400 trading days; eight annual historical-signal chunks
- Minute confirmation: unavailable; data confidence reduced

## What changed versus v6

v6 evaluated research returns (VectorBT scans, validation selection, factor
IC, forward-return labels) on the raw execution price view, so corporate
actions (splits, dividends, rights issues) showed up as fake losses. v7
strictly separates the two price views:

- Research view: causal adjusted OHLC (`adj_open/adj_high/adj_low/adj_close`,
  `adj_pre_close` when present), `price_basis = causal_hfq`, used only by
  VectorBT scans, validation selection, factor IC/quantiles, and forward
  returns. The strict walk-forward blocks (does not fall back to raw prices)
  when adjusted fields are missing.
- Execution view: raw OHLC + `adj_factor` + cash dividends + ST/suspension/
  limit-up/down/one-word flags + industry + listing days, used only by the
  formal `ashare_daily_v1` engine.

v6 was superseded for strategy interpretation for this reason; its artifacts
remain untouched as an immutable audit record.

## Generated evidence

- Historical signals: identical to v6 (same snapshot, factor and signal
  versions); signal and rejection Parquet hashes match v6 exactly.
- Factor-analysis results: 20, covering composite trend quality, RPS60,
  RPS120, PA score, and risk penalty across four out-of-sample years.
- Four fixed rolling windows; each ran 36 VectorBT parameter combinations.
- Full per-window training scans are archived as
  `training-scan-<train-start>-<train-end>.parquet` (SHA-256 recorded in the
  experiment manifest).

## Outcome

Only the 2019-2021 training window produced eligible parameters (4 of 36
passed positive-return-after-costs and max drawdown <= 20%); it was selected
on validation and ran the 2023 formal out-of-sample test. The other three
windows had no training parameter pass the predeclared filters and are
reported as `not_evaluated` (failure stage `training_filter`) instead of
fabricated zero returns.

2023 formal test (raw execution view, ashare_daily_v1):

- tradable return: -24.10%
- excess vs benchmark: -12.35%
- max drawdown: 26.29%
- closed trades: 752

Promotion gates did not pass (`all_passed = false`), `oos_evaluation_complete
= false` (only 1 of 4 windows evaluated), and the strategy remains
`experimental` with production disabled. No parameters, drawdown limits, or
factor weights were adjusted to improve the outcome; the result is kept as
measured.

The compact immutable artifacts committed with this record are:

- [`REPORT.md`](../../data/artifacts/walk-forward/trend-quality-wf-2018-2025-purchased-v7/REPORT.md)
- [`result.json`](../../data/artifacts/walk-forward/trend-quality-wf-2018-2025-purchased-v7/result.json)
- [`manifest.json`](../../data/artifacts/walk-forward/trend-quality-wf-2018-2025-purchased-v7/manifest.json)

The manifest records SHA-256 values for the omitted Parquet evidence as well:

```text
signals.parquet                    80d028d6ad4a593b92a2646dd673965ab2dda90a32d163da6e9184f7c1d5fecd
rejected.parquet                   a2ad3e50406a415b60a79c50d39329ce1c2cc1bf69eeafd12517771b9b857e6a
training-scan-2018-2020.parquet    828a0bfe166a70f751517819948545b91bb38917f71f77178f7fabf08a90e898
training-scan-2019-2021.parquet    32c34f2573210bc2b85ee82e436966bfab8cc37838f67c7230995e5030c7c0a1
training-scan-2020-2022.parquet    ca88baa604fff8e251ea74a66c5550b0534d0b56bedd31f90eec6bd9f4c91759
training-scan-2021-2023.parquet    7c986e6ff284e37614b19639b1a4a6123e45142f9a8e7d6ece6ed25d3399e71c
```
