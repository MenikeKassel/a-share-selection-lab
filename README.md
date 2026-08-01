# A-Share Selection Lab

> A股每日选股、透明因子研究与自动复盘系统

A-Share Selection Lab 是一个独立的 FastAPI + React 研究系统。它保留自研
选股、PA、威科夫候选、A 股交易约束和不可覆盖快照，通过适配器按模块接入
Alphalens Reloaded、VectorBT、RQAlpha 与 Qlib。

本仓库：

- 不连接或修改 FreeStockDB；
- 不连接或修改任何 KOL 研究台；
- 不自动下单；
- 不预测或承诺“明天必涨停”；
- 不用分钟 K 线伪造 CVD、Delta、Footprint、Level-2 或隐藏订单流。

## 已实现能力

### 正式业务链路

- 日线数据新鲜度、覆盖率和质量闸门；
- 基于 XSHG 交易所日历计算预期交易日，并支持请求级显式覆盖；
- 历史 `available_at`、行业、ST 等 point-in-time 连接；
- 透明趋势、相对强弱、量价、基本面、估值和风险因子；
- 硬门槛拒绝项与正式候选分开返回，且永不进入策略快照；
- Winsorize、Percentile、Z-score、Robust Z-score、方向统一、行业/市值中性；
- 缺失因子按可用权重重新归一化；
- 可配置 PA 分形摆动点、ATR/价格变化/间隔过滤和结构评分；
- 九类威科夫**候选**及支持证据、反证和替代解释；
- 完整 1 分钟会话的 VWAP、开盘区间、同分钟 RVOL、VP/TPO 近似和尾盘强弱；
- 趋势质量、突破启动、基本面改善确认和高风险情绪观察池；
- 原子写入、不可覆盖且覆盖全部输入内容哈希的候选快照；
- 自研 A 股日频正式执行器；
- 1/3/5/10/20/60 日自动复盘；
- Walk-forward 时间切分和生产升级门禁。

### 正式 A 股执行器

`POST /api/v1/backtests` 始终使用 `ashare_daily_v1`，支持：

- 信号日收盘后生成、下一交易日开盘尝试成交；
- T+1；
- 涨停无法买入、跌停无法卖出；
- 一字涨停、一字跌停、停牌；
- 100 股整数手；
- 手续费、最低手续费、卖出印花税和滑点；
- 现金、持仓、调仓、单股上限和行业上限；
- 复权因子变化和现金分红；
- `theoretical_return` 与 `tradable_return` 分开报告；
- 无法成交原因单独保留。

`tradable_return` 来自包含全部 A 股交易约束和成本的正式路径；
`theoretical_return` 使用同一信号、调仓日和组合路径，仅关闭停牌/涨跌停限制、
手续费、印花税和滑点，用作可比的无摩擦基线，而不是信号日收盘价收益。

VectorBT 和 RQAlpha 的结果都不会替代该正式结果。

## 架构

```text
backend/app/
├── adapters/                 # 所有第三方量化库调用
│   ├── alphalens/
│   ├── vectorbt/
│   ├── rqalpha/
│   └── qlib/
├── api/                      # FastAPI 路由和请求 Schema
├── core/                     # 配置
├── data/                     # 数据契约、历史状态、新鲜度闸门
├── db/                       # SQLAlchemy、仓储和 Alembic
├── domain/                   # 稳定协议与统一结果
├── engines/                  # optional 引擎发现
├── execution/                # 自研正式 A 股执行器
├── research/                 # 因子、PA、威科夫、分钟、Walk-forward
├── selection/                # 选股、策略池、快照、复盘
└── services/                 # 研究任务编排

frontend/src/
├── components/
├── pages/
│   ├── ResearchEngines.tsx
│   ├── FactorResearch.tsx
│   ├── Backtests.tsx
│   └── MLExperiments.tsx
└── api.ts
```

业务服务只依赖系统协议和适配器门面。下列导入不会散落到业务目录：

```python
import alphalens
import vectorbt
import rqalpha
import qlib
```

