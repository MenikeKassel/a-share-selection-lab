import {
  AlertCircle,
  CheckCircle2,
  GitBranch,
  LockKeyhole,
} from "lucide-react";
import { useCallback } from "react";
import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { Metric } from "../components/Metric";
import { StatusBadge } from "../components/StatusBadge";
import { useApi } from "../hooks/useApi";
import type {
  WalkForwardGate,
  WalkForwardMetrics,
  WalkForwardRun,
  WalkForwardSplit,
} from "../types";

export function WalkForward() {
  const loader = useCallback(() => api.walkForwardRuns(), []);
  const { data, loading, error } = useApi(loader, []);

  return (
    <section>
      <div className="page-heading">
        <div>
          <span className="eyebrow">OUT-OF-SAMPLE VALIDATION</span>
          <h1>Walk-forward 验证</h1>
          <p>
            训练、验证和测试区间严格按时间滚动；测试期参数冻结，结果不会自动进入生产。
          </p>
        </div>
        <StatusBadge status="research">production_enabled = false</StatusBadge>
      </div>

      <div className="notice notice-info">
        <LockKeyhole size={20} />
        <div>
          <strong>实验边界</strong>
          <span>
            Walk-forward
            只验证样本外稳健性。即使所有门禁通过，也必须经过人工审批和自研 A
            股执行器复核。
          </span>
        </div>
      </div>

      {error ? (
        <div className="notice notice-error">
          <AlertCircle size={20} />
          <span>{error}</span>
        </div>
      ) : null}
      {loading ? <div className="loading-bar" /> : null}
      {!loading && data.length === 0 ? (
        <EmptyState
          title="暂无 Walk-forward 实验"
          description="运行 POST /api/v1/walk-forward-experiments 后，分窗口结果会显示在这里。"
        />
      ) : (
        <div className="experiment-list">
          {data.map((run) => (
            <WalkForwardRunCard key={run.id} run={run} />
          ))}
        </div>
      )}
    </section>
  );
}

function WalkForwardRunCard({ run }: { run: WalkForwardRun }) {
  const result = run.result;
  const splits = run.splits ?? result?.splits ?? [];
  const gate = normalizeGate(
    run.gate_results ??
      run.gates ??
      run.gate ??
      result?.gate_results ??
      result?.gates,
  );
  const aggregate = run.aggregate_metrics ?? result?.aggregate_metrics ?? {};
  const isDataBlocked = run.status === "blocked";
  const gatePassed = gate.passed ?? inferGatePassed(gate);
  const badgeStatus = isDataBlocked
    ? "blocked"
    : gatePassed
      ? run.lifecycle_status
      : run.status === "failed"
        ? "failed"
        : "experimental";
  const gateReason = isDataBlocked
    ? (run.error_message ?? result?.error)
    : gate.reason;

  return (
    <article className="panel walk-forward-card">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">{run.source_engine ?? "SELF ENGINE"}</span>
          <h2>{run.strategy_code}</h2>
          <small className="walk-forward-run-id">Run #{run.id}</small>
        </div>
        <StatusBadge status={badgeStatus}>
          {isDataBlocked ? "blocked" : run.lifecycle_status}
        </StatusBadge>
      </div>

      <div className="metrics-grid compact">
        <Metric
          label="样本外可成交收益"
          value={percent(aggregate.tradable_return)}
          tone={tone(aggregate.tradable_return)}
        />
        <Metric
          label="样本外超额收益"
          value={percent(aggregate.excess_return)}
          tone={tone(aggregate.excess_return)}
        />
        <Metric
          label="最大回撤"
          value={percent(aggregate.max_drawdown)}
          tone={aggregate.max_drawdown != null ? "negative" : "default"}
        />
        <Metric label="平仓交易数" value={integer(aggregate.trade_count)} />
      </div>

      <div className="walk-forward-gate-row">
        <div>
          <span className="eyebrow">
            {isDataBlocked ? "DATA READINESS GATE" : "PROMOTION GATE"}
          </span>
          <strong className={gatePassed ? "gate-passed" : "gate-blocked"}>
            {isDataBlocked
              ? "实验被数据门禁阻断"
              : gatePassed
                ? "门禁通过"
                : "门禁未通过"}
          </strong>
          {gateReason ? <small>{gateReason}</small> : null}
        </div>
        <div className="walk-forward-safety">
          <span>生产状态</span>
          <strong>production_enabled = {String(run.production_enabled)}</strong>
        </div>
      </div>

      {!isDataBlocked ? (
        <div className="walk-forward-checks">
          {gate.passed_checks?.map((check) => (
            <span className="walk-forward-check passed" key={`pass-${check}`}>
              <CheckCircle2 size={13} /> {checkLabel(check)}
            </span>
          ))}
          {gate.failed_checks?.map((check) => (
            <span className="walk-forward-check failed" key={`fail-${check}`}>
              <AlertCircle size={13} /> {checkLabel(check)}
            </span>
          ))}
        </div>
      ) : null}

      <div className="walk-forward-splits">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">ROLLING WINDOWS</span>
            <h3>分窗口样本外结果</h3>
          </div>
          <GitBranch size={19} />
        </div>
        {splits.map((split, index) => (
          <SplitRow
            key={split.id ?? `${split.test_start}-${index}`}
            split={split}
            index={index}
          />
        ))}
      </div>

      <div className="experiment-meta">
        <span>因子版本</span>
        <code>{run.factor_version ?? "—"}</code>
        <span>数据快照</span>
        <code>{run.data_snapshot_version ?? "—"}</code>
      </div>
    </article>
  );
}

