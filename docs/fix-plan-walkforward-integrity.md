# Walk-forward 真实性修复实施方案（v1.2 定稿）

> 文档版本：v1.2（2026-08-05，用户最终拍板）
> 变更历史：
> - v1.0 初版方案（Hermes 起草，docs/fix-plan-walkforward-integrity.md）
> - v1.1 并入 GPT 修复实施计划（PR 1-6 + Schema v2 + v8），逐条实测验证，标注数据矛盾与决策点
> - v1.2 并入用户最终决策（D1 定稿）：c → 成功则 strict；失败则 total_return_proxy（受约束的 b）；双执行器拆分；manifest 能力分级
>
> 状态：**已定稿，实施中（按用户批准顺序执行）**
> 关联实验：v7 及更早产物不可覆盖；修复后新实验 `trend-quality-wf-2018-2025-purchased-v8`

---

## 一、总体原则（GPT 计划，采纳）

1. 信号只能使用当时已知的信息
2. 研究筛参和正式回测使用一致的成交时点
3. 持仓、公司行为、成交盈亏形成可审计账本
4. 数据不足时明确阻断，不近似替代
5. v7 作为不可变历史记录，修复后创建全新 v8
6. 修复前后策略参数、因子权重、晋级标准不得为改善结果而调整

新增版本标识（GPT 建议，采纳）：

```text
snapshot_schema_version = 2
execution_engine = ashare_daily_v2
experiment_code = trend-quality-wf-2018-2025-purchased-v8
```

---

## 二、实测验证结论（Hermes 已核实，2026-08-05）

以下为对 GPT 计划关键论断的本地实证，**全部基于真实数据/代码**：

### ✅ 确认属实（GPT 计划成立的基础）

| # | 论断 | 证据 |
|---|---|---|
| 1 | `ashare_daily.py` 用 `adj_factor` 比例改持仓数量 | `ashare_daily.py:364-368`：`adjusted = int(position.quantity * new_factor / position.adj_factor); position.quantity = adjusted` |
| 2 | 导入器把 `adj_factor` 当价格复权因子 | `importer.py:304-311`：`scale = factor / first_adj; adj_price = raw * scale` |
| 3 | 收盘涨跌停状态泄漏到次日开盘 | `importer.py:314-316` 用全天 `pct_chg` 推导 `limit_up`；`ashare_daily.py:344-352` 开盘判断读 `limit_up/one_word_*` |
| 4 | VectorBT 用 `next_bar_close` + `.ffill()` | `signal_converter.py:44`（ffill）、`:81`（`execution_proxy: next_bar_close`） |
| 5 | `_trade_concentration` FIFO 只弹一笔 | `walk_forward.py:1262`：`buy = buys[symbol].pop(0)` |
| 6 | PIT 校验未真正检查 cutoff | `snapshots.py:360-364`：`cutoff_delta` 计算后 `_ = cutoff_delta` 丢弃 |
| 7 | TinyShare 探测只验导入不验运行 | `tinyshare/worker.py:20`：`__package_info__` 只 import + 哈希；manifest 记录 `package` 成功但 capabilities 部分接口报错 |

### ⚠️ 关键数据矛盾（GPT 计划需修正的点）

**GPT 计划 PR 1.3 要求严格回测时 `explicit_corporate_actions=true`，但购买数据中不存在任何公司行为明细字段。**

实测证据：
- 导入器 `_REQUIRED_COLUMNS`（importer.py:40-51）仅含 12 列：`trade_date, name, open, high, low, close, pre_close, vol, amount, adj_factor, first_adj, ts_code`（+ `change, pct_chg`），**无 `cash_dividend_per_share`、无 `share_bonus_ratio/capitalization_ratio/rights_issue_ratio/conversion_ratio`、无 `split_ratio`**
- `daily.parquet` 实际列（已读）确无这些字段；执行器 `ashare_daily.py:369` 读 `row.get("cash_dividend_per_share", 0.0)` 恒为 0
- 快照 manifest 无 `schema_version`、无 `capabilities` 字段
- `tinyshare_capabilities.json` 仅证明 stock_basic/trade_cal 等可用，**未提供分红送转数据接口的证据**

