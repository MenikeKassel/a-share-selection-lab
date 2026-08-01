import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WalkForward } from "./WalkForward";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("WalkForward", () => {
  it("distinguishes a data-blocked run from a failed promotion gate", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify([
          {
            id: 4,
            strategy_code: "trend_quality_v1",
            status: "blocked",
            lifecycle_status: "experimental",
            production_enabled: false,
            data_snapshot_version: "missing",
            gate_results: { all_passed: false },
            error_message:
              "independent 2018-2025 snapshot manifest was not found; import it before running",
            created_at: "2026-08-01T00:00:00Z",
          },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    render(<WalkForward />);

    expect(await screen.findByText("实验被数据门禁阻断")).toBeInTheDocument();
    expect(
      screen.getByText(
        "independent 2018-2025 snapshot manifest was not found; import it before running",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("门禁未通过")).not.toBeInTheDocument();
  });

  it("renders split metrics, gate status, and production safety boundary", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify([
          {
            id: 7,
            strategy_code: "trend_quality_v1",
            status: "succeeded",
            lifecycle_status: "experimental",
            production_enabled: false,
            factor_version: "transparent_factor_v1",
            data_snapshot_version: "sha256:ashare-2018-2025-v1",
            aggregate_metrics: {
              tradable_return: 0.12,
              excess_return: 0.04,
              max_drawdown: -0.18,
              trade_count: 248,
            },
            gate_results: {
              passed: false,
              passed_checks: ["costs"],
              failed_checks: ["cross_industry"],
              reason: "Sample needs more independent windows",
            },
            splits: [
              {
                train_start: "2018-01-01",
                train_end: "2020-12-31",
                validation_start: "2021-01-01",
                validation_end: "2021-12-31",
                test_start: "2022-01-01",
                test_end: "2022-12-31",
                selected_parameters: {
                  top_n: 10,
                  holding_period: 20,
                  rebalance_frequency: "weekly",
                },
                test_metrics: {
                  tradable_return: 0.08,
                  excess_return: 0.02,
                  sharpe: 0.91,
                  max_drawdown: -0.1,
                  trade_count: 62,
                },
                gate_results: { excess_return: true },
              },
            ],
            created_at: "2026-08-01T00:00:00Z",
          },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    render(<WalkForward />);

    expect(await screen.findByText("trend_quality_v1")).toBeInTheDocument();
    expect(screen.getByText("2018-01-01 – 2020-12-31")).toBeInTheDocument();
    expect(screen.getByText("Top N 10")).toBeInTheDocument();
    expect(screen.getByText("跨行业稳定性")).toBeInTheDocument();
    expect(screen.getAllByText("production_enabled = false")).toHaveLength(2);
    expect(screen.getByText("experimental")).toBeInTheDocument();
  });
});
