from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import uvicorn

from app.core.config import get_settings
from app.engines.registry import EngineRegistry


def _progress_printer() -> Callable[[str, int, int, str], None]:
    """Render walk-forward progress on stderr without polluting stdout JSON.

    Interactive terminals get an in-place ``\\r`` bar; redirected output
    (e.g. ``> log 2> err``) gets one compact line per update so logs stay
    readable.
    """

    interactive = sys.stderr.isatty()
    rendered: dict[str, Any] = {"stage": None}

    def render(stage: str, done: int, total: int, detail: str) -> None:
        if stage != rendered["stage"]:
            if interactive and rendered["stage"] is not None:
                sys.stderr.write("\n")
            rendered["stage"] = stage
            rendered["done"] = 0
        percent = done / total if total > 0 else 1.0
        label = f"[{stage}] {done}/{total} ({percent:5.1%}) {detail}"
        if interactive:
            bar_width = 24
            bar = "█" * round(percent * bar_width) + "░" * (
                bar_width - round(percent * bar_width)
            )
            sys.stderr.write(f"\r\033[K{label} {bar}")
        else:
            sys.stderr.write(f"{label}\n")
        sys.stderr.flush()
        rendered["done"] = done

    return render


def main() -> None:
    parser = argparse.ArgumentParser(prog="ashare-lab")
    subparsers = parser.add_subparsers(dest="command")
    serve = subparsers.add_parser("serve", help="start the FastAPI service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    subparsers.add_parser("engines", help="show optional engine status")
    snapshot = subparsers.add_parser(
        "validate-snapshot",
        help="audit an imported point-in-time historical snapshot",
    )
    snapshot.add_argument("manifest", type=Path)
    experiment = subparsers.add_parser(
        "first-experiment",
        help="run the deterministic first end-to-end experiment",
    )
    experiment.add_argument(
        "--output-root",
        type=Path,
        default=Path("./data/experiments/first"),
    )
    probe = subparsers.add_parser(
        "probe-supplement-provider",
        help="probe the isolated TinyShare supplementary data provider",
    )
    probe.add_argument("--python", dest="python_executable", type=Path, default=None)
    purchased = subparsers.add_parser(
        "import-purchased-snapshot",
        help="import the read-only purchased CSV archive into an immutable snapshot",
    )
    purchased.add_argument("--source", type=Path, required=True)
    purchased.add_argument("--snapshot-root", type=Path, default=Path("./data/raw/imports"))
    purchased.add_argument("--snapshot-id", default="ashare-2018-2025-v1")
    purchased.add_argument("--start-date", type=date.fromisoformat, default=date(2016, 1, 1))
    purchased.add_argument("--end-date", type=date.fromisoformat, default=date(2025, 12, 31))
    purchased.add_argument("--supplement-tinyshare", action="store_true")
    walk = subparsers.add_parser(
        "run-walk-forward",
        help="run the immutable trend_quality_v1 walk-forward experiment",
    )
    walk.add_argument("--experiment-code", default="trend-quality-wf-2018-2025-purchased-v1")
    walk.add_argument(
        "--manifest",
        type=Path,
        default=Path("./data/raw/imports/ashare-2018-2025-v1/manifest.json"),
    )
    args = parser.parse_args()
    if args.command == "engines":
        print(
            json.dumps(
                [item.as_dict() for item in EngineRegistry().statuses()],
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "validate-snapshot":
        from app.data.snapshots import audit_snapshot_files, validate_snapshot_manifest

        manifest = validate_snapshot_manifest(args.manifest)
        print(
            json.dumps(
                {
                    "snapshot_id": manifest.snapshot_id,
                    **audit_snapshot_files(manifest),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "first-experiment":
        from app.experiments.first_experiment import run_first_experiment

        result = run_first_experiment(args.output_root)
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "run_id": result["run_id"],
                    "report_path": result["report_path"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "probe-supplement-provider":
        from app.adapters.market_data.tinyshare import (
            TinyShareIsolatedClient,
            TinyShareProviderError,
        )
        from app.adapters.market_data.tinyshare.supplement import TinyShareSnapshotCompleter

        client = TinyShareIsolatedClient(python_executable=args.python_executable)
        completer = TinyShareSnapshotCompleter(client)
        capabilities = completer.probe()
        try:
            package_info = client.package_info()
        except TinyShareProviderError as error:
            package_info = {"available": False, "error": str(error)}
        print(
            json.dumps(
                {
                    "provider": "tinyshare",
                    "configured": bool(os.getenv("TINYSHARE_TOKEN")),
                    "package": package_info,
                    "capabilities": [item.as_dict() for item in capabilities],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "import-purchased-snapshot":
        from app.adapters.market_data.purchased_csv import (
            PurchasedCsvSnapshotImporter,
            PurchasedCsvSnapshotResult,
        )

        snapshot_directory = (args.snapshot_root / args.snapshot_id).expanduser().resolve()
        importer = PurchasedCsvSnapshotImporter(
            source_dir=args.source,
            snapshot_root=args.snapshot_root,
            snapshot_id=args.snapshot_id,
            start_date=args.start_date,
            end_date=args.end_date,
        )
        import_result: PurchasedCsvSnapshotResult | None = None
        manifest_path = snapshot_directory / "manifest.json"
        if not (args.supplement_tinyshare and manifest_path.exists()):
            import_result = importer.run()
            manifest_path = import_result.manifest_path
        payload: dict[str, object] = {
            "snapshot_id": args.snapshot_id,
            "manifest_path": str(manifest_path),
        }
        if import_result is not None:
            payload.update(
                {
                    "daily_path": str(import_result.daily_path),
                    "row_count": import_result.row_count,
                    "symbol_count": import_result.symbol_count,
                    "date_range": [
                        import_result.min_date.isoformat(),
                        import_result.max_date.isoformat(),
                    ],
                }
            )
        if args.supplement_tinyshare:
            from app.adapters.market_data.tinyshare import TinyShareIsolatedClient
            from app.adapters.market_data.tinyshare.supplement import TinyShareSnapshotCompleter

            payload["supplement"] = TinyShareSnapshotCompleter(TinyShareIsolatedClient()).complete(
                manifest_path.parent,
                start_date=args.start_date,
                end_date=args.end_date,
            )
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return
    if args.command == "run-walk-forward":
        from app.api.schemas import WalkForwardRunRequest
        from app.api.serializers import walk_forward_dict
        from app.db.session import create_session_factory, initialize_database
        from app.services.walk_forward import WalkForwardTaskService

        initialize_database()
        settings = get_settings()
        with create_session_factory()() as session:
            record = WalkForwardTaskService(session, settings).run(
                WalkForwardRunRequest(
                    experiment_code=args.experiment_code,
                    snapshot_manifest_path=str(args.manifest),
                ),
                progress=_progress_printer(),
            )
        print(json.dumps(walk_forward_dict(record), ensure_ascii=False, indent=2, default=str))
        return
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=getattr(args, "host", "127.0.0.1"),
        port=getattr(args, "port", 8000),
        reload=settings.environment == "development",
    )


if __name__ == "__main__":
    main()
