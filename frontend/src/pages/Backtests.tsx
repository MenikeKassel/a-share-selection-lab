import { AlertTriangle, Gauge, ShieldCheck, Zap } from "lucide-react";
import { useCallback } from "react";
import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { StatusBadge } from "../components/StatusBadge";
import { useApi } from "../hooks/useApi";

const classifications = [
  {
    code: "formal",
    title: "正式 A 股回测",
    subtitle: "自研日频执行引擎",
    icon: ShieldCheck,
    description:
      "次日开盘、T+1、整手、涨跌停、停牌、手续费、印花税和滑点均进入正式结果。",
    tone: "formal",
  },
  {
    code: "vectorbt",
    title: "快速参数研究",
    subtitle: "VectorBT",
    icon: Zap,
    description:
      "用于 top_n、持有期、调仓频率与成本敏感性扫描；结果必须再过正式引擎。",
    tone: "research",
  },
  {
    code: "rqalpha",
    title: "交叉验证",
    subtitle: "RQAlpha",
    icon: Gauge,
    description:
      "对照订单、成交、账户、现金与费用变化；差异报告不替代正式结果。",
    tone: "validation",
  },
];

export function Backtests() {
  const loader = useCallback(() => api.formalBacktests(), []);
  const { data, loading, error } = useApi(loader, []);

  return (
    <section>
      <div className="page-heading">
        <div>
          <span className="eyebrow">EXECUTION LAB</span>
          <h1>回测实验</h1>
          <p>三类结果独立展示，研究引擎不会自动升级为正式策略。</p>
        </div>
      </div>

      <div className="backtest-classifications">
        {classifications.map(
          ({ code, title, subtitle, icon: Icon, description, tone }) => (
            <article className={`classification-card ${tone}`} key={code}>
              <Icon size={22} />
              <span className="eyebrow">{subtitle}</span>
              <h2>{title}</h2>
              <p>{description}</p>
              <StatusBadge status={tone === "formal" ? "formal" : "research"}>
                {tone === "formal" ? "正式结果来源" : "非正式结果"}
              </StatusBadge>
            </article>
          ),
        )}
      </div>

      <div className="notice notice-warning">
        <AlertTriangle size={20} />
        <div>
          <strong>理论收益 ≠ 可成交收益</strong>
          <span>默认买入价不是信号日收盘价；无法成交案例单独记录。</span>
        </div>
      </div>

      <article className="panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">FORMAL RUNS</span>
            <h2>正式 A 股回测记录</h2>
          </div>
        </div>
        {error ? <p className="error-copy">{error}</p> : null}
        {loading ? <div className="loading-bar" /> : null}
        {!loading && data.length === 0 ? (
          <EmptyState
            title="尚无正式回测"
            description="通过 POST /api/v1/backtests 运行自研执行模型。"
          />
        ) : (
          <div className="data-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>策略</th>
                  <th>区间</th>
                  <th>理论收益</th>
                  <th>可成交收益</th>
                  <th>最大回撤</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {data.map((run) => (
                  <tr key={run.id}>
                    <td>
                      <strong>{run.strategy_code}</strong>
                      <small>{run.engine_type}</small>
                    </td>
                    <td>
                      {run.start_date} — {run.end_date}
                    </td>
                    <td>
                      {formatPercent(
                        run.result?.performance?.theoretical_return,
                      )}
                    </td>
                    <td>
                      {formatPercent(run.result?.performance?.tradable_return)}
                    </td>
                    <td>
                      {formatPercent(run.result?.performance?.max_drawdown)}
                    </td>
                    <td>
                      <StatusBadge status={run.status}>
                        {run.status}
                      </StatusBadge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </article>
    </section>
  );
}

function formatPercent(value: number | undefined) {
  return value == null ? "—" : `${(value * 100).toFixed(2)}%`;
}
