"""Public CLI dispatcher adding the host contract without rewriting the established CLI."""

from __future__ import annotations

import sys


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "contract":
        from djobs.contract_cli import main as contract_main

        raise SystemExit(contract_main(argv[1:]))
    from djobs.entrypoint import main as established_main

    established_main()
    if not argv or argv[0] in {"--help", "-h", "help"}:
        print("\nExternal host contract:\n  djobs contract --help")


if __name__ == "__main__":
    main()
