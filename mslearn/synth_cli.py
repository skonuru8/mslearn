"""Run synthesis pipeline: python -m mslearn.synth_cli [--local]."""
import argparse

from mslearn.worker.context import get_context


def main() -> None:
    parser = argparse.ArgumentParser(description="Run synthesis over current graph")
    parser.add_argument(
        "--local",
        action="store_true",
        help="run Celery task eagerly in-process",
    )
    args = parser.parse_args()

    from mslearn.worker.app import app
    from mslearn.worker.context import build_default_context, set_context
    from mslearn.worker.tasks import synthesize_task

    if args.local:
        app.conf.task_always_eager = True
    set_context(build_default_context())
    synthesize_task.delay().get()
    graph = get_context().graph
    concepts = graph.all_concepts()
    curriculum = graph.curriculum()
    print(f"concepts={len(concepts)} curriculum={len(curriculum)}")


if __name__ == "__main__":
    main()
