"""Command line entry point: `python -m uwcopilot.mcp_server` (also `make mcp`).

stdio is the default transport: an MCP client (Claude Desktop, Claude Code, the MCP Python SDK client, ...)
starts this command as a subprocess and talks JSON-RPC over its stdin/stdout. Nothing else may be written to
stdout, so logging goes to stderr.

--transport streamable-http serves MCP over HTTP at http://<host>:<port><path> instead. The HTTP server has no
authentication, so it binds to 127.0.0.1 by default and refuses any other address unless --unsafe-allow-remote is
given (then put it behind something that authenticates callers).

The server started here serves the SYNTHETIC guidelines and the SYNTHETIC lending programs (corpus/programs/), so a
client can fetch the [NSP-x.y] sections that startup memos cite.
"""

import argparse
import ipaddress
import logging
import sys
from collections.abc import Sequence

from uwcopilot.agent.programs import PROGRAMS_DIR
from uwcopilot.mcp_server.guideline_store import GuidelineStore
from uwcopilot.mcp_server.server import ServerDependencies, build_server

TRANSPORTS = ("stdio", "streamable-http")
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8765
DEFAULT_HTTP_PATH = "/mcp"
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class UnsafeHostError(ValueError):
    """Raised when the HTTP transport would listen on a non-loopback address without --unsafe-allow-remote."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(prog="python -m uwcopilot.mcp_server", description=__doc__.split("\n")[0])
    parser.add_argument("--transport", choices=TRANSPORTS, default="stdio", help="default: stdio")
    parser.add_argument("--host", default=DEFAULT_HTTP_HOST, help="HTTP only; default 127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT, help=f"HTTP only; default {DEFAULT_HTTP_PORT}")
    parser.add_argument("--path", default=DEFAULT_HTTP_PATH, help=f"HTTP only; default {DEFAULT_HTTP_PATH}")
    parser.add_argument(
        "--unsafe-allow-remote",
        action="store_true",
        help="HTTP only: allow a non-loopback --host. The server has no authentication.",
    )
    parser.add_argument("--log-level", choices=LOG_LEVELS, default="WARNING", help="stderr log level")
    return parser.parse_args(argv)


def is_loopback(host: str) -> bool:
    """True for localhost and loopback IP addresses (127.0.0.0/8, ::1)."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_http_host(host: str, allow_remote: bool) -> None:
    """Refuse a non-loopback HTTP host unless explicitly allowed."""
    if not allow_remote and not is_loopback(host):
        raise UnsafeHostError(
            f"Refusing to serve MCP over HTTP on {host!r}: the server has no authentication. Use 127.0.0.1, or pass "
            "--unsafe-allow-remote and put an authenticating proxy in front of it."
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the server until the client disconnects (stdio) or the process is stopped (HTTP)."""
    args = parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=args.log_level, format="%(levelname)s %(name)s: %(message)s")
    if args.transport == "streamable-http":
        try:
            check_http_host(args.host, args.unsafe_allow_remote)
        except UnsafeHostError as error:
            print(error, file=sys.stderr)
            return 2
    # The guidelines plus the SYNTHETIC lending programs, so [NSP-x.y] citations in startup memos resolve.
    dependencies = ServerDependencies(guideline_store=GuidelineStore(programs_dir=PROGRAMS_DIR))
    server = build_server(dependencies, log_level=args.log_level)
    try:
        if args.transport == "stdio":
            server.run("stdio")
        else:
            server.run("streamable-http", host=args.host, port=args.port, streamable_http_path=args.path)
    finally:
        dependencies.close()
    return 0
