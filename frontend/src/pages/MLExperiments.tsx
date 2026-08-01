import { AlertCircle, LockKeyhole } from "lucide-react";
import { useCallback } from "react";
import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { Metric } from "../components/Metric";
import { StatusBadge } from "../components/StatusBadge";
import { useApi } from "../hooks/useApi";

export function MLExperiments() {
  const loader = useCallback(() => api.experiments(), []);
  const { data, loading, error } = useApi(loader, []);

  return (
    <section>
      <div className="page-heading">
        <div>
          <span className="eyebrow">EXPERIMENT ONLY</span>
          <h1>模型实验</h1>
          <p>Qlib 模型与透明规则模型并列比较，默认且持续禁止直接进入生产。</p>
        </div>
        <StatusBadge status="research">production_enabled = false</StatusBadge>
      </div>

      <div className="notice notice-info">
        <LockKeyhole size={20} />
        <div>
          <strong>生产隔离</strong>
          <span>
            预测分数只写入实验结果；不会覆盖历史候选、因子版本或正式策略排名。
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
          title="尚无模型实验"
          description="导出系统因子数据后，可运行最小 LightGBM 排名实验。"
        />
      ) : (
        <div className="experiment-list">
          {data.map((experiment) => (
            <article className="panel experiment-card" key={experiment.id}>
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">{experiment.model_type}</span>
                  <h2>{experiment.experiment_code}</h2>
                </div>
                <StatusBadge status={experiment.status}>
                  {experiment.status}
                </StatusBadge>
              </div>
              <div className="split-timeline">
                <div>
                  <span>TRAIN</span>
                  <strong>
                    {experiment.train_start} — {experiment.train_end}
                  </strong>
                </div>
                <div>
                  <span>VALIDATION</span>
                  <strong>
                    {experiment.validation_start} — {experiment.validation_end}
                  </strong>
                </div>
                <div>
                  <span>TEST</span>
                  <strong>
                    {experiment.test_start} — {experiment.test_end}
                  </strong>
                </div>
              </div>
              <div className="metrics-grid compact">
                <Metric
                  label="模型 Rank IC"
                  value={format(experiment.result?.metrics?.rank_ic)}
                />
                <Metric
                  label="规则 Rank IC"
                  value={format(experiment.result?.metrics?.rule_rank_ic)}
                />
                <Metric
                  label="模型 Top N"
                  value={percent(experiment.result?.metrics?.top_n_return)}
                />
                <Metric
                  label="规则 Top N"
                  value={percent(experiment.result?.metrics?.rule_top_n_return)}
                />
              </div>
              <div className="experiment-meta">
                <span>特征版本</span>
                <code>{experiment.feature_version}</code>
                <span>生产状态</span>
                <strong className="blocked-text">禁止</strong>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

const format = (value?: number) => (value == null ? "—" : value.toFixed(4));
const percent = (value?: number) =>
  value == null ? "—" : `${(value * 100).toFixed(2)}%`;
