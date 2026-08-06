from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class TinyShareProviderError(RuntimeError):
    """A supplementary provider could not be called safely."""


@dataclass(frozen=True, slots=True)
class TinyShareCapability:
    name: str
    method: str
    required: bool
    available: bool
    row_count: int = 0
    columns: tuple[str, ...] = ()
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "method": self.method,
            "required": self.required,
            "available": self.available,
            "row_count": self.row_count,
            "columns": list(self.columns),
            "error": self.error,
        }


class TinyShareIsolatedClient:
    """Run TinyShare outside the application interpreter.

    The token is read only from the current process environment and copied to
    the child environment for one request.  It is never included in request
    JSON, exception text, or returned metadata.
    """

    def __init__(
        self,
        *,
        python_executable: str | Path | None = None,
        worker_path: str | Path | None = None,
        timeout_seconds: int = 120,
        token_env: str = "TINYSHARE_TOKEN",
    ) -> None:
        configured_python = python_executable or os.getenv("TINYSHARE_PYTHON")
        self.python_executable = str(configured_python) if configured_python else None
        self.worker_path = Path(worker_path or Path(__file__).with_name("worker.py")).resolve()
        self.timeout_seconds = timeout_seconds
        self.token_env = token_env

    @property
    def configured(self) -> bool:
        return bool(os.getenv(self.token_env, "").strip())

    def package_info(self) -> dict[str, Any]:
        """Return the isolated package version and module hash without a token."""

        rows = self.call("__package_info__")
        if not rows:
            raise TinyShareProviderError("TinyShare package inspection returned no metadata")
        return rows[0]

    def runtime_info(self) -> dict[str, Any]:
        """PR 6: run the isolated interpreter's runtime self-test.

        Fails closed when the configured interpreter is missing (no silent
        fallback to the host interpreter), when it cannot start, or when
        TinyShare cannot be imported / initialised.  The probe makes no
        remote calls and never touches the token.
        """
        if self.python_executable is None:
            raise TinyShareProviderError(
                "TINYSHARE_PYTHON is not configured; refusing to fall back to "
                "the host interpreter"
            )
        interpreter = Path(self.python_executable).expanduser()
        if not interpreter.exists():
            raise TinyShareProviderError(
                f"configured TINYSHARE_PYTHON does not exist: {interpreter}"
            )
        rows = self.call("__runtime_info__")
        if not rows:
            raise TinyShareProviderError("TinyShare runtime probe returned no metadata")
        info = rows[0]
        if info.get("api_factory_available") is not True:
            raise TinyShareProviderError(
                "TinyShare runtime probe failed: no pro_api/proapi factory "
                "in the configured interpreter"
            )
        if info.get("api_initialization_success") is not True:
            raise TinyShareProviderError(
                "TinyShare runtime probe failed: API factory is not callable "
                "in the configured interpreter"
            )
        return info

    def call(self, method: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        payload = self._invoke({"method": method, "params": params or {}})
        rows = payload.get("rows", [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise TinyShareProviderError("TinyShare worker response rows are not objects")
        return rows

    def call_many(
        self, method: str, params: list[dict[str, Any]]
    ) -> list[list[dict[str, Any]]]:
        """Execute several calls through one isolated TinyShare process."""

        if not params:
            return []
        payload = self._invoke(
            {
                "method": "__batch_call__",
                "requests": [{"method": method, "params": item} for item in params],
            }
        )
        batches = payload.get("batches", [])
        if not isinstance(batches, list):
            raise TinyShareProviderError("TinyShare worker response batches are invalid")
        output: list[list[dict[str, Any]]] = []
        for rows in batches:
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise TinyShareProviderError("TinyShare worker batch rows are not objects")
            output.append(rows)
        if len(output) != len(params):
            raise TinyShareProviderError("TinyShare worker returned an incomplete batch")
        return output

    def _invoke(self, request: dict[str, Any]) -> dict[str, Any]:
        method = str(request.get("method", ""))
        package_probe = method == "__package_info__"
        if not self.configured and not package_probe:
            raise TinyShareProviderError(f"{self.token_env} is not configured")
        if self.python_executable is None:
            raise TinyShareProviderError(
                "TINYSHARE_PYTHON is not configured; use an isolated Python 3.11 interpreter"
            )
        if not self.worker_path.exists():
            raise TinyShareProviderError(f"TinyShare worker does not exist: {self.worker_path}")
        with tempfile.TemporaryDirectory(prefix="ashare-tinyshare-") as directory:
            root = Path(directory)
            request_path = root / "request.json"
            response_path = root / "response.json"
            request_path.write_text(
                json.dumps(request, ensure_ascii=False),
                encoding="utf-8",
            )
            environment = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONIOENCODING": "utf-8",
            }
            # Keep the child isolated from arbitrary host variables, while
            # preserving the Windows paths needed by pathlib, tempfile,
            # requests and TinyShare's local device/cache helpers.
            for name in (
                "SYSTEMROOT",
                "WINDIR",
                "COMSPEC",
                "USERPROFILE",
                "HOMEDRIVE",
                "HOMEPATH",
                "APPDATA",
                "LOCALAPPDATA",
                "PROGRAMDATA",
                "TEMP",
                "TMP",
            ):
                value = os.environ.get(name)
                if value:
                    environment[name] = value
            home = os.environ.get("HOME") or os.environ.get("USERPROFILE")
            if home:
                environment["HOME"] = home
            if self.configured:
                environment[self.token_env] = os.environ[self.token_env]
                environment["TINYSHARE_TOKEN"] = os.environ[self.token_env]
            try:
                completed = subprocess.run(
                    [
                        self.python_executable,
                        str(self.worker_path),
                        str(request_path),
                        str(response_path),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    env=environment,
                    timeout=self.timeout_seconds,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise TinyShareProviderError(
                    f"TinyShare worker failed to start: {error}"
                ) from error
            if completed.returncode != 0:
                detail = (
                    completed.stderr or completed.stdout or "worker exited without a message"
                ).strip()
                raise TinyShareProviderError(self._redact(detail))
            if not response_path.exists():
                raise TinyShareProviderError("TinyShare worker returned no response")
            try:
                payload = json.loads(response_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise TinyShareProviderError("TinyShare worker returned invalid JSON") from error
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise TinyShareProviderError(
                self._redact(str(payload.get("error", "unknown provider error")))
            )
        return payload

    def probe(self, capabilities: list[dict[str, Any]]) -> list[TinyShareCapability]:
        output: list[TinyShareCapability] = []
        for capability in capabilities:
            name = str(capability["name"])
            method = str(capability["method"])
            required = bool(capability.get("required", True))
            error: TinyShareProviderError | None = None
            rows: list[dict[str, Any]] | None = None
            attempts = 2 if self.configured else 1
            for _ in range(attempts):
                try:
                    rows = self.call(method, dict(capability.get("params", {})))
                    break
                except TinyShareProviderError as caught:
                    error = caught
            if rows is not None:
                columns = tuple(sorted(rows[0].keys())) if rows else ()
                output.append(TinyShareCapability(name, method, required, True, len(rows), columns))
            else:
                output.append(
                    TinyShareCapability(
                        name,
                        method,
                        required,
                        False,
                        error=self._redact(str(error or "unknown provider error")),
                    )
                )
        return output

    def _redact(self, value: str) -> str:
        # A token is normally not present in provider errors, but never trust a
        # third-party exception to keep it out of captured diagnostics.
        for token_name in {self.token_env, "TINYSHARE_TOKEN"}:
            token = os.getenv(token_name, "")
            if token:
                value = value.replace(token, "[REDACTED]")
        return value
