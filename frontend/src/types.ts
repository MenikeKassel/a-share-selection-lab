export type RunStatus =
  | "blocked"
  | "running"
  | "succeeded"
  | "failed"
  | "unavailable"
  | "pending"
  | "ready";

export interface EngineRun {
  id: number;
  engine_type: string;
  run_type: string;
  status: RunStatus;
  started_at: string;
  completed_at?: string | null;
  error_message?: string | null;
  result_summary?: Record<string, unknown> | null;
}

export interface EngineStatus {
  engine_type: "alphalens" | "vectorbt" | "rqalpha" | "qlib";
  installed: boolean;
  available: boolean;
  version: string | null;
  required: boolean;
  functions: string[];
  formal_result: boolean;
  license_notice: string;
  installation_extra: string;
  production_enabled: boolean;
  unavailable_reason: string | null;
  last_run: EngineRun | null;
}

export interface FactorAnalysisRow {
  id: number;
  run_id: number | null;
  factor_code: string;
  analysis_engine: string;
  start_date: string;
  end_date: string;
  horizon: number;
  ic: number | null;
  rank_ic: number | null;
  icir: number | null;
  long_short_return: number | null;
  turnover: number | null;
  coverage: number;
  result: {
    ic_std?: number;
    quantile_returns?: number[];
    industry_results?: Record<string, Record<string, number>>;
    metadata?: Record<string, unknown>;
  };
  created_at: string;
}

export interface BacktestRun {
  id: number;
  engine_type: string;
  strategy_code: string;
  start_date: string;
  end_date: string;
  status: RunStatus;
  formal_ashare_result: boolean;
  result?: {
    performance?: Record<string, number>;
    metadata?: Record<string, unknown>;
    execution_failures?: Array<Record<string, unknown>>;
  } | null;
  created_at: string;
}

export interface ModelExperiment {
  id: number;
  experiment_code: string;
  engine: string;
  model_type: string;
  train_start: string;
  train_end: string;
  validation_start: string;
  validation_end: string;
  test_start: string;
  test_end: string;
  feature_version: string;
  label_definition: string;
  status: RunStatus;
  experiment_only: boolean;
  production_enabled: boolean;
  result?: {
    metrics?: {
      rank_ic?: number;
      top_n_return?: number;
      rule_rank_ic?: number;
      rule_top_n_return?: number;
      turnover?: number;
      rule_turnover?: number;
    };
  } | null;
}

export type LifecycleStatus =
  | "experimental"
  | "validated"
  | "production_candidate"
  | "production"
  | "retired";

export interface WalkForwardMetrics {
  theoretical_return?: number | null;
  tradable_return?: number | null;
  benchmark_return?: number | null;
  excess_return?: number | null;
  annualized_return?: number | null;
  max_drawdown?: number | null;
  sharpe?: number | null;
  turnover?: number | null;
  trade_count?: number | null;
  execution_failures?: number | null;
  rank_ic?: number | null;
  ic?: number | null;
  [metric: string]: number | null | undefined;
}

export interface WalkForwardSplit {
  id?: string | number;
  train_start: string;
  train_end: string;
  validation_start: string;
  validation_end: string;
  test_start: string;
  test_end: string;
  selected_parameters?: Record<string, unknown>;
  nearby_parameters?: Array<Record<string, unknown>>;
  train_metrics?: WalkForwardMetrics;
  validation_metrics?: WalkForwardMetrics;
  test_metrics?: WalkForwardMetrics;
  gate_results?: Record<string, boolean>;
  gate_failures?: string[];
  failure_reason?: string;
  status?: string;
}

export interface WalkForwardGate {
  passed?: boolean;
  status?: string;
  passed_checks?: string[];
  failed_checks?: string[];
  checks?: Record<string, boolean>;
  reason?: string;
}

export interface WalkForwardResult {
  splits?: WalkForwardSplit[];
  aggregate_metrics?: WalkForwardMetrics;
  gates?: WalkForwardGate | Record<string, boolean>;
  gate_results?: WalkForwardGate | Record<string, boolean>;
  factor_results?: Array<Record<string, unknown>>;
  signal_audit?: Record<string, unknown>;
  snapshot_audit?: Record<string, unknown>;
  data_source_summary?: Record<string, unknown>;
  error?: string;
  [field: string]: unknown;
}

export interface WalkForwardRun {
  id: number | string;
  strategy_code: string;
  factor_version?: string | null;
  data_snapshot_version?: string | null;
  status: RunStatus;
  lifecycle_status: LifecycleStatus;
  production_enabled: boolean;
  source_engine?: string;
  start_date?: string;
  end_date?: string;
  config?: Record<string, unknown>;
  splits?: WalkForwardSplit[];
  aggregate_metrics?: WalkForwardMetrics;
  gate_results?: WalkForwardGate | Record<string, boolean>;
  gates?: WalkForwardGate | Record<string, boolean>;
  /** Backward-compatible alias used by older experimental artifacts. */
  gate?: WalkForwardGate | Record<string, boolean>;
  result?: WalkForwardResult | null;
  artifact_path?: string | null;
  error_message?: string | null;
  created_at: string;
  completed_at?: string | null;
  snapshot_audit?: Record<string, unknown>;
  data_source_summary?: Record<string, unknown>;
}

export interface WalkForwardRunRequest {
  experiment_code?: string;
  strategy_code: string;
  start_date: string;
  end_date: string;
  snapshot_manifest_path?: string;
  data_path?: string;
  signal_path?: string;
  benchmark_symbol?: string;
  top_n?: number[];
  holding_period?: number[];
  rebalance_frequency?: string[];
  slippage_bps?: number[];
}

export interface SelectionCandidate {
  symbol: string;
  total_score: number | null;
  data_confidence: string;
  minute_confirmation: string;
  eligible: boolean;
  strategies: string[];
}

export interface SelectionSnapshot {
  id: number;
  selection_date: string;
  strategy_code: string;
  strategy_version: string;
  factor_version: string;
  data_snapshot_version: string;
  selection_status: string;
  candidates: SelectionCandidate[];
  created_at: string;
}

export interface DataQualitySnapshot {
  as_of_date: string;
  expected_latest_trade_date: string | null;
  daily_market_max_date: string | null;
  minute_market_max_date: string | null;
  daily_coverage_ratio: number;
  minute_coverage_ratio: number;
  selection_status: string;
  details: {
    message?: string;
    minute_confirmation?: string;
    data_confidence?: string;
  };
}

export interface DataProviderStatus {
  provider_code: string;
  configured: boolean;
  reachable: boolean;
  endpoint: string;
  read_only: boolean;
  daily_latest_date: string | null;
  minute_latest_date: string | null;
  daily_instrument_count: number;
  minute_instrument_count: number;
  capabilities: string[];
  limitations: string[];
  checked_at: string | null;
  error: string | null;
}

export interface MarketDataSnapshot {
  id: number;
  provider_code: string;
  snapshot_code: string;
  status: RunStatus;
  start_date: string;
  end_date: string;
  daily_latest_date: string | null;
  minute_latest_date: string | null;
  daily_row_count: number;
  daily_symbol_count: number;
  daily_coverage_ratio: number;
  minute_coverage_ratio: number;
  manifest_path?: string | null;
  daily_path?: string | null;
  walk_forward_eligible: boolean;
  metadata?: Record<string, unknown> | null;
  error_message?: string | null;
  created_at: string;
  completed_at?: string | null;
}
