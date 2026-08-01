from __future__ import annotations

import argparse
import json
from pathlib import Path

import uvicorn

from app.core.config import get_settings
from app.engines.registry import EngineRegistry


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
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=getattr(args, "host", "127.0.0.1"),
        port=getattr(args, "port", 8000),
        reload=settings.environment == "development",
    )


if __name__ == "__main__":
    main()
