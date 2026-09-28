"""Thin executable wrapper around the one canonical djobs CLI dispatcher."""

from __future__ import annotations

from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> None:
    from djobs.entrypoint import main as run_public_cli

    run_public_cli(argv)


if __name__ == "__main__":
    main()
