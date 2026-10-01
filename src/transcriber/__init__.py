import sys


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--self-check":
        from .selfcheck import run_self_check

        raise SystemExit(run_self_check(sys.argv[2] if len(sys.argv) > 2 else None))

    from .gui.app import run

    raise SystemExit(run())
