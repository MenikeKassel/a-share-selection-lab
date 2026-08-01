import type {
  BacktestRun,
  DataQualitySnapshot,
  EngineStatus,
  FactorAnalysisRow,
  ModelExperiment,
  SelectionSnapshot,
  WalkForwardRun,
  WalkForwardRunRequest,
} from "./types";

const API_PREFIX = import.meta.env.VITE_API_PREFIX ?? "/api/v1";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`API ${response.status}: ${response.statusText}`);
  }
  return (await response.json()) as T;
}

async function postJson<T>(path: string, payload: unknown): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`API ${response.status}: ${response.statusText}`);
  }
  return (await response.json()) as T;
}

export const api = {
  engines: () => getJson<EngineStatus[]>("/engines/status"),
  factorAnalysis: () => getJson<FactorAnalysisRow[]>("/factor-analysis"),
  formalBacktests: () => getJson<BacktestRun[]>("/backtests"),
  experiments: () => getJson<ModelExperiment[]>("/ml-experiments"),
  latestSelection: (strategyCode?: string) =>
    getJson<SelectionSnapshot>(
      `/selections/latest${strategyCode ? `?strategy_code=${encodeURIComponent(strategyCode)}` : ""}`,
    ),
  latestDataQuality: () => getJson<DataQualitySnapshot>("/data-quality/latest"),
  walkForwardRuns: () => getJson<WalkForwardRun[]>("/walk-forward-experiments"),
  walkForwardRun: (runId: number | string) =>
    getJson<WalkForwardRun>(
      `/walk-forward-experiments/${encodeURIComponent(String(runId))}`,
    ),
  createWalkForwardRun: (payload: WalkForwardRunRequest) =>
    postJson<WalkForwardRun>("/walk-forward-experiments", payload),
};
