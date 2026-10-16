"""DuckDB with HTTPS access (the httpfs extension), set up for this machine's proxy and CA bundle.

Why DuckDB: Overture publishes the places theme as Parquet files on S3 (about 11 GB per release). DuckDB reads only
the columns and the row groups a query needs, using each row group's min/max statistics of the bbox columns
("predicate pushdown"), so one metro's places are read without downloading the whole release (how long that takes
is not measured by a script).

Setup, step by step (a step is skipped when it is not needed):
1. LOAD httpfs. If it is not installed, INSTALL it from DuckDB's extension repository.
2. If that download fails (DuckDB's installer uses plain HTTP, which some networks, including this build
   environment's proxy, refuse), download the same extension file from the repository over HTTPS with httpx
   (trust_env=True, so the proxy and CA bundle apply) and INSTALL it from the local file. DuckDB checks an
   extension's signature when it loads it and refuses unsigned extensions by default, so a changed file is not
   loaded.
3. When HTTPS_PROXY (or https_proxy) is set, its host and port are passed to DuckDB's http_proxy setting, and a
   user name and password in it, if any, to http_proxy_username / http_proxy_password. The values are never
   logged or stored.
4. When a CA bundle variable is set (SSL_CERT_FILE, REQUESTS_CA_BUNDLE or CURL_CA_BUNDLE), it is passed as
   ca_cert_file, so an intercepting proxy's certificate is trusted the same way httpx trusts it.

Memory and threads are capped (MEMORY_LIMIT, THREADS: design choices for a shared 4-CPU machine).
"""

import gzip
import logging
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

import duckdb
import httpx

from uwcopilot.studio.data.paths import STUDIO_DATA_DIR

LOGGER = logging.getLogger(__name__)
EXTENSION_REPOSITORY = "https://extensions.duckdb.org"
EXTENSIONS_DIR = STUDIO_DATA_DIR / "duckdb_extensions"
CA_BUNDLE_VARIABLES: tuple[str, ...] = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
PROXY_VARIABLES: tuple[str, ...] = ("HTTPS_PROXY", "https_proxy")
# Design choices: the machine is shared with other jobs, so a query may not take all of it.
MEMORY_LIMIT = "1GB"
THREADS = 2
DOWNLOAD_TIMEOUT_S = 120.0


def connect(https: bool) -> duckdb.DuckDBPyConnection:
    """An in-memory DuckDB connection; with https=True, httpfs is loaded and the proxy and CA bundle are set."""
    connection = duckdb.connect()
    connection.execute(f"SET memory_limit='{MEMORY_LIMIT}'")
    connection.execute(f"SET threads={THREADS}")
    if https:
        load_httpfs(connection)
        apply_proxy(connection)
        apply_ca_bundle(connection)
    return connection


def load_httpfs(connection: duckdb.DuckDBPyConnection) -> None:
    """Load httpfs, installing it first (from the repository, or from a file fetched over HTTPS) when needed."""
    try:
        connection.execute("LOAD httpfs")
        return
    except duckdb.Error:
        LOGGER.info("httpfs is not installed; installing it")
    try:
        connection.execute("INSTALL httpfs")
    except duckdb.Error as exc:
        LOGGER.warning("INSTALL httpfs failed (%s); fetching the extension file over HTTPS", type(exc).__name__)
        local_file = download_extension(connection, "httpfs")
        connection.execute(f"INSTALL '{local_file.as_posix()}'")
    connection.execute("LOAD httpfs")


def extension_url(version: str, platform: str, name: str) -> str:
    """The repository URL of one extension file (gzip-compressed)."""
    return f"{EXTENSION_REPOSITORY}/v{version}/{platform}/{name}.duckdb_extension.gz"


def download_extension(connection: duckdb.DuckDBPyConnection, name: str) -> Path:
    """Fetch an extension file over HTTPS (proxy-aware) into data/studio/duckdb_extensions and return its path."""
    platform = str(connection.execute("PRAGMA platform").fetchone()[0])
    version = duckdb.__version__
    target = EXTENSIONS_DIR / version / platform / f"{name}.duckdb_extension"
    if target.is_file():
        return target
    url = extension_url(version, platform, name)
    with httpx.Client(timeout=DOWNLOAD_TIMEOUT_S, trust_env=True, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gzip.decompress(response.content))
    return target


def proxy_parts(value: str) -> tuple[str, str | None, str | None]:
    """('host:port', user or None, password or None) from a proxy URL such as http://user:pw@host:3128."""
    parts = urlsplit(value if "://" in value else f"http://{value}")
    if not parts.hostname:
        raise ValueError("the proxy variable has no host")
    address = f"{parts.hostname}:{parts.port}" if parts.port else parts.hostname
    user = unquote(parts.username) if parts.username else None
    password = unquote(parts.password) if parts.password else None
    return address, user, password


def apply_proxy(connection: duckdb.DuckDBPyConnection) -> None:
    """Pass the HTTPS proxy (if one is set) to DuckDB. Nothing about it is logged."""
    value = next((os.environ[name] for name in PROXY_VARIABLES if os.environ.get(name)), None)
    if not value:
        return
    address, user, password = proxy_parts(value)
    connection.execute("SET http_proxy=?", [address])
    if user:
        connection.execute("SET http_proxy_username=?", [user])
        connection.execute("SET http_proxy_password=?", [password or ""])


def apply_ca_bundle(connection: duckdb.DuckDBPyConnection) -> None:
    """Pass the first CA bundle variable that points to a file to DuckDB."""
    for name in CA_BUNDLE_VARIABLES:
        path = os.environ.get(name)
        if path and Path(path).is_file():
            connection.execute("SET ca_cert_file=?", [path])
            return
