# Domain Context

## Product

**A-Share Selection Lab** is an A-share daily selection, transparent-factor
research, and automatic review system. It does not place orders and never
claims that a stock is certain to rise or hit its price limit.

## Ubiquitous language

- **formal selection**: a versioned, immutable candidate snapshot produced only
  after the daily-data freshness gate passes.
- **research result**: an Alphalens, VectorBT, RQAlpha, or Qlib artifact. It is
  never promoted to production automatically.
- **formal backtest**: a result produced by the in-house A-share daily execution
  engine with T+1, lot, suspension, price-limit, fee, tax, and slippage rules.
- **minute confirmation**: optional structure confirmation for symbols with a
  complete 1-minute session. Missing minute data is `unavailable`, never zero.
- **Wyckoff candidate**: a heuristic candidate with evidence and alternatives;
  it is not a statement about an operator, institution, or hidden order flow.
- **available_at**: the earliest timestamp at which a datum may legally enter a
  historical calculation.
- **engine run**: an auditable record of one optional external-engine attempt,
  including unavailable and failed attempts.

## Public seams under test

1. REST API under `/api/v1`.
2. `FactorAnalysisEngine` and `BacktestEngine` protocols.
3. Data freshness gate and factor pipeline input/output.
4. In-house A-share execution engine result.
5. Adapter converters at their public functions.
6. Frontend routes and visible result classification.

