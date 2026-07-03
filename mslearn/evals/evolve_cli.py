"""CLI entry: python -m mslearn.evals.evolve_cli --once"""

import sys

from mslearn.evals.evolve import evolve_once
from mslearn.worker.context import build_default_context


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--once" not in argv:
        print("usage: python -m mslearn.evals.evolve_cli --once")
        return 2
    ctx = build_default_context()
    summary = evolve_once(ctx)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