## 环境要求

- Windows、Linux 或 macOS；
- Python 3.11 或 3.12；
- Node.js 20.19+（本地验证为 24.15.0）；
- npm 10+；
- 推荐使用 `uv`。

本地完整验证环境：

```text
Python                    3.11
Pandas                    2.3.3
NumPy                     1.26.4
FastAPI                   0.141.1
exchange-calendars        4.13.2
Alphalens Reloaded        0.4.6
VectorBT                  0.28.5
RQAlpha                   6.3.0
Qlib / pyqlib             0.9.7
LightGBM                  4.7.0
React                     19.2.8
React Router              7.18.2
Vite                      8.1.5
Vitest                    4.1.10
```

VectorBT 1.x 要求 Pandas 3，而 Alphalens Reloaded 0.4.6 要求 Pandas <3。
因此本项目固定 VectorBT 0.28.5 + Pandas 2.3.x，这是经过依赖求解和真实
烟测的兼容组合。

## 安装

### 核心系统

核心系统不安装任何外部量化引擎也能启动：

```powershell
uv sync --extra dev
uv run alembic upgrade head
uv run ashare-lab serve --host 127.0.0.1 --port 8000
```

打开：

- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/v1/health>

### 前端

```powershell
cd frontend
npm ci
Copy-Item .env.example .env
npm run dev
```

打开 <http://127.0.0.1:5173>。开发服务器会将 `/api` 代理到
`127.0.0.1:8000`。

### 可选量化引擎

按需安装：

```powershell
uv sync --extra factor-research
uv sync --extra fast-backtest
uv sync --extra rqalpha-validation
uv sync --extra ml-research
uv sync --extra quant-all
```

也可以同时保留开发工具：

```powershell
uv sync --extra quant-all --extra dev
```

主系统会在运行时发现包。缺失包返回 `unavailable` 和明确安装命令，不会造成
FastAPI 启动失败。

## 第三方库、许可证与商业使用

| 引擎 | 是否必需 | 已验证版本 | 用途 | 许可证/注意事项 |
|---|---:|---:|---|---|
| exchange-calendars | 是 | 4.13.2 | XSHG 交易日历和正式数据新鲜度闸门 | Apache-2.0 |
| Alphalens Reloaded | 否 | 0.4.6 | IC、Rank IC、分组收益、换手、行业分析 | Apache-2.0 |
| VectorBT | 否 | 0.28.5 | 快速参数扫描和敏感性研究 | Apache-2.0 with Commons Clause；不得销售主要由该软件构成的产品或服务，商业使用前应单独审查 |
| RQAlpha | 否 | 6.3.0 | 事件驱动订单、账户和费用交叉验证 | Apache-2.0；正式运行需要单独准备 RQAlpha 中国市场 bundle |
| Qlib / pyqlib | 否 | 0.9.7 | 因子数据导出、实验记录和预测导入 | MIT；这里只作实验引擎 |
| LightGBM | 否 | 4.7.0 | Qlib 最小透明基线实验 | MIT |

