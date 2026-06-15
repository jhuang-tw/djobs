"""Validate the MCP Registry manifest (server.json) stays correct.

server.json describes djobs for the official MCP Registry so agents can discover
it as an MCP server (not only via PyPI / the Marketplace). These checks guard the
fields the registry validates and the invariant that the manifest version tracks
the single source of truth in ``djobs.__version__``.
"""

from __future__ import annotations

import json
from pathlib import Path

import djobs

_SERVER_JSON = Path(__file__).resolve().parents[2] / "server.json"


def _load() -> dict:
    return json.loads(_SERVER_JSON.read_text(encoding="utf-8"))


def test_registry_identity_and_limits() -> None:
    # The registry verifies namespace ownership via this reverse-DNS name (which
    # must match the `<!-- mcp-name: ... -->` marker in the PyPI README) and caps
    # the description at 100 characters.
    data = _load()
    assert data["name"] == "io.github.jhuang-tw/djobs"
    assert 0 < len(data["description"]) <= 100


def test_version_tracks_package_version() -> None:
    data = _load()
    assert data["version"] == djobs.__version__
    for package in data["packages"]:
        assert package["version"] == djobs.__version__


def test_pypi_package_launches_mcp_subcommand() -> None:
    # Ownership is verified against the real PyPI package `djobs`; the MCP server
    # is launched via the `mcp` subcommand (uvx djobs mcp), so the identifier can
    # stay the verifiable package name rather than a non-existent `djobs-mcp` dist.
    package = _load()["packages"][0]
    assert package["registryType"] == "pypi"
    assert package["registryBaseUrl"] == "https://pypi.org"
    assert package["identifier"] == "djobs"
    assert package["transport"]["type"] == "stdio"
    positional = [
        arg["value"]
        for arg in package.get("packageArguments", [])
        if arg.get("type") == "positional"
    ]
    assert positional == ["mcp"]
