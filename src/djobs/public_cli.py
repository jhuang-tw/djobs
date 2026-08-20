"""Public CLI dispatcher for focused high-level djobs surfaces."""

from __future__ import annotations

import sys


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "contract":
        from djobs.contract_cli import main as contract_main

        raise SystemExit(contract_main(argv[1:]))
    if argv and argv[0] == "context":
        from djobs.context_cli import main as context_main

        raise SystemExit(context_main(argv[1:]))
    from djobs.entrypoint import main as established_main

    established_main()
    if not argv or argv[0] in {"--help", "-h", "help"}:
        print(
            "\nContext preview:\n"
            "  djobs context [current request]\n\n"
            "External host contract:\n"
            "  djobs contract --help"
        )


if __name__ == "__main__":
    main()
