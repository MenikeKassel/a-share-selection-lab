from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib import metadata
from importlib.util import find_spec
from typing import Any


@dataclass(frozen=True, slots=True)
class EngineStatus:
    engine_type: str
    installed: bool
    available: bool
    version: str | None
    required: bool
    functions: list[str]
    formal_result: bool
    license_notice: str
    installation_extra: str
    production_enabled: bool
    unavailable_reason: str | None = None
    last_run: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _EngineDefinition:
    engine_type: str
    distribution: str
    module: str
    functions: tuple[str, ...]
    formal_result: bool
    license_notice: str
    installation_extra: str
    production_enabled: bool = False


_DEFINITIONS = (
    _EngineDefinition(
        engine_type="alphalens",
        distribution="alphalens-reloaded",
        module="alphalens",
        functions=("因子IC", "Rank IC", "分组收益", "换手率", "衰减", "行业分析"),
        formal_result=False,
        license_notice="Apache-2.0 license；用于研究结果验证。",
        installation_extra="factor-research",
    ),
    _EngineDefinition(
        engine_type="vectorbt",
        distribution="vectorbt",
        module="vectorbt",
        functions=("快速参数扫描", "权重敏感性", "持有期与调仓频率研究"),
        formal_result=False,
        license_notice=("Apache-2.0 with Commons Clause license；商业产品与服务需单独审查限制。"),
        installation_extra="fast-backtest",
    ),
    _EngineDefinition(
        engine_type="rqalpha",
        distribution="rqalpha",
        module="rqalpha",
        functions=("事件驱动交叉验证", "成交与账户状态对照", "费用差异分析"),
        formal_result=False,
        license_notice="Apache-2.0 license；仅作可选交叉验证。",
        installation_extra="rqalpha-validation",
    ),
    _EngineDefinition(
        engine_type="qlib",
        distribution="pyqlib",
        module="qlib",
        functions=("数据集导出", "实验记录", "预测导入", "规则模型对比"),
        formal_result=False,
        license_notice="MIT license；模型只用于实验，默认禁止生产。",
        installation_extra="ml-research",
    ),
)


class EngineRegistry:
    """Discovers optional engines without importing them into business code."""

    def statuses(self, last_runs: dict[str, dict[str, Any]] | None = None) -> list[EngineStatus]:
        run_lookup = last_runs or {}
        return [self._status(item, run_lookup.get(item.engine_type)) for item in _DEFINITIONS]

    @staticmethod
    def _status(definition: _EngineDefinition, last_run: dict[str, Any] | None) -> EngineStatus:
        try:
            version = metadata.version(definition.distribution)
            installed = True
        except metadata.PackageNotFoundError:
            version = None
            installed = False

        available = installed and find_spec(definition.module) is not None
        reason = None
        if not installed:
            reason = f"未安装可选依赖；运行 uv sync --extra {definition.installation_extra}。"
        elif not available:
            reason = "已找到发行包，但 Python 模块不可导入。"

        return EngineStatus(
            engine_type=definition.engine_type,
            installed=installed,
            available=available,
            version=version,
            required=False,
            functions=list(definition.functions),
            formal_result=definition.formal_result,
            license_notice=definition.license_notice,
            installation_extra=definition.installation_extra,
            production_enabled=definition.production_enabled,
            unavailable_reason=reason,
            last_run=last_run,
        )