function SplitRow({
  split,
  index,
}: {
  split: WalkForwardSplit;
  index: number;
}) {
  const metrics = split.test_metrics ?? {};
  const parameters = split.selected_parameters ?? {};
  return (
    <div className="walk-forward-split">
      <div className="walk-forward-split-index">{index + 1}</div>
      <div className="walk-forward-periods">
        <span>TRAIN</span>
        <strong>
          {split.train_start} – {split.train_end}
        </strong>
        <span>VALIDATION</span>
        <strong>
          {split.validation_start} – {split.validation_end}
        </strong>
        <span>TEST</span>
        <strong>
          {split.test_start} – {split.test_end}
        </strong>
      </div>
      <div className="walk-forward-parameters">
        <span className="eyebrow">SELECTED PARAMETERS</span>
        <div>
          {parameterEntries(parameters).map(([key, value]) => (
            <code key={key}>
              {parameterLabel(key)} {String(value)}
            </code>
          ))}
        </div>
      </div>
      <div className="walk-forward-split-metrics">
        <span>Test 超额</span>
        <strong
          className={
            tone(metrics.excess_return) === "positive"
              ? "gate-passed"
              : undefined
          }
        >
          {percent(metrics.excess_return)}
        </strong>
        <span>Sharpe</span>
        <strong>{decimal(metrics.sharpe)}</strong>
        <span>回撤</span>
        <strong>{percent(metrics.max_drawdown)}</strong>
      </div>
    </div>
  );
}

function normalizeGate(gate: WalkForwardRun["gate_results"]): WalkForwardGate {
  if (!gate) return {};
  if ("passed" in gate || "passed_checks" in gate || "failed_checks" in gate) {
    return gate as WalkForwardGate;
  }
  const checks = gate as Record<string, boolean>;
  const passed_checks = Object.entries(checks)
    .filter(([, passed]) => passed)
    .map(([name]) => name);
  const failed_checks = Object.entries(checks)
    .filter(([, passed]) => !passed)
    .map(([name]) => name);
  return { checks, passed_checks, failed_checks };
}

function inferGatePassed(gate: WalkForwardGate) {
  if (gate.failed_checks?.length) return false;
  if (gate.passed_checks?.length) return true;
  if (gate.checks) return Object.values(gate.checks).every(Boolean);
  return false;
}

function parameterEntries(parameters: Record<string, unknown>) {
  return Object.entries(parameters).filter(([, value]) => value != null);
}

function parameterLabel(key: string) {
  const labels: Record<string, string> = {
    top_n: "Top N",
    holding_period: "持有",
    rebalance_frequency: "调仓",
    slippage_bps: "滑点",
  };
  return labels[key] ?? key;
}

function checkLabel(check: string) {
  const labels: Record<string, string> = {
    cross_industry: "跨行业稳定性",
    stable_ic_direction: "IC 方向稳定",
    survives_costs: "成本后有效",
    acceptable_drawdown: "回撤可接受",
    consistent_across_periods: "跨周期一致",
    stable_nearby_parameters: "邻近参数稳定",
    not_driven_by_extremes: "不依赖极端个股",
    out_of_sample_healthy: "样本外健康",
  };
  return labels[check] ?? check;
}

function tone(
  value: number | null | undefined,
): "default" | "positive" | "negative" {
  return value == null ? "default" : value >= 0 ? "positive" : "negative";
}

function percent(value: number | null | undefined) {
  return value == null ? "—" : `${(value * 100).toFixed(2)}%`;
}

function decimal(value: number | null | undefined) {
  return value == null ? "—" : value.toFixed(2);
}

function integer(value: number | null | undefined) {
  return value == null ? "—" : Math.round(value).toLocaleString();
}
