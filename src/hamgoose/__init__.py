"""hamgoose - Factory-Droid-style Mission orchestration extension for Goose.

hamgoose is a standalone MCP server (stdio transport) built on the official
Goose custom-extension model. It owns deterministic orchestration mechanics
(mission state, scheduling, retries, persistence, Git tracking, event logging)
while delegating semantic reasoning (decomposition, implementation, validation)
to isolated Goose workers.

Run as an MCP server via:
    hamgoose            # installed entry point
    python -m hamgoose  # module entry point
"""
__all__ = ["main", "get_extension", "__version__"]

try:
    # HG-16: single source of truth - installed package metadata. The literal
    # below is only the fallback when hamgoose runs from a source tree that was
    # never installed (version skew between tree and install is exactly what
    # the M-2026-1909541C forensics uncovered).
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    __version__ = _pkg_version("hamgoose")
except Exception:  # pragma: no cover - source-tree fallback
    __version__ = "0.1.8-dev"


def main():
    import sys

    argv = sys.argv[1:]
    if argv and argv[0] in {"register", "add", "unregister", "remove", "help", "--help", "-h"}:
        from .register import cli_main

        raise SystemExit(cli_main(argv))
    from .server import main as _main

    return _main()


def get_extension():
    from .server import get_extension as _get

    return _get()
