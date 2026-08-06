"""PR 6 tests: TinyShare runtime probe fail-closed behaviour."""
import json
import sys

import pytest
from app.adapters.market_data.tinyshare.client import (
    TinyShareIsolatedClient,
    TinyShareProviderError,
)


def test_runtime_info_fails_when_interpreter_missing() -> None:
    client = TinyShareIsolatedClient(
        python_executable="Z:/does/not/exist/python.exe",
        token_env="TINYSHARE_TOKEN",
    )
    with pytest.raises(TinyShareProviderError, match="does not exist"):
        client.runtime_info()


def test_runtime_info_fails_when_not_configured() -> None:
    client = TinyShareIsolatedClient(
        python_executable=None,
        token_env="TINYSHARE_TOKEN",
    )
    with pytest.raises(TinyShareProviderError, match="not configured"):
        client.runtime_info()


def test_runtime_probe_worker_reports_environment(monkeypatch, tmp_path) -> None:
    """Run the real worker with __runtime_info__: without tinyshare in the
    interpreter the probe FAILS CLOSED with a clear error (never a crash,
    never a token leak)."""
    import importlib

    worker = importlib.import_module("app.adapters.market_data.tinyshare.worker")

    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    request_path.write_text(
        json.dumps({"method": "__runtime_info__", "params": {}}), encoding="utf-8"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["worker.py", str(request_path), str(response_path)],
    )
    exit_code = worker.main()
    payload = json.loads(response_path.read_text(encoding="utf-8"))
    if exit_code == 0:
        # tinyshare IS installed: assert the probe reported environment.
        assert payload["ok"] is True
        info = payload["rows"][0]
        assert info["sys.executable"]
        assert info["sys.version"]
        assert info["module_path"]
        assert "token" not in json.dumps(info).lower()
    else:
        # tinyshare NOT installed: fail closed with a clear error.
        assert payload["ok"] is False
        assert "tinyshare" in payload["error"]
        assert "TINYSHARE_TOKEN" not in payload["error"]


def test_runtime_probe_reports_factory_when_available(monkeypatch, tmp_path) -> None:
    """The worker reports whether tinyshare exposes a usable API factory."""
    import importlib
    import types

    worker = importlib.import_module("app.adapters.market_data.tinyshare.worker")

    fake_ts = types.ModuleType("tinyshare")
    fake_ts.__file__ = str(tmp_path / "tinyshare.py")
    (tmp_path / "tinyshare.py").write_text("def pro_api(token): pass\n", encoding="utf-8")
    fake_ts.pro_api = lambda token: object()

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "tinyshare":
            return fake_ts
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    request_path.write_text(
        json.dumps({"method": "__runtime_info__", "params": {}}), encoding="utf-8"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["worker.py", str(request_path), str(response_path)],
    )
    exit_code = worker.main()
    assert exit_code == 0
    payload = json.loads(response_path.read_text(encoding="utf-8"))
    info = payload["rows"][0]
    assert info["api_factory_available"] is True
    assert info["api_initialization_success"] is True
