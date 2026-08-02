from __future__ import annotations

import sys
from pathlib import Path

from app.adapters.market_data.tinyshare import TinyShareIsolatedClient, TinyShareProviderError
from app.adapters.market_data.tinyshare.supplement import TinyShareSnapshotCompleter


def test_tinyshare_probe_degrades_without_token(monkeypatch) -> None:
    monkeypatch.delenv("TINYSHARE_TOKEN", raising=False)
    capabilities = TinyShareSnapshotCompleter(TinyShareIsolatedClient()).probe()
    assert capabilities
    assert all(not item.available for item in capabilities)
    assert all(item.error == "TINYSHARE_TOKEN is not configured" for item in capabilities)


def test_tinyshare_requires_an_explicit_isolated_interpreter(monkeypatch) -> None:
    monkeypatch.setenv("TINYSHARE_TOKEN", "session-token")
    monkeypatch.delenv("TINYSHARE_PYTHON", raising=False)

    client = TinyShareIsolatedClient()

    try:
        client.call("trade_cal")
    except TinyShareProviderError as error:
        assert "isolated Python 3.11" in str(error)
    else:  # pragma: no cover - defensive assertion for the security boundary
        raise AssertionError("TinyShare must not run inside the FastAPI interpreter")


def test_tinyshare_client_does_not_echo_token(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TINYSHARE_TOKEN", "secret-token-for-test")
    worker = tmp_path / "worker.py"
    worker_source = (
        "from pathlib import Path; import sys; "
        'Path(sys.argv[2]).write_text(\'{"ok":false,"error":"secret-token-for-test"}\')'
    )
    worker.write_text(
        worker_source,
        encoding="utf-8",
    )
    client = TinyShareIsolatedClient(worker_path=worker, python_executable=sys.executable)
    try:
        client.call("anything")
    except TinyShareProviderError as error:
        assert "secret-token-for-test" not in str(error)
        assert "[REDACTED]" in str(error)


def test_tinyshare_client_passes_only_required_host_paths(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TINYSHARE_TOKEN", "session-token")
    monkeypatch.setenv("USERPROFILE", r"C:\Users\fixture")
    monkeypatch.setenv("HOMEDRIVE", "C:")
    monkeypatch.setenv("HOMEPATH", r"\Users\fixture")
    monkeypatch.setenv("APPDATA", r"C:\Users\fixture\AppData\Roaming")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\fixture\AppData\Local")
    monkeypatch.setenv("TEMP", r"C:\Temp\fixture")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-cross-boundary")
    worker = tmp_path / "worker.py"
    worker.write_text(
        """
import json
import os
import sys
from pathlib import Path

keys = [
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME", "APPDATA",
    "LOCALAPPDATA", "TEMP", "UNRELATED_SECRET",
]
row = {key: os.environ.get(key) for key in keys}
Path(sys.argv[2]).write_text(json.dumps({"ok": True, "rows": [row]}))
""".strip(),
        encoding="utf-8",
    )

    client = TinyShareIsolatedClient(worker_path=worker, python_executable=sys.executable)
    [environment] = client.call("anything")

    assert environment["USERPROFILE"] == r"C:\Users\fixture"
    assert environment["HOMEDRIVE"] == "C:"
    assert environment["HOMEPATH"] == r"\Users\fixture"
    assert environment["HOME"] == r"C:\Users\fixture"
    assert environment["APPDATA"] == r"C:\Users\fixture\AppData\Roaming"
    assert environment["LOCALAPPDATA"] == r"C:\Users\fixture\AppData\Local"
    assert environment["TEMP"] == r"C:\Temp\fixture"
    assert environment["UNRELATED_SECRET"] is None


def test_tinyshare_client_batches_calls_in_one_worker(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TINYSHARE_TOKEN", "session-token")
    worker = tmp_path / "worker.py"
    worker.write_text(
        """
import json
import sys
from pathlib import Path

request = json.loads(Path(sys.argv[1]).read_text())
assert request["method"] == "__batch_call__"
batches = [[{"trade_date": item["params"]["trade_date"]}] for item in request["requests"]]
Path(sys.argv[2]).write_text(json.dumps({"ok": True, "batches": batches}))
""".strip(),
        encoding="utf-8",
    )
    client = TinyShareIsolatedClient(worker_path=worker, python_executable=sys.executable)

    batches = client.call_many(
        "daily_basic", [{"trade_date": "20240102"}, {"trade_date": "20240103"}]
    )

    assert batches == [
        [{"trade_date": "20240102"}],
        [{"trade_date": "20240103"}],
    ]
