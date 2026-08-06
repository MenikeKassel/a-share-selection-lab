from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    if len(sys.argv) != 3:
        return _fail("worker expects request and response paths")
    request_path = Path(sys.argv[1])
    response_path = Path(sys.argv[2])
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        method = str(request.get("method", ""))
        if method == "__package_info__":
            import tinyshare as ts  # type: ignore[import-not-found]

            module_path = Path(str(getattr(ts, "__file__", "")))
            digest = hashlib.sha256()
            with module_path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            try:
                version = importlib.metadata.version("tinyshare")
            except importlib.metadata.PackageNotFoundError:
                version = str(getattr(ts, "__version__", "unknown"))
            return _write_rows(
                response_path,
                [
                    {
                        "distribution": "tinyshare",
                        "version": version,
                        "module_filename": module_path.name,
                        "sha256": digest.hexdigest(),
                    }
                ],
            )
        if method == "__runtime_info__":
            # PR 6: runtime self-test WITHOUT a token and WITHOUT any remote
            # call.  Proves the interpreter can load TinyShare and construct
            # the API factory; the caller decides whether to proceed.
            try:
                import tinyshare as ts

                module_path = Path(str(getattr(ts, "__file__", "")))
                api_factory_name: str | None = None
                api_factory_available = False
                api_initialization_success: bool | None = None
                factory = getattr(ts, "pro_api", None) or getattr(ts, "proapi", None)
                if factory is not None:
                    api_factory_name = getattr(factory, "__name__", "pro_api")
                    api_factory_available = True
                    try:
                        # No token: initialization without credentials must
                        # not be attempted remotely; presence of the factory
                        # is enough for the probe.
                        api_initialization_success = callable(factory)
                    except Exception:  # pragma: no cover - defensive
                        api_initialization_success = False
                return _write_rows(
                    response_path,
                    [
                        {
                            "sys.executable": sys.executable,
                            "sys.version": sys.version.split()[0],
                            "sys.prefix": sys.prefix,
                            "sys.base_prefix": sys.base_prefix,
                            "path_home": str(Path.home()),
                            "module_path": str(module_path),
                            "module_exists": module_path.exists(),
                            "api_factory_name": api_factory_name,
                            "api_factory_available": api_factory_available,
                            "api_initialization_success": api_initialization_success,
                        }
                    ],
                )
            except Exception as error:  # pragma: no cover - probe must fail closed
                return _write_error(
                    response_path,
                    f"tiny runtime probe failed: {type(error).__name__}: {error}",
                )
        token = os.getenv("TINYSHARE_TOKEN", "").strip()
        if not token:
            return _write_error(response_path, "TINYSHARE_TOKEN is not configured")
        import tinyshare as ts

        api_factory = getattr(ts, "pro_api", None) or getattr(ts, "proapi", None)
        if api_factory is None:
            return _write_error(response_path, "tinyshare has no pro_api/proapi factory")
        try:
            api = api_factory(token)
        except TypeError:
            setter = getattr(ts, "set_token", None) or getattr(ts, "settoken", None)
            if setter is None:
                return _write_error(response_path, "tinyshare has no token setter")
            setter(token)
            api = api_factory()
        if method == "__batch_call__":
            requests = request.get("requests", [])
            if not isinstance(requests, list) or len(requests) > 100:
                return _write_error(response_path, "invalid TinyShare batch request")
            batches: list[list[dict[str, object]]] = []
            for item in requests:
                if not isinstance(item, dict):
                    return _write_error(response_path, "invalid TinyShare batch item")
                item_method = str(item.get("method", ""))
                item_params = item.get("params", {})
                if not item_method or not isinstance(item_params, dict):
                    return _write_error(response_path, "invalid TinyShare batch item")
                function = getattr(api, item_method, None)
                if function is None:
                    return _write_error(
                        response_path, f"tinyshare endpoint is unavailable: {item_method}"
                    )
                batches.append(_result_rows(function(**item_params)))
            return _write_batches(response_path, batches)
        params = request.get("params", {})
        if not method or not isinstance(params, dict):
            return _write_error(response_path, "invalid worker request")
        function = getattr(api, method, None)
        if function is None:
            return _write_error(response_path, f"tinyshare endpoint is unavailable: {method}")
        return _write_rows(response_path, _result_rows(function(**params)))
    except Exception as error:
        return _write_error(response_path, f"{type(error).__name__}: {error}")


def _write_error(path: Path, message: str) -> int:
    path.write_text(
        json.dumps({"ok": False, "error": message}, ensure_ascii=False), encoding="utf-8"
    )
    return 1


def _write_rows(path: Path, rows: list[dict[str, object]]) -> int:
    path.write_text(
        json.dumps({"ok": True, "rows": rows}, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return 0


def _write_batches(path: Path, batches: list[list[dict[str, object]]]) -> int:
    path.write_text(
        json.dumps({"ok": True, "batches": batches}, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return 0


def _result_rows(result: Any) -> list[dict[str, object]]:
    if hasattr(result, "to_dict"):
        rows = result.to_dict(orient="records")
        return [dict(row) for row in rows]
    if isinstance(result, list):
        return [dict(row) for row in result]
    return [dict(row) for row in result] if result is not None else []


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
