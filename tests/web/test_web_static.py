"""Tests for serving the static export: route-to-file fallback, MIME types, cache headers, CSP hashes, safety."""

import base64
import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from uwcopilot.web.app import create_app

INLINE_SCRIPT = 'self.__next_f.push([0,"boot"])'


def write(path: Path, text: str) -> None:
    """Create parent folders and write text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def export_dir(tmp_path: Path) -> Path:
    """A tiny fake Next.js export: trailingSlash: true layout (route/index.html), plus one route.html page."""
    out = tmp_path / "out"
    write(out / "index.html", f"<html><body>home<script>{INLINE_SCRIPT}</script></body></html>")
    write(out / "404.html", "<html><body>custom not found</body></html>")
    write(out / "studio" / "index.html", "<html><body>studio page</body></html>")
    write(out / "studio" / "index.txt", "0:route payload")
    write(out / "lender" / "file.html", "<html><body>lender file page</body></html>")
    write(out / "about" / "index.html", "<html><body>about page</body></html>")
    write(out / "_next" / "static" / "chunks" / "app-3f2a.js", "console.log('app')")
    write(out / "_next" / "static" / "css" / "app.css", "body{}")
    (out / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    write(tmp_path / "secret.txt", "outside the export")
    return out


@pytest.fixture
def site(make_settings, export_dir: Path) -> Iterator[TestClient]:
    """A client for an app whose static folder is the fake export."""
    with TestClient(create_app(make_settings(UWC_STATIC_DIR=str(export_dir)))) as test_client:
        yield test_client


def test_root_serves_index_with_a_csp_hash_for_its_inline_script(site: TestClient) -> None:
    response = site.get("/")
    assert response.status_code == 200 and "home" in response.text
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.headers["cache-control"] == "no-cache"
    digest = base64.b64encode(hashlib.sha256(INLINE_SCRIPT.encode()).digest()).decode()
    script_src = response.headers["content-security-policy"].split("script-src ")[1].split(";")[0]
    assert script_src == f"'self' 'sha256-{digest}'"


@pytest.mark.parametrize(
    ("path", "text"),
    [
        ("/studio/", "studio page"),
        ("/studio", "studio page"),
        ("/about/", "about page"),
        ("/lender/file", "lender file page"),
        ("/lender/file/", "lender file page"),
    ],
)
def test_routes_without_extension_find_their_html_file(site: TestClient, path: str, text: str) -> None:
    response = site.get(path)
    assert response.status_code == 200 and text in response.text


def test_hashed_assets_are_cached_for_a_year(site: TestClient) -> None:
    script = site.get("/_next/static/chunks/app-3f2a.js")
    assert script.status_code == 200
    assert script.headers["content-type"] == "text/javascript; charset=utf-8"
    assert script.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert site.get("/_next/static/css/app.css").headers["content-type"] == "text/css; charset=utf-8"


def test_other_files_get_their_type_and_revalidate(site: TestClient) -> None:
    payload = site.get("/studio/index.txt")
    assert payload.headers["content-type"] == "text/plain; charset=utf-8"
    assert payload.headers["cache-control"] == "no-cache"
    assert site.get("/favicon.ico").headers["content-type"] == "image/x-icon"


def test_unknown_paths_get_the_404_page(site: TestClient) -> None:
    for path in ("/no/such/page", "/missing.js", "/lender/file.json"):
        response = site.get(path)
        assert response.status_code == 404 and "custom not found" in response.text


@pytest.mark.parametrize("path", ["/../secret.txt", "/%2e%2e/secret.txt", "/lender/..%2f..%2fsecret.txt", "/.env"])
def test_paths_cannot_leave_the_export(site: TestClient, path: str) -> None:
    response = site.get(path)
    assert response.status_code == 404 and "outside the export" not in response.text


def test_symlinks_out_of_the_export_are_not_followed(site: TestClient, export_dir: Path) -> None:
    (export_dir / "leak.txt").symlink_to(export_dir.parent / "secret.txt")
    response = site.get("/leak.txt")
    assert response.status_code == 404 and "outside the export" not in response.text


def test_etag_revalidation_returns_304(site: TestClient) -> None:
    first = site.get("/about")
    second = site.get("/about", headers={"If-None-Match": first.headers["etag"]})
    assert second.status_code == 304 and second.content == b""
    assert second.headers["etag"] == first.headers["etag"]


def test_head_requests_have_headers_but_no_body(site: TestClient) -> None:
    response = site.head("/lender/file")
    assert response.status_code == 200 and response.content == b""
    assert response.headers["content-type"].startswith("text/html")


def test_missing_export_shows_a_not_built_page(client: TestClient) -> None:
    root = client.get("/")
    assert root.status_code == 200 and "frontend has not been built" in root.text
    assert client.get("/studio").status_code == 404
    assert client.get("/api/health").status_code == 200  # the API keeps working
