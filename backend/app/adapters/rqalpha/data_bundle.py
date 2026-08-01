from __future__ import annotations

from pathlib import Path

import pandas as pd


def export_rqalpha_signals(signals: pd.DataFrame, output_path: Path) -> Path:
    required = {"signal_date", "symbol", "target_weight"}
    if missing := required.difference(signals.columns):
        raise ValueError(f"RQAlpha signals are missing columns: {sorted(missing)}")
    output = signals.copy()
    output["signal_date"] = pd.to_datetime(output["signal_date"]).dt.strftime("%Y-%m-%d")
    output = output.sort_values(["signal_date", "symbol"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(output_path, index=False)
    return output_path


def strategy_template(signal_path: Path) -> str:
    normalized = signal_path.resolve().as_posix()
    return f'''"""Generated RQAlpha validation strategy. Do not use for live orders."""
import pandas as pd
from rqalpha.api import order_target_percent


def init(context):
    context.signals = pd.read_csv(r"{normalized}")
    context.signals["signal_date"] = pd.to_datetime(context.signals["signal_date"]).dt.date


def before_trading(context):
    # Signals are produced after the prior close and acted on at the next event.
    today = context.now.date()
    current = context.signals.loc[context.signals["signal_date"] < today]
    if current.empty:
        context.targets = {{}}
        return
    latest = current["signal_date"].max()
    rows = current.loc[current["signal_date"] == latest]
    context.targets = dict(zip(rows["symbol"], rows["target_weight"]))


def handle_bar(context, bar_dict):
    for instrument in list(context.portfolio.positions.keys()):
        if instrument not in context.targets:
            order_target_percent(instrument, 0)
    for instrument, weight in context.targets.items():
        order_target_percent(instrument, float(weight))
'''
