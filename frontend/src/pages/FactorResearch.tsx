import { AlertCircle, Layers3 } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { Metric } from "../components/Metric";
import { useApi } from "../hooks/useApi";

const percent = (value: number | null | undefined) =>
  value == null ? "—" : `${(value * 100).toFixed(2)}%`;
const decimal = (value: number | null | undefined) =>
  value == null ? "—" : value.toFixed(4);

export function FactorResearch() {
  const loader = useCallback(() => api.factorAnalysis(), []);
  const { data, loading, error } = useApi(loader, []);
  const [engine, setEngine] = useState<"native" | "alphalens">("alphalens");
  const filtered = useMemo(
    () => data.filter((row) => row.analysis_engine === engine),
    [data, engine],
  );
  const active = filtered[0];
  const quantiles = active?.result.quantile_returns ?? [];
  const industries = Object.entries(active?.result.industry_results ?? {});

  return (
    <section>
      <div className="page-heading">
        <div>
          <span className="eyebrow">FACTOR DIAGNOSTICS</span>
          <h1>因子研究</h1>
          <p>远期收益只作为标签参与验证，绝不回流到因子值或历史正式排名。</p>
        </div>
        <div className="segmented-control" role="tablist">
          <button
            className={engine === "native" ? "active" : ""}
            onClick={() => setEngine("native")}
            type="button"
          >
            自研计算
          </button>
          <button
            className={engine === "alphalens" ? "active" : ""}
            onClick={() => setEngine("alphalens")}
            type="button"
          >
            Alphalens
          </button>
        </div>
      </div>

      {error ? (
        <div className="notice notice-error">
          <AlertCircle size={20} />
          <span>{error}</span>
        </div>
      ) : null}
      {loading ? <div className="loading-bar" /> : null}
      {!loading && !active ? (
        <EmptyState
          title={`暂无${engine === "native" ? "自研" : " Alphalens"}结果`}
          description="运行一次因子分析后，IC、分组收益、衰减和换手会显示在这里。"
        />
      ) : null}

      {active ? (
        <>
          <div className="factor-title-row">
            <div>
              <span className="eyebrow">FACTOR CODE</span>
              <h2>{active.factor_code}</h2>
            </div>
            <span className="date-range">
              {active.start_date} — {active.end_date} · {active.horizon}D
            </span>
          </div>
          <div className="metrics-grid">
            <Metric label="IC" value={decimal(active.ic)} />
            <Metric label="Rank IC" value={decimal(active.rank_ic)} />
            <Metric label="ICIR" value={decimal(active.icir)} />
            <Metric label="覆盖率" value={percent(active.coverage)} />
          </div>
          <div className="research-grid">
            <article className="panel">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">QUANTILE RETURNS</span>
                  <h2>分组收益</h2>
                </div>
                <Layers3 size={20} />
              </div>
              <div className="bar-chart">
                {quantiles.map((value, index) => {
                  const max = Math.max(
                    ...quantiles.map((item) => Math.abs(item)),
                    0.001,
                  );
                  return (
                    <div className="bar-column" key={`${index}-${value}`}>
                      <span>{percent(value)}</span>
                      <div
                        className={value >= 0 ? "bar positive" : "bar negative"}
                        style={{
                          height: `${Math.max(Math.abs(value / max) * 120, 4)}px`,
                        }}
                      />
                      <strong>Q{index + 1}</strong>
                    </div>
                  );
                })}
              </div>
              <div className="chart-footer">
                <span>多空分组差</span>
                <strong>{percent(active.long_short_return)}</strong>
              </div>
            </article>
            <article className="panel">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">DECAY & TURNOVER</span>
                  <h2>衰减与换手</h2>
                </div>
              </div>
              <div className="decay-list">
                {filtered.slice(0, 5).map((row) => (
                  <div key={row.id}>
                    <span>{row.horizon}D</span>
                    <div>
                      <i
                        style={{
                          width: `${Math.min(Math.abs(row.rank_ic ?? 0) * 500, 100)}%`,
                        }}
                      />
                    </div>
                    <strong>{decimal(row.rank_ic)}</strong>
                    <small>换手 {percent(row.turnover)}</small>
                  </div>
                ))}
              </div>
            </article>
            <article className="panel panel-full">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">INDUSTRY BREAKDOWN</span>
                  <h2>分行业表现</h2>
                </div>
              </div>
              {industries.length === 0 ? (
                <p className="muted-copy">当前结果未提供历史行业分组。</p>
              ) : (
                <div className="data-table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>行业</th>
                        {Object.keys(industries[0][1]).map((period) => (
                          <th key={period}>{period} Rank IC</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {industries.map(([industry, values]) => (
                        <tr key={industry}>
                          <td>
                            <strong>{industry}</strong>
                          </td>
                          {Object.entries(values).map(([period, value]) => (
                            <td key={period}>{decimal(value)}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </article>
          </div>
        </>
      ) : null}
    </section>
  );
}
