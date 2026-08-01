import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("research result boundaries", () => {
  it("renders the real stale-data gate message without inventing minute scores", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/data-quality/latest")) {
        return new Response(
          JSON.stringify({
            as_of_date: "2026-07-30",
            expected_latest_trade_date: "2026-07-30",
            daily_market_max_date: "2026-07-29",
            minute_market_max_date: null,
            daily_coverage_ratio: 0.98,
            minute_coverage_ratio: 0,
            selection_status: "blocked_stale_daily_data",
            details: {
              message:
                "行情尚未更新至最新交易日。当前最新有效选股日期：2026-07-29。",
              minute_confirmation: "unavailable",
              data_confidence: "blocked",
            },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(
        JSON.stringify({
          id: 0,
          selection_date: "",
          strategy_code: "",
          strategy_version: "",
          factor_version: "",
          data_snapshot_version: "",
          selection_status: "not_run",
          candidates: [],
          created_at: "",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });

    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>,
    );

    expect(
      await screen.findByText(
        "行情尚未更新至最新交易日。当前最新有效选股日期：2026-07-29。",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("0.00%")).toBeInTheDocument();
    expect(screen.getByText(/cvd/)).toHaveTextContent("unavailable");
  });

  it("renders engine availability and non-formal classification", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify([
          {
            engine_type: "vectorbt",
            installed: true,
            available: true,
            version: "0.28.5",
            required: false,
            functions: ["快速参数扫描"],
            formal_result: false,
            license_notice: "Apache-2.0 with Commons Clause license",
            installation_extra: "fast-backtest",
            production_enabled: false,
            unavailable_reason: null,
            last_run: null,
          },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    render(
      <MemoryRouter initialEntries={["/research-engines"]}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByText("VectorBT")).toBeInTheDocument();
    expect(screen.getByText("研究 / 交叉验证")).toBeInTheDocument();
    expect(screen.getByText(/Commons Clause/)).toBeInTheDocument();
  });

  it("loads the latest formal trend snapshot on the dashboard", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/data-quality/latest")) {
        return new Response(
          JSON.stringify({
            as_of_date: "2026-07-30",
            expected_latest_trade_date: "2026-07-30",
            daily_market_max_date: "2026-07-30",
            minute_market_max_date: "2026-03-31",
            daily_coverage_ratio: 1,
            minute_coverage_ratio: 0.1,
            selection_status: "ready",
            details: { message: "数据闸门已通过" },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      expect(url).toContain(
        "/selections/latest?strategy_code=trend_quality_v1",
      );
      return new Response(
        JSON.stringify({
          id: 1,
          selection_date: "2026-03-31",
          strategy_code: "trend_quality_v1",
          strategy_version: "1.0.0",
          factor_version: "transparent_factor_v1",
          data_snapshot_version: "sha256:test",
          selection_status: "ready",
          candidates: [
            {
              symbol: "600000.SH",
              total_score: 81.2,
              data_confidence: "normal",
              minute_confirmation: "available",
              eligible: true,
              strategies: ["trend_quality_v1"],
            },
          ],
          created_at: "2026-07-30T00:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });

    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByText("600000.SH")).toBeInTheDocument();
  });

  it("keeps the three backtest classes visually separate", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    render(
      <MemoryRouter initialEntries={["/backtests"]}>
        <App />
      </MemoryRouter>,
    );

    expect(screen.getByText("正式 A 股回测")).toBeInTheDocument();
    expect(screen.getByText("快速参数研究")).toBeInTheDocument();
    expect(screen.getByText("交叉验证")).toBeInTheDocument();
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
  });

  it("shows factor industry performance separately", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify([
          {
            id: 1,
            run_id: 1,
            factor_code: "return_20d",
            analysis_engine: "alphalens",
            start_date: "2024-01-02",
            end_date: "2025-12-31",
            horizon: 5,
            ic: 0.01,
            rank_ic: 0.02,
            icir: 0.1,
            long_short_return: 0.03,
            turnover: 0.2,
            coverage: 1,
            result: {
              quantile_returns: [0.01, 0.02],
              industry_results: { 电子: { "5D": 0.04 } },
            },
            created_at: "2026-01-01",
          },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    render(
      <MemoryRouter initialEntries={["/factor-research"]}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByText("分行业表现")).toBeInTheDocument();
    expect(screen.getByText("电子")).toBeInTheDocument();
  });

  it("shows model production as disabled", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    render(
      <MemoryRouter initialEntries={["/ml-experiments"]}>
        <App />
      </MemoryRouter>,
    );

    expect(screen.getByText("production_enabled = false")).toBeInTheDocument();
  });
});
