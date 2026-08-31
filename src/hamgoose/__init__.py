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
__version__ = "0.1.5"


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
