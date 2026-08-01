from __future__ import annotations

import subprocess
import sys
from importlib import metadata
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import pandas as pd

from app.adapters import OptionalEngineUnavailableError
from app.adapters.rqalpha.data_bundle import export_rqalpha_signals, strategy_template


class RQAlphaValidationAdapter:
    engine_code = "rqalpha"

    def status(self) -> dict[str, Any]:
        available = find_spec("rqalpha") is not None
        try:
            version = metadata.version("rqalpha")
        except metadata.PackageNotFoundError:
            version = None
        return {
            "engine_type": self.engine_code,
            "available": available,
            "installed": version is not None,
            "version": version,
            "installation_hint": "uv sync --extra rqalpha-validation",
            "formal_result": False,
        }

    def prepare_validation(self, signals: pd.DataFrame, artifact_dir: Path) -> dict[str, str]:
        signal_path = export_rqalpha_signals(signals, artifact_dir / "signals.csv")
        strategy_path = artifact_dir / "strategy.py"
        strategy_path.write_text(strategy_template(signal_path), encoding="utf-8")
        return {
            "signal_path": str(signal_path),
            "strategy_path": str(strategy_path),
        }

    def run(
        self,
        *,
        signals: pd.DataFrame,
        artifact_dir: Path,
        start_date: str,
        end_date: str,
        benchmark: str,
        data_bundle_path: str | None = None,
        initial_cash: float = 1_000_000.0,
        stock_commission_multiplier: float = 1.0,
        minimum_commission: float = 5.0,
        tax_multiplier: float = 1.0,
        slippage: float = 0.0,
        timeout_seconds: int = 600,
    ) -> dict[str, Any]:
        if not self.status()["available"]:
            raise OptionalEngineUnavailableError(
                "RQAlpha 未安装；运行 uv sync --extra rqalpha-validation。"
            )
        artifacts = self.prepare_validation(signals, artifact_dir)
        output_path = artifact_dir / "rqalpha_result.pkl"
        command = [
            sys.executable,
            "-m",
            "rqalpha",
            "run",
            "-f",
            artifacts["strategy_path"],
            "-s",
            start_date,
            "-e",
            end_date,
            "-bm",
            benchmark,
            "-a",
            "stock",
            str(initial_cash),
            "--stock-t1",
            "--matching-type",
            "next_bar",
            "--stock-commission-multiplier",
            str(stock_commission_multiplier),
            "--cn-stock-min-commission",
            str(minimum_commission),
            "--tax-multiplier",
            str(tax_multiplier),
            "--slippage",
            str(slippage),
            "-o",
            str(output_path),
        ]
        if data_bundle_path:
            command.extend(["--data-bundle-path", data_bundle_path])
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "RQAlpha validation failed: "
                + (completed.stderr.strip() or completed.stdout.strip())
            )
        return {
            **artifacts,
            "result_path": str(output_path),
            "command": command,
            "formal_result": False,
        }