**结论**：若按 GPT 计划"缺少明确公司行为数据 → 正式执行 blocked"，则 **v8 将无法运行正式执行**（`failure_stage=snapshot_capability_gate`），除非：
- (a) TinyShare 补充接口能提供分红/送转/配股明细（需实测探测，当前无凭证）
- (b) 明确接受 `explicit_corporate_actions=false` + `execution_confidence=reduced`，v8 正式执行降级运行并在报告标注（与 GPT 计划第 1.3 节冲突，需用户拍板）

### ✅ 已存在但计划未充分利用的数据

- `universe.parquet` 含**真实 `list_date`/`delist_date`**（如 000001.SZ=1991-04-03）→ PR 5 的 security_master 已有基础，`listing_days` 可直接改用真实上市日期
- `state_history.parquet` 含 `effective_date/industry/list_date/is_st` → PR 5 的历史状态表已有基础
- 已存 `trading_calendar.parquet` → PR 5 的交易日历推进可用

---

## 三、PR 1：修复公司行为处理（GPT 计划，含修正）

### 1.1 禁止 `adj_factor` 修改真实持股数量 ✅ 采纳

`ashare_daily.py` 删除：

```python
adjusted = int(position.quantity * new_factor / position.adj_factor)
position.quantity = adjusted
```

`adj_factor` 仅用于构造研究复权价格。

### 1.2 新增明确公司行为数据（**修正**：购买数据无此字段）

- 新增 `backend/app/execution/corporate_actions.py`，定义 `action_type ∈ {cash_dividend, bonus_share, capitalization, split, rights_issue}` 及处理规则（送转：`new_qty = old_qty * (1 + bonus + capitalization) * split_ratio`；现金按**动作前**股数；配股默认 `ignore`）
- **修正**：快照中无公司行为明细时，**不伪造**；manifest `capabilities.explicit_corporate_actions=false`；正式执行按用户拍板结果处理（见决策点 D1）

### 1.3 数据不足失败关闭 ✅ 采纳

- manifest 增加 `capabilities` 块；严格正式回测要求全部 true
- 研究扫描可用因果复权价继续；正式执行缺能力时 blocked 或降级（D1）

### 1.4 测试 ✅ 采纳

`tests/execution/test_corporate_actions.py`：纯现金分红股数不变、adj_factor 变化股数不变、10送2 变 1200 股、送股后成本不凭空增加、同日送股+分红顺序固定、缺能力时严格回测阻断。

---

## 四、PR 2：修复涨跌停和开盘成交判断（GPT 计划，采纳）

### 2.1 拆分字段

`limit_up/limit_down` → `close_at_limit_up/close_at_limit_down`（保留旧名 deprecated 或直接重命名）；新增 `limit_up_price/limit_down_price/open_at_limit_up/open_at_limit_down/one_word_limit_up/one_word_limit_down/price_limit_rule_version`。

### 2.2 执行器只读开盘可知数据

- 买入阻断：`suspended` +（conservative 时）`open_at_limit_up`
- 卖出阻断：`suspended` +（conservative 时）`open_at_limit_down`
- **禁止** `_buy_block_reason/_sell_block_reason` 读 `close_at_limit_up/down/one_word_*`

### 2.3 日线成交模型

`execution_policy ∈ {daily_conservative, daily_optimistic}`；正式实验默认 `daily_conservative`；报告标注日线近似。

### 2.4 涨跌停规则数据化

新增 `backend/app/market_rules/price_limits.py`：按 `exchange/board/is_st/trade_date/listing_days/effective_from/effective_to` 查表；Decimal 舍入。**注**：创业板 2020-08-24 起 20%、科创板 2019-07-22 起 20%、北交所 30%、ST 5%、新股首日等规则需在表内表达。

### 2.5 测试 ✅ 采纳（开盘正常收盘涨停不误阻、开盘涨停收盘未涨停保守阻、一字板不影响开盘决策、制度日期边界、ST/创业板/科创板/北交所/新股、舍入边界）

---

## 五、PR 3：统一筛参和正式执行逻辑（GPT 计划，采纳）

### 3.1 四层流程

```
VectorBT 粗筛 → ashare_daily_v2 训练期复核 → ashare_daily_v2 验证期选参 → ashare_daily_v2 样本外测试
```

`training_pass/validation_winner/promotion_gate` 全部来自 `ashare_daily_v2`；36 组参数全部过正式复核。

### 3.2 VectorBT next-open + 禁 ffill

