import { AlertCircle, CheckCircle2, Database, RefreshCw } from "lucide-react";
import { useCallback, useState } from "react";
import { api } from "../api";
import { Metric } from "../components/Metric";
import { StatusBadge } from "../components/StatusBadge";
import { useApi } from "../hooks/useApi";
import type { DataProviderStatus, MarketDataSnapshot } from "../types";

const emptyProvider: DataProviderStatus = {
  provider_code: "freestockdb",
  configured: false,
  reachable: false,
  endpoint: "",
  read_only: true,
  daily_latest_date: null,
  minute_latest_date: null,
  daily_instrument_count: 0,
  minute_instrument_count: 0,
  capabilities: [],
  limitations: [],
  checked_at: null,
  error: null,
};

export function MarketData() {
  const providerLoader = useCallback(() => api.dataProviderStatus(), []);
  const snapshotLoader = useCallback(() => api.marketDataSnapshots(), []);
  const provider = useApi(providerLoader, emptyProvider);
  const snapshots = useApi(snapshotLoader, []);
  const [creating, setCreating] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function createSnapshot() {
    setCreating(true);
    setMessage(null);
    try {
      await api.createMarketDataSnapshot({
        provider_code: "freestockdb",
        lookback_days: 400,
      });
      setMessage("最近 400 日行情快照已生成，可在选股接口中使用 snapshot_id。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "快照生成失败");
    } finally {
      setCreating(false);
    }
  }

  const connected = provider.data.reachable;
  return (
    <section>
      <div className="page-heading">
        <div>
          <span className="eyebrow">MARKET DATA PROVIDER</span>
          <h1>行情数据</h1>
          <p>
            FreeStockDB
            只读提供日线和分钟线；日线先固化为不可变快照，分钟数据仅为候选股按需缓存。
          </p>
        </div>
        <StatusBadge status={connected ? "available" : "not-installed"}>
          {connected ? "服务可达" : "服务不可达"}
        </StatusBadge>
      </div>

      {provider.data.error ? (
        <div className="notice notice-error">
          <AlertCircle size={20} />
          <div>
            <strong>FreeStockDB 当前不可用</strong>
            <span>{provider.data.error}</span>
          </div>
        </div>
      ) : null}
      {message ? (
        <div className="notice notice-info">
          <CheckCircle2 size={20} />
          <span>{message}</span>
        </div>
      ) : null}

      <div className="metrics-grid">
        <Metric
          label="日线最新日期"
          value={provider.data.daily_latest_date ?? "—"}
          note="FreeStockDB"
        />
        <Metric
          label="分钟最新日期"
          value={provider.data.minute_latest_date ?? "—"}
          note="1 分钟原始 K 线"
        />
        <Metric
          label="日线证券数量"
          value={String(provider.data.daily_instrument_count)}
          note="服务横截面"
        />
        <Metric
          label="分钟证券数量"
          value={String(provider.data.minute_instrument_count)}
          note="目标时点覆盖"
        />
      </div>

      <div className="two-column">
        <article className="panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">READ-ONLY ADAPTER</span>
              <h2>FreeStockDB</h2>
            </div>
            <Database size={20} />
          </div>
          <p className="muted-copy">
            端点：{provider.data.endpoint || "未配置"}
          </p>
          <p className="muted-copy">
            复权由本项目重算；成交量固定按 shares 解释；不提供
            CVD、盘口或逐笔成交。
          </p>
          <button
            className="primary-button"
            onClick={createSnapshot}
            disabled={!connected || creating}
          >
            <RefreshCw size={16} />
            {creating ? "正在生成快照…" : "生成最近 400 日快照"}
          </button>
        </article>

        <article className="panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">DATA LIMITATIONS</span>
              <h2>实验边界</h2>
            </div>
          </div>
          <ul className="feature-list">
            {provider.data.limitations.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </article>
      </div>

      <article className="panel" style={{ marginTop: 18 }}>
        <div className="panel-heading">
          <div>
            <span className="eyebrow">IMMUTABLE SNAPSHOTS</span>
            <h2>已生成快照</h2>
          </div>
        </div>
        {snapshots.loading ? <div className="loading-bar" /> : null}
        {!snapshots.loading && snapshots.data.length === 0 ? (
          <p className="muted-copy">暂无行情快照。</p>
        ) : (
          <div className="data-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>快照</th>
                  <th>区间</th>
                  <th>日线覆盖</th>
                  <th>状态</th>
                  <th>Walk-forward</th>
                </tr>
              </thead>
              <tbody>
                {snapshots.data.map((snapshot: MarketDataSnapshot) => (
                  <tr key={snapshot.id}>
                    <td>
                      <code>
                        #{snapshot.id} {snapshot.snapshot_code}
                      </code>
                    </td>
                    <td>
                      {snapshot.start_date} — {snapshot.end_date}
                    </td>
                    <td>{(snapshot.daily_coverage_ratio * 100).toFixed(2)}%</td>
                    <td>
                      <StatusBadge status={snapshot.status}>
                        {snapshot.status}
                      </StatusBadge>
                    </td>
                    <td>
                      {snapshot.walk_forward_eligible ? "eligible" : "blocked"}
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
