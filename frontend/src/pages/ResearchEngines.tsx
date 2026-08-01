import { AlertCircle, CheckCircle2, PackageOpen } from "lucide-react";
import { useCallback } from "react";
import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { StatusBadge } from "../components/StatusBadge";
import { useApi } from "../hooks/useApi";

const names = {
  alphalens: "Alphalens Reloaded",
  vectorbt: "VectorBT",
  rqalpha: "RQAlpha",
  qlib: "Qlib",
};

export function ResearchEngines() {
  const loader = useCallback(() => api.engines(), []);
  const { data, loading, error } = useApi(loader, []);

  return (
    <section>
      <div className="page-heading">
        <div>
          <span className="eyebrow">ADAPTER REGISTRY</span>
          <h1>研究引擎</h1>
          <p>第三方库均经过适配层；缺失或失败不会阻塞正式选股与复盘。</p>
        </div>
      </div>

      {error ? (
        <div className="notice notice-error">
          <AlertCircle size={20} />
          <div>
            <strong>无法读取引擎状态</strong>
            <span>{error}</span>
          </div>
        </div>
      ) : null}

      {loading ? <div className="loading-bar" aria-label="加载中" /> : null}

      {!loading && data.length === 0 ? (
        <EmptyState
          title="尚无引擎状态"
          description="启动 FastAPI 后端后，此处会显示已安装版本与最近任务。"
        />
      ) : (
        <div className="engine-grid">
          {data.map((engine) => (
            <article className="engine-card" key={engine.engine_type}>
              <div className="engine-card-top">
                <div className={`engine-symbol engine-${engine.engine_type}`}>
                  {engine.engine_type.slice(0, 2).toUpperCase()}
                </div>
                <StatusBadge
                  status={engine.available ? "available" : "not-installed"}
                >
                  {engine.available ? "可用" : "未安装"}
                </StatusBadge>
              </div>
              <div>
                <span className="eyebrow">{engine.engine_type}</span>
                <h2>{names[engine.engine_type]}</h2>
                <span className="version">
                  {engine.version
                    ? `v${engine.version}`
                    : "optional dependency"}
                </span>
              </div>
              <ul className="feature-list">
                {engine.functions.map((feature) => (
                  <li key={feature}>
                    <CheckCircle2 size={14} />
                    {feature}
                  </li>
                ))}
              </ul>
              <div className="engine-classification">
                <span>结果分类</span>
                <strong>
                  {engine.formal_result ? "正式结果" : "研究 / 交叉验证"}
                </strong>
              </div>
              <div className="license-note">
                <PackageOpen size={16} />
                <span>{engine.license_notice}</span>
              </div>
              <div className="last-run">
                <span>最近任务</span>
                <strong>
                  {engine.last_run
                    ? `#${engine.last_run.id} · ${engine.last_run.status}`
                    : "暂无"}
                </strong>
              </div>
              {!engine.available && engine.unavailable_reason ? (
                <code>{engine.unavailable_reason}</code>
              ) : null}
            </article>
          ))}
        </div>
      )}

      <div className="notice notice-info">
        <AlertCircle size={20} />
        <div>
          <strong>正式结果边界</strong>
          <span>
            VectorBT 与 Qlib 结果始终为 experimental；RQAlpha
            只作交叉验证。正式策略必须通过自研 A 股执行模型。
          </span>
        </div>
      </div>
    </section>
  );
}
