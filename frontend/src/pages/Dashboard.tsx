import {
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  Clock3,
  Database,
} from "lucide-react";
import { useCallback } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Metric } from "../components/Metric";
import { StatusBadge } from "../components/StatusBadge";
import { useApi } from "../hooks/useApi";
import type { DataQualitySnapshot, SelectionSnapshot } from "../types";

const emptyQuality: DataQualitySnapshot = {
  as_of_date: "",
  expected_latest_trade_date: null,
  daily_market_max_date: null,
  minute_market_max_date: null,
  daily_coverage_ratio: 0,
  minute_coverage_ratio: 0,
  selection_status: "not_run",
  details: {},
};

const emptySelection: SelectionSnapshot = {
  id: 0,
  selection_date: "",
  strategy_code: "",
  strategy_version: "",
  factor_version: "",
  data_snapshot_version: "",
  selection_status: "not_run",
  candidates: [],
  created_at: "",
};

const API_DOCS_URL =
  import.meta.env.VITE_API_DOCS_URL ?? "http://127.0.0.1:8000/docs";

export function Dashboard() {
  const qualityLoader = useCallback(() => api.latestDataQuality(), []);
  const selectionLoader = useCallback(
    () => api.latestSelection("trend_quality_v1"),
    [],
  );
  const quality = useApi(qualityLoader, emptyQuality);
  const selection = useApi(selectionLoader, emptySelection);
  const ready = quality.data.selection_status === "ready";

  return (
    <section>
      <div className="page-heading">
        <div>
          <span className="eyebrow">DAILY LAB</span>
          <h1>每日候选与数据闸门</h1>
          <p>日线负责全市场筛选；分钟数据只为有覆盖的标的提供结构确认。</p>
        </div>
        <a
          className="primary-button"
          href={`${API_DOCS_URL}#/selection-and-review/run_selection_api_v1_selections_run_post`}
        >
          运行正式选股
          <ArrowUpRight size={16} />
        </a>
      </div>

      <div className={`notice ${ready ? "notice-success" : "notice-warning"}`}>
        {ready ? <CheckCircle2 size={20} /> : <AlertTriangle size={20} />}
        <div>
          <strong>
            {ready ? "数据闸门已通过" : "当前没有可用的最新正式快照"}
          </strong>
          <span>
            {quality.data.details.message ??
              quality.error ??
              "运行选股后，这里会显示真实新鲜度和覆盖率。"}
          </span>
        </div>
      </div>

      <div className="metrics-grid">
        <Metric
          label="预期交易日"
          value={quality.data.expected_latest_trade_date ?? "—"}
          note="expected_latest_trade_date"
        />
        <Metric
          label="日线最新日期"
          value={quality.data.daily_market_max_date ?? "—"}
          note="daily_market_max_date"
        />
        <Metric
          label="日线覆盖率"
          value={
            quality.data.as_of_date
              ? formatPercent(quality.data.daily_coverage_ratio)
              : "—"
          }
          note="质量闸门 ≥ 95%"
        />
        <Metric
          label="分钟覆盖率"
          value={
            quality.data.as_of_date
              ? formatPercent(quality.data.minute_coverage_ratio)
              : "—"
          }
          note="缺失时降级，不记 0 分"
        />
      </div>

      {selection.data.id ? (
        <article className="panel candidate-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">IMMUTABLE SNAPSHOT</span>
              <h2>
                {selection.data.strategy_code} · {selection.data.selection_date}
              </h2>
            </div>
            <code>{selection.data.data_snapshot_version.slice(0, 22)}…</code>
          </div>
          <div className="data-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>代码</th>
                  <th>综合得分</th>
                  <th>分钟确认</th>
                  <th>置信度</th>
                  <th>策略池</th>
                </tr>
              </thead>
              <tbody>
                {selection.data.candidates.slice(0, 12).map((candidate) => (
                  <tr key={candidate.symbol}>
                    <td>
                      <strong>{candidate.symbol}</strong>
                    </td>
                    <td>
                      {candidate.total_score == null
                        ? "硬门槛剔除"
                        : candidate.total_score.toFixed(2)}
                    </td>
                    <td>{candidate.minute_confirmation}</td>
                    <td>
                      <StatusBadge
                        status={
                          candidate.data_confidence === "normal"
                            ? "available"
                            : "pending"
                        }
                      >
                        {candidate.data_confidence}
                      </StatusBadge>
                    </td>
                    <td>{candidate.strategies.join(" / ") || "结构观察"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </article>
      ) : null}

      <div className="two-column">
        <article className="panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">SELECTION FLOW</span>
              <h2>正式选股链路</h2>
            </div>
            <Database size={20} />
          </div>
          <ol className="flow-list">
            {[
              "数据新鲜度与完整性闸门",
              "历史股票池与基础因子",
              "截面标准化与前 200 名",
              "PA / 威科夫候选确认",
              "可用标的分钟结构确认",
              "多策略不可覆盖快照",
            ].map((label, index) => (
              <li key={label}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                {label}
              </li>
            ))}
          </ol>
        </article>

        <article className="panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">REVIEW WINDOWS</span>
              <h2>自动复盘窗口</h2>
            </div>
            <Clock3 size={20} />
          </div>
          <div className="horizon-row">
            {[1, 3, 5, 10, 20, 60].map((day) => (
              <span key={day}>{day}日</span>
            ))}
          </div>
          <p className="muted-copy">
            同时计算理论收益与可成交收益，记录涨停无法买入、跌停无法卖出和停牌案例。
          </p>
          <Link className="text-link" to="/backtests">
            查看正式执行模型 <ArrowUpRight size={15} />
          </Link>
        </article>
      </div>

      <div className="microstructure-strip">
        {[
          "cvd",
          "bid_ask_delta",
          "footprint",
          "absorption",
          "iceberg_order",
          "level2_orderbook",
          "option_wall",
        ].map((item) => (
          <span key={item}>
            {item} <strong>unavailable</strong>
          </span>
        ))}
      </div>
    </section>
  );
}

const formatPercent = (value: number) => `${(value * 100).toFixed(2)}%`;
