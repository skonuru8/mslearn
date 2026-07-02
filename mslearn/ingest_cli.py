"""Ingest a source: python -m mslearn.ingest_cli <ref> [--role spine] [--local]"""
import argparse

from mslearn.worker.context import get_context


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a source into mslearn")
    parser.add_argument("ref")
    parser.add_argument("--role", choices=["spine", "supplement"], default="supplement")
    parser.add_argument("--type", dest="source_type", default=None)
    parser.add_argument("--local", action="store_true",
                        help="run extraction inline (no Redis/worker needed)")
    args = parser.parse_args()

    from mslearn.pipeline.orchestrator import ingest_source
    from mslearn.worker.app import app
    from mslearn.worker.context import build_default_context, set_context

    if args.local:
        app.conf.task_always_eager = True
    set_context(build_default_context())
    source_id = ingest_source(args.ref, role=args.role, source_type=args.source_type)

    row = get_context().db.source_row(source_id)
    print(f"{source_id}: status={row['status']} chunks={row['total_chunks']}"
          f" done={row['done_chunks']} failed={row['failed_chunks']}")


if __name__ == "__main__":
    main()