- `signal_converter.py`：`next_bar_close` → `next_bar_open`；研究价格 `adj_open` 成交、`adj_close` 估值
- 禁止执行价格 `.ffill()`；缺开盘价当日不可成交
- 重构 `ResearchPriceMatrix(execution_open, valuation_close, tradable_mask, entries, exits)`

### 3.3 差异报告

每参数输出 `vectorbt_return/formal_train_return/return_gap/.../research_formal_divergence` 阈值告警。

### 3.4 测试 ✅ 采纳

---

## 六、PR 4：FIFO 持仓与已实现盈亏账本（GPT 计划，采纳）

### 4.1-4.2

新增 `backend/app/execution/lot_ledger.py`（`PositionLot`：lot_id/symbol/entry_date/quantity_remaining/entry_price/allocated_buy_commission/unit_cost/industry_at_entry）；卖出输出 `realized_pnl/matched_cost/matched_quantity/realized_return/industry_at_entry/industry_at_exit/matched_lot_ids`；佣金按匹配数量分摊。

### 4.3 重写集中度统计

`walk_forward.py` 删除 `buy = buys[symbol].pop(0)`；聚合执行器 `realized_pnl`；行业按 `industry_at_entry`。

### 4.4 测试 ✅ 采纳（买100+买200+卖150 等 8 项）

---

## 七、PR 5：证券主数据和 PIT 审计（GPT 计划，采纳 + 本地基础）

### 5.1 security_master / security_state_history

- `security_master.parquet`（symbol/exchange/board/security_type/list_date/delist_date）
- `security_state_history.parquet`（date/symbol/name/is_st/delisting_risk/listing_status/published_at/available_at）
- `listing_days = trade_date - list_date`（**改用 universe.parquet 真实 list_date，不再用 CSV 首行**）
- 本地已有：universe.parquet（真实 list_date）、state_history.parquet、trading_calendar.parquet

### 5.2 PIT cutoff 语义

- 统一 `asof_for_signal_date(frame, signal_date, cutoff)`：`available_at <= D 18:30`
- 仅日期无时间记录 → 下一交易日 18:30
- 因子数据保留 `source_period_end/source_published_at/source_available_at/signal_cutoff_at`
- `snapshots.py:360-364` 补上真正的 cutoff 校验

### 5.3 PIT 测试 ✅ 采纳（18:29 可用 / 18:31 不可用 / D+1 09:00 不可用 / 周末节假日 / 哈希变化）

---

## 八、PR 6：TinyShare 运行环境加固（GPT 计划，采纳）

- `__runtime_info__` 探测（sys.executable/version/prefix/home/模块路径/API factory/最小无害调用，不返回 token）
- 配置解释器不存在 → 默认失败；仅 `-AllowInterpreterFallback` 允许回退
- runtime probe 失败 → 不再连续调用接口

---

## 九、快照 Schema v2（GPT 计划，采纳）

```json
{
  "schema_version": 2,
  "execution_capabilities": {
    "explicit_corporate_actions": true,
    "historical_security_master": true,
    "historical_security_state": true,
    "historical_price_limit_rules": true,
    "open_tradability_model": "daily_conservative",
    "exact_queue_matching": false
  },
  "price_conventions": {
    "research_execution_price": "causal_adjusted_next_open",
    "research_valuation_price": "causal_adjusted_close",
    "formal_execution_price": "raw_next_open",
    "formal_valuation_price": "raw_close"
  }
}
```

严格 Walk-forward 启动条件：schema_version>=2 + 全部 capabilities + PIT + 哈希 + 覆盖。缺失 → `status=blocked, failure_stage=snapshot_capability_gate`。

**⚠️ 修正**：`explicit_corporate_actions` 当前数据无法为 true（见第二节矛盾）——v8 实际取值取决于决策点 D1。

---

## 十、v8 重跑流程（GPT 计划，采纳）

1. 不修改 v7 文档/产物
2. 重新导入 Schema v2 快照（含 security_master/state_history 重建、涨跌停规则表、公司行为能力标注）
3. 快照哈希 + PIT 审计
4. 历史信号不变性检查（signals.parquet SHA-256 == v7）
5. 相同 4 窗口、36 参数、因子权重/成本/回撤/晋级门槛
6. `ashare_daily_v2` 训练/验证/OOS
7. v7 vs v8 差异报告（信号/成交/涨跌停/公司行为/FIFO/收益/回撤/门禁）
8. 无论结果好坏保存；lifecycle=experimental、production_enabled=false