版本与元数据来源：
[exchange-calendars](https://pypi.org/project/exchange-calendars/4.13.2/)、 
[Alphalens Reloaded](https://pypi.org/project/alphalens-reloaded/0.4.6/)、
[VectorBT](https://pypi.org/project/vectorbt/0.28.5/)、
[RQAlpha](https://pypi.org/project/rqalpha/)、
[Qlib](https://pypi.org/project/pyqlib/)。

许可证表是工程提示，不是法律意见。商业发布前应由使用方复核所有直接和传递
依赖许可证。

## 数据目录

```text
data/
├── raw/          # 用户显式导入的原始快照，不提交 Git
├── processed/    # 规范化日线、分钟、因子 Parquet，不提交 Git
├── artifacts/    # 引擎任务、候选和复盘产物，不提交 Git
└── exports/      # 显式导出的研究结果，不提交 Git
```

SQLite 只保存任务、版本、汇总、候选快照元数据和复盘汇总。大规模逐日因子明细
必须保留在 Parquet。

### 日线数据契约

必需字段：

```text
date
symbol
open
high
low
close
volume
amount
```

建议字段：

```text
turnover_rate
industry
market_cap
adj_factor
cash_dividend_per_share
limit_up
limit_down
one_word_limit_up
one_word_limit_down
suspended
is_st
listing_days
delisting_risk
```

### 1 分钟数据契约

```text
timestamp
open
high
low
close
volume
amount
```

分钟确认只在完整上午 120 分钟 + 下午 120 分钟时运行。VWAP 使用：

```text
cumulative_amount / cumulative_volume
```

调用方必须正确指定供应商成交量是 `shares` 还是 `lots`。若 VWAP 明显落在实际
成交区间外，系统会抛出单位不匹配错误，不会静默生成错误结果。

### 财务、估值与公告

必须带审计字段：

```text
period_end
published_at
available_at
fetched_at
source
content_hash
```

历史计算只会连接 `available_at <= 当日 18:30` 的记录，避免把收盘后尚未可见的
财务信息带入当日因子。行业、ST 和股票池状态使用
`effective_date` 做向后 as-of 连接，后续状态不会覆盖历史。

## 数据新鲜度闸门

正式选股前计算：

```text
expected_latest_trade_date
daily_market_max_date
minute_market_max_date
daily_coverage_ratio
minute_coverage_ratio
```

日线落后时：

```text
selection_status = blocked_stale_daily_data
```

并返回：

```text
行情尚未更新至最新交易日。
当前最新有效选股日期：YYYY-MM-DD。
```

分钟缺失允许降级：

```text
minute_confirmation = unavailable
minute_score = null
data_confidence = reduced
```

分钟缺失从不记作 0 分。

## 分钟近似与不可用数据

系统固定展示：

```text
cvd = unavailable
bid_ask_delta = unavailable
footprint = unavailable
absorption = unavailable
iceberg_order = unavailable
level2_orderbook = unavailable
option_wall = unavailable
```

VP 固定提示：

```text
Volume Profile基于1分钟K线估算，不等同于逐笔成交分布。
```

TPO 固定提示：

```text
TPO基于1分钟K线近似，无法还原分钟内部价格路径。
```

## 数据库迁移

数据库默认为：

```text
sqlite:///./data/a_share_selection_lab.db
```

执行迁移：

```powershell
uv run alembic upgrade head
uv run alembic current
```

首个迁移建立：

- `external_engine_runs`
- `engine_comparisons`
- `factor_analysis_results`
- `model_experiments`
- `backtest_runs`
- `data_quality_snapshots`
- `selection_snapshots`
- `candidate_reviews`

候选快照由以下组合唯一标识，不允许覆盖：

```text
selection_date
strategy_code
strategy_version
factor_version
data_snapshot_version
```

`data_snapshot_version` 对日线、财务、估值、基准、板块 RPS、每只股票的分钟数据
及选股配置共同取内容哈希。快照使用原子非覆盖写入；同一版本再次写入会明确失败。

## API

### 系统与引擎

```http
GET /api/v1/health
GET /api/v1/engines
GET /api/v1/engines/status
```

### 因子研究

```http
POST /api/v1/factor-analysis/run
GET  /api/v1/factor-analysis
GET  /api/v1/factor-analysis/{run_id}
```

一次研究默认先保存自研计算基线，再调用 Alphalens 做交叉验证；即使 Alphalens
未安装，自研基线仍会保留。可用 `include_native_baseline=false` 显式关闭基线。

### 回测

```http
POST /api/v1/backtests
GET  /api/v1/backtests
GET  /api/v1/backtests/{run_id}

POST /api/v1/research-backtests/vectorbt
GET  /api/v1/research-backtests/{run_id}

POST /api/v1/validation-backtests/rqalpha
GET  /api/v1/validation-backtests/{run_id}
```

### Qlib 实验与比较

```http
POST /api/v1/ml-experiments/qlib
GET  /api/v1/ml-experiments
GET  /api/v1/ml-experiments/{experiment_id}

GET /api/v1/engine-comparisons
GET /api/v1/engine-comparisons/{comparison_id}
```

### 正式选股与复盘

```http
POST /api/v1/selections/run
GET  /api/v1/selections
GET  /api/v1/selections/latest
GET  /api/v1/data-quality/latest

POST /api/v1/reviews/run
```

所有输入文件路径都应指向本仓库明确导入的数据目录。本项目不会自动发现或连接
其他仓库的数据。

## 前端路由

| 路由 | 页面 |
|---|---|
| `/` | 每日候选和数据闸门 |
| `/research-engines` | 引擎安装、版本、作用、最近任务和许可证 |
| `/factor-research` | 自研/Alphalens IC、分组收益、衰减和换手 |
| `/backtests` | 正式 A 股、VectorBT 快研、RQAlpha 验证三类结果 |
| `/ml-experiments` | Qlib 实验、时间切分和规则模型对比 |

## 调度

设置：

```text
ASHARE_SCHEDULER_ENABLED=true
```

可选任务：

| 任务 | 默认计划 | 默认启用 |
|---|---|---:|
| `daily_factor_analysis` | 周一至周五 18:45 | 是 |
| `weekly_parameter_research` | 周六 09:00 | 否 |
| `weekly_strategy_validation` | 周六 13:00 | 否 |
| `monthly_qlib_experiment` | 每月 1 日 02:00 | 否 |

任务需要由部署方显式提供数据输入。未配置输入时会记录跳过。每个 optional 任务都
在异常隔离包装器中运行；失败不会影响数据更新、正式选股、正式复盘和自研回测。
Qlib 使用独立月度任务且 `max_instances=1`，不会阻塞每日正式链路。

可直接复制 `config/examples/` 中的四个 JSON 请求示例，再把其中路径改为
`ASHARE_DATA_ROOT` 内的 Parquet/CSV 数据。VectorBT 的参数网格支持 `top_n`、
持有期、调仓频率、成本、`factor_weights`、ATR、突破量比、PA 分数和风险扣分
阈值；权重和阈值会真实改变入选信号。

环境变量见 [.env.example](.env.example)。

## Walk-forward 与策略生命周期

支持年度滚动：

```text
训练 2021—2023 / 验证 2024 / 测试 2025
训练 2022—2024 / 验证 2025 / 测试 2026
```

状态：

```text
experimental
validated
production_candidate
production
retired
```

方向一致、邻域稳定、扣除成本有效、不过度依赖极端股票、跨行业、回撤可接受、
IC 方向稳定和样本外健康八项全部通过，才会进入 `production_candidate`。
VectorBT/Qlib 即使全部通过也必须人工审批，并由正式 A 股执行器复核。

## 验证

后端：

```powershell
uv run ruff check backend
uv run mypy backend/app
uv run pytest backend/tests
```

前端：

```powershell
cd frontend
npm run format:check
npm run typecheck
npm test
npm run build
```

测试覆盖：

- 日线过期阻断和分钟缺失降级；
- `available_at`、历史行业和历史 ST 防未来数据；
- Alphalens 输入/结果转换和真实 IC 一致性；
- VectorBT 次日信号和真实参数场景；
- RQAlpha 缺失降级、信号模板和差异报告；
- Qlib 严格切分、预测导入和真实 LightGBM 最小实验；
- T+1、整手、涨停不可买、跌停不可卖和交易成本；
- 候选快照不可覆盖；
- Walk-forward 不自动生产；
- FastAPI 关键端点；
- React 结果分类、类型检查、测试和生产构建。

## 第一次端到端实验

在安装全部可选研究依赖后运行：

```powershell
uv sync --extra quant-all --extra dev
uv run ashare-lab first-experiment
```

该命令生成一组固定随机种子的合成 A 股风格数据，并实际运行数据质量闸门、正式
选股与四类候选快照、自研/Alphalens 因子研究、VectorBT 参数扫描、正式 A 股
回测、Qlib/LightGBM 时间切分实验和六个持有期的自动复盘。它不连接其他仓库，
不自动下单，所有结果都带 `synthetic_data=true` 和 `production_enabled=false`。

RQAlpha 只有在部署方提供独立中国市场 bundle 后才执行事件驱动交叉验证；未提供
bundle 时，本实验会把该步骤记录为已接受的 `skipped`，适配器、信号模板、结果
导入和差异报告仍由自动测试覆盖。

每次运行写入独立且不覆盖的目录：

```text
data/experiments/first/<run_id>/
├── REPORT.md
├── manifest.json
├── experiment.db
├── inputs/
└── artifacts/
```

该目录默认被 Git 忽略。命令只有在所有必需步骤通过时才以成功状态退出，否则
保存失败清单并返回非零退出码。

## 配置

复制环境变量模板：

```powershell
Copy-Item .env.example .env
```

重要配置：

```text
ASHARE_DATABASE_URL
ASHARE_DATA_ROOT
ASHARE_ARTIFACT_ROOT
ASHARE_TIMEZONE
ASHARE_CORS_ORIGINS
ASHARE_MIN_DAILY_COVERAGE_RATIO
ASHARE_EXPECTED_UNIVERSE_SIZE
ASHARE_SCHEDULER_ENABLED
```

## 2018--2025 trend-quality Walk-forward 验证

本仓库新增独立的真实数据验证入口，数据只从本仓库的
`data/raw/imports/ashare-2018-2025-v1/manifest.json` 读取，不连接
FreeStockDB、ai-hub 或 KOL 项目。导入快照应至少包含：

```text
daily.(parquet|csv)       # date/symbol/OHLCV/amount，另含复权和交易状态
benchmark.(parquet|csv)   # 含 000300.SH
financials.(parquet|csv)  # period_end/published_at/available_at/fetched_at/source/content_hash
valuations.(parquet|csv)  # 同上审计字段
state_history.(parquet|csv) # 可选，effective_date 的历史股票池/ST/行业状态
```

`manifest.json` 必须声明 `snapshot_id`、`immutable=true`、`audit_valid=true`、
`coverage_ratio>=0.95` 和 `point_in_time_cutoff=18:30`；文件路径必须位于 manifest
目录内。服务会再次读取日线检查重复键、OHLC、覆盖率、PIT 审计字段和内容哈希，失败即
保存为 `blocked/experimental`，不会用合成数据冒充真实样本。

启动 API 后运行本轮实验：

```powershell
$payload = @{
  experiment_code = "trend-quality-wf-2018-2025-v1"
  strategy_code = "trend_quality_v1"
  snapshot_manifest_path = "data/raw/imports/ashare-2018-2025-v1/manifest.json"
} | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/api/v1/walk-forward-experiments `
  -Method Post -ContentType "application/json" -Body $payload
```

四个固定窗口为 `2018--2020/2021/2022`、`2019--2021/2022/2023`、
`2020--2022/2023/2024`、`2021--2023/2024/2025`。每窗扫描
`top_n=[5,10,20]`、持有期 `[5,10,20]`、日/周调仓、5/10bps 滑点；参数只在训练/验证期
选择，测试期冻结。VectorBT 只作快速扫描，正式收益、T+1、整手、涨跌停、停牌、费用和
无法成交记录均由自研 `ashare_daily_v1` 复核。通过全部门禁也只会进入
`validated`，始终不会自动启用生产。

结果查看：

```text
GET /api/v1/walk-forward-experiments
GET /api/v1/walk-forward-experiments/{id}
```

前端路径为 `/walk-forward-experiments`。当前工作区尚未提供独立的 2018--2025 真实快照，
因此默认请求会被明确记录为 `blocked`；这是数据边界保护，不是回测收益结论。

不要把 `.env`、数据库、原始行情、Parquet 因子明细或引擎 artifact 提交到 Git。
