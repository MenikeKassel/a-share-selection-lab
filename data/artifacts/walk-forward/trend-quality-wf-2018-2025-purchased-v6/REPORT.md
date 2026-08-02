# trend_quality_v1 Walk-forward report

- lifecycle: `experimental`
- production_enabled: `false`
- all gates passed: `False`

## Windows

| train | validation | test | selected parameters | failure reason | tradable return | excess | max drawdown | closed trades |
|---|---|---|---|---|---:|---:|---:|---:|
| 2018-01-01..2020-12-31 | 2021-01-01..2021-12-31 | 2022-01-01..2022-12-31 | `{}` | no training parameter passed cost and drawdown filters | 0.0000% | 0.0000% | 0.0000% | 0 |
| 2019-01-01..2021-12-31 | 2022-01-01..2022-12-31 | 2023-01-01..2023-12-31 | `{}` | no training parameter passed cost and drawdown filters | 0.0000% | 0.0000% | 0.0000% | 0 |
| 2020-01-01..2022-12-31 | 2023-01-01..2023-12-31 | 2024-01-01..2024-12-31 | `{}` | no training parameter passed cost and drawdown filters | 0.0000% | 0.0000% | 0.0000% | 0 |
| 2021-01-01..2023-12-31 | 2024-01-01..2024-12-31 | 2025-01-01..2025-12-31 | `{}` | no training parameter passed cost and drawdown filters | 0.0000% | 0.0000% | 0.0000% | 0 |

## Gates

- `positive_tradable_excess_3_of_4`: **False**
- `median_oos_excess_positive`: **False**
- `composite_rank_ic_positive_3_of_4`: **False**
- `combined_oos_max_drawdown_within_limit`: **True**
- `stress_10bps_excess_positive`: **False**
- `adjacent_parameters_positive_60pct`: **False**
- `at_least_200_closed_trades`: **False**
- `top_5_trades_contribution_within_35pct`: **False**
- `at_least_3_positive_industries`: **False**
- `single_industry_contribution_within_40pct`: **False**
- `all_passed`: **False**

This artifact is research-only; it is not an order or an investment recommendation.