---

## 十一、决策点（已定稿）

### D1（最终拍板）：公司行为数据策略 = c → 成功则 strict；失败则受约束的 b

**用户决策原文要点**：

1. **先执行 (c) 探测 TinyShare 公司行为能力**；若无法补齐可靠的公司行为明细，则执行"受约束的 (b)"——允许 v8 产出降级执行结果，但严格正式执行和策略晋级继续 blocked
2. 不单独选 (a)（否则永远只能做研究扫描）；也不能用原始价格忽略公司行为跑 (b)（会制造假亏损）
3. **v8 同时输出两种状态**（实验完成与否 与 严格正式标准拆开）：

```json
{
  "experiment_status": "completed",
  "research_scan_available": true,
  "proxy_execution_available": true,
  "strict_execution_status": "blocked",
  "execution_confidence": "reduced",
  "promotion_eligible": false,
  "production_enabled": false
}
```

4. **不能采用的降级方式**：原始价格 + 股数不变 + 分红 0（除权日造假亏损）；也不恢复 `quantity *= adj_factor 比例`（adj_factor 非送转比例）
5. **正确的降级模式：总回报代理（total_return_proxy）**

### 公司行为三模式

```text
corporate_action_mode = explicit | total_return_proxy | unavailable
```

- `explicit`：有完整明细时——现金分红入账、送转拆调整股数、配股按政策；可获得严格执行结果
- `total_return_proxy`（v8 无明细时使用）：
  - 原始开盘价负责判断可成交性/涨跌停/停牌/初始整手数量
  - **因果复权价格负责持仓期间的经济收益**
  - 不根据 `adj_factor` 直接修改持股股数；不额外发现金股息（避免与复权总回报重复计算）
  - 佣金/印花税按代理成交名义金额计算
  - 报告明确：股数、分红现金流、公司行为后整手状态并非精确重建

单个买入 lot 记录：`entry_raw_notional / entry_adj_open / entry_raw_quantity`

```text
proxy_market_value(t) = entry_raw_notional × causal_adj_close(t) ÷ causal_adj_open(entry)
proxy_exit_value      = entry_raw_notional × causal_adj_open(exit) ÷ causal_adj_open(entry)
```

局限（只能标记 reduced）：送转后真实股数、零碎股、配股认购、分红再投资、公司行为后最低佣金/整手约束。

### 双执行器拆分（用户拍板）

```text
ashare_daily_v2_proxy   当前购买数据即可运行
  {"formal_ashare_result": false, "execution_result_level": "proxy",
   "corporate_action_mode": "total_return_proxy", "execution_confidence": "reduced"}

ashare_daily_v2_strict  仅公司行为能力通过才运行
  {"formal_ashare_result": true, "execution_result_level": "strict",
   "corporate_action_mode": "explicit", "execution_confidence": "full"}
```

v8 可同时出现：研究扫描 completed / 代理执行 completed / 严格执行 blocked / 晋级门禁 blocked。

### TinyShare 探测通过标准

有限探测，**不能仅凭"接口调用成功"解除阻断**。公司行为数据至少需要：

```text
symbol, ex_date, cash_dividend_per_share, bonus_share_ratio,
capitalization_ratio, split_ratio, rights_issue_ratio, rights_issue_price, source
```

最好还有：`announcement_date, published_at, available_at, implementation_status`

必须验证 8 项：
1. 覆盖 2018—2025
2. 覆盖退市股票和历史股票
3. 除权日期不是只有公告日期
4. 送股/转增/现金分红可区分
5. 不只是当前最新一条记录
6. 与 `adj_factor` 变化日期基本一致
7. 重复下载结果稳定
8. 数据许可允许本项目使用

（缺少 `available_at` 不阻止执行器处理公司行为——执行通常依据实际除权日——但降低 PIT 审计能力）

### manifest 能力设计（分级，不整体标不可用）

```json
{
  "corporate_actions": {
    "explicit_events_available": false,
    "total_return_proxy_available": true,
    "coverage_start": null,
    "coverage_end": null,
    "source": "causal_adj_factor",
    "strict_execution_eligible": false
  }
}
```

```json
{
  "research_ready": true,
  "proxy_execution_ready": true,
  "strict_execution_ready": false,
  "walk_forward_research_ready": true,
  "promotion_eligible": false
}
```

