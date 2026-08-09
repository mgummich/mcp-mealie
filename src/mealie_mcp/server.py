"""FastMCP server wiring: config, lifespan, startup probe, tool registration."""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from .client import MealieClient
from .config import Config, ConfigError
from .tools import admin, cookbooks, library, mealplan, recipes

log = logging.getLogger("mealie_mcp")

MIN_MEALIE_MAJOR = 2

_client: MealieClient | None = None


def get_client() -> MealieClient:
    """Return the process-wide client created by the server lifespan.

    Tool modules receive this function, not the client itself, because the
    client only exists while the lifespan is running.

    Returns:
        The live MealieClient.

    Raises:
        RuntimeError: If called outside a running server lifespan.
    """
    if _client is None:  # pragma: no cover - only reachable on misuse
        raise RuntimeError("client is not initialized; the server lifespan did not run")
    return _client


def configure_logging(level: str) -> None:
    """Log to stderr only. Under stdio transport, stdout is the protocol.

    Args:
        level: Logging level name, e.g. "INFO"; unknown names fall back
            to INFO.
    """
    logging.basicConfig(
        stream=sys.stderr,
        level=getattr(logging, level, logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )


async def probe(client: MealieClient) -> tuple[str, str]:
    """Confirm the instance is reachable, modern enough, and that the token works.

    /api/app/about needs no authentication, so it proves reachability and
    version only — /api/users/self is what actually validates the token.

    Args:
        client: The client to probe with.

    Returns:
        A tuple of (Mealie version, authenticated username) for logging.

    Raises:
        ConfigError: If the Mealie version is older than 2.0.
        ToolError: If the instance is unreachable or the token is rejected.
    """
    about = await client.request("GET", "/api/app/about")
    version = str((about or {}).get("version") or "unknown")

    major = version.lstrip("v").split(".")[0]
    if major.isdigit() and int(major) < MIN_MEALIE_MAJOR:
        raise ConfigError(
            f"Mealie {version} is not supported — this server needs 2.0 or newer "
            "(1.x has no /api/households endpoints)"
        )

    me = await client.request("GET", "/api/users/self")
    username = (me or {}).get("username") or (me or {}).get("email") or "unknown user"
    return version, username


def build_server(config: Config) -> FastMCP:
    """Assemble the FastMCP server: lifespan-managed client, tools registered.

    Args:
        config: Validated configuration; read_only controls whether write
            tools are registered at all.

    Returns:
        The server, ready for .run() or an in-process fastmcp.Client.
    """

    @asynccontextmanager
    async def lifespan(_server: FastMCP):
        global _client
        _client = MealieClient(config.url, config.token, verify_ssl=config.verify_ssl)
        try:
            # main() already probed before the transport started; no need again.
            yield
        finally:
            await _client.aclose()
            _client = None

    mcp = FastMCP(name="mealie", lifespan=lifespan)
    for module in (recipes, mealplan, cookbooks, library, admin):
        module.register(mcp, get_client, config.read_only)
    return mcp


def main() -> None:
    """Entry point for the mcp-mealie console script.

    Validates the environment and probes Mealie before starting the stdio
    transport, so a misconfiguration surfaces as one clear message on stderr
    and exit code 2 instead of every tool call failing separately.
    """
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"mcp-mealie: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    configure_logging(config.log_level)

    # Fail before the transport starts, so the client shows one clear error
    # instead of every tool call failing separately.
    try:
        asyncio.run(_verify(config))
    except (ConfigError, ToolError) as exc:
        print(f"mcp-mealie: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except (httpx.HTTPError, OSError) as exc:
        print(f"mcp-mealie: cannot reach Mealie at {config.url}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    build_server(config).run()


async def _verify(config: Config) -> None:
    client = MealieClient(config.url, config.token, verify_ssl=config.verify_ssl)
    try:
        version, username = await probe(client)
        log.info("connected to Mealie %s as %s", version, username)
        if config.read_only:
            log.info("read-only mode: write tools are not registered")
    finally:
        await client.aclose()


if __name__ == "__main__":  # pragma: no cover
    main()