### v8 报告固定声明

> 本实验缺少逐事件公司行为明细。执行结果采用因果复权总回报代理模型，不使用复权因子直接修改持股数量。该结果适合研究策略方向和成本敏感性，不构成严格的原始价格现金流与持股数量回放。严格执行门禁保持阻断，策略不得晋级生产。

并输出：`corporate_action_mode: total_return_proxy` / `strict_execution_status: blocked` / `proxy_execution_status: completed` / `production_enabled: false`

### 必须增加的测试（用户拍板清单）

1. 纯现金分红日不会在代理净值中产生虚假损失
2. 送转导致 adj_factor 变化时，不直接修改真实数量
3. 代理模式不双计（复权收益 + 现金股息）
4. 缺公司行为明细时 strict 引擎明确阻断
5. 同一情况下 proxy 引擎能够完成
6. 报告不能把 proxy 结果标为 `formal_ashare_result=true`
7. 代理执行结果不能通过生产晋级门禁
8. 后续补齐明细后，可对 proxy 与 strict 生成差异报告

### 最终执行顺序（用户拍板）

```text
1. 探测 TinyShare 公司行为能力
2. 可用且覆盖合格：使用 explicit 模式，解除 strict 阻断
3. 不可用或覆盖不合格：使用 total_return_proxy 跑 v8
4. v8 输出代理收益结果
5. strict_execution_status 继续 blocked
6. promotion_eligible 始终为 false
```

### 探测结果（2026-08-05 实测，已执行）

**TinyShare 全部接口不可用**：`TinySharePermissionError: 授权验证失败：积分授权码已过期（guT6ha4m***）`。trade_cal / stock_basic / daily_basic / dividend 四个代表性接口全部失败（同一授权错误），无法验证 8 项覆盖标准（2018-2025 覆盖、退市股票、除权日期、送转/分红区分、历史记录、adj_factor 一致性、重复下载稳定、数据许可）。

**决策生效路径：c 失败 → 受约束的 b**：
- v8 使用 `total_return_proxy` 模式跑代理执行
- `strict_execution_status = blocked`（TinyShare 授权过期，无 explicit 公司行为数据来源）
- `promotion_eligible = false` 始终
- 恢复 TinyShare 授权（续费/换新码）后可重跑探测，若 8 项通过则解除 strict 阻断

注意：v6/v7 时代 TinyShare 曾可用（capabilities.json 记录 2026-08-02 探测成功），当前授权已过期；未来如需 strict 执行需用户提供有效授权码。

### D2 / D3（GPT 计划，默认采纳）

- **D2**：涨跌停历史规则表实现机制 + 核心规则（创业板 2020-08-24、科创板 2019-07-22、北交所 30%、ST 5%、新股首日）；完整制度日期作为后续增强
- **D3**：PR 1-4 合并后，v7 收益解读冻结——v7 文档保留现状（已标注 superseded），不再用 v7 收益作任何论证

---

## 十二、实施顺序（GPT 计划，采纳）

```text
PR 1 公司行为失败关闭 → PR 2 开盘涨跌停与交易规则 → PR 3 next-open 一致性
→ PR 4 FIFO lot ledger → PR 5 证券主数据与 PIT → PR 6 TinyShare 运行环境
→ Schema v2 导入 → v8 重跑
```

每 PR 完成后跑对应测试；全量回归（ruff/mypy/pytest）通过后才进入下一 PR；v8 前做信号不变性检查。

---

## 十三、验收标准（v1.0 保留，微调）

1. `adj_factor` 不再出现在持仓数量计算路径（grep 验证）
2. 开盘成交不再引用收盘涨跌停/一字板字段（conservative 模式除外）
3. VectorBT `execution_proxy == next_bar_open`，无跨日 ffill 成交
4. 卖出交易携带 `realized_pnl/matched_cost/matched_quantity/lot_ids`
5. PIT 校验拒绝 `available_at > D 18:30`
6. TinyShare runtime probe 失败即阻断接口调用
7. v8 signals.parquet SHA-256 == v7（信号不变性）
8. 全量 ruff/mypy/pytest 通过；前端字段变化时 typecheck/build 通过
9. 执行器 metadata 标注 `corporate_action_execution/execution_confidence/open_execution_mode/daily_approximation`
