"""Tests for the updater service."""

import io
import json
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.updater import (
    UpdateError,
    apply_index_update,
    check_for_update,
    download_index_asset,
    find_index_asset,
    is_newer_version,
    parse_version,
    ensure_latest_index,
)


# --- Version parsing tests ---

def test_parse_version_simple():
    """Test parsing simple version string."""
    assert parse_version("1.0.0") == (1, 0, 0)
    assert parse_version("2.1.3") == (2, 1, 3)


def test_parse_version_with_v_prefix():
    """Test parsing version with v prefix."""
    assert parse_version("v1.0.0") == (1, 0, 0)
    assert parse_version("v2.1.3") == (2, 1, 3)


def test_parse_version_short():
    """Test parsing short version strings."""
    assert parse_version("1.0") == (1, 0, 0)
    assert parse_version("2") == (2, 0, 0)


def test_parse_version_invalid():
    """Test parsing invalid version returns zeros."""
    assert parse_version("invalid") == (0, 0, 0)
    assert parse_version("") == (0, 0, 0)


def test_is_newer_version():
    """Test version comparison."""
    assert is_newer_version("v1.1.0", "1.0.0") is True
    assert is_newer_version("v2.0.0", "1.9.9") is True
    assert is_newer_version("v1.0.0", "1.0.0") is False
    assert is_newer_version("v1.0.0", "1.1.0") is False
    assert is_newer_version("v1.0.1", "1.0.0") is True


# --- Asset finding tests ---

def test_find_index_asset():
    """Test finding index asset in release."""
    release = {
        "assets": [
            {"name": "source.zip", "browser_download_url": "https://example.com/source.zip"},
            {"name": "index-v1.0.0.zip", "browser_download_url": "https://example.com/index.zip"},
        ]
    }
    asset = find_index_asset(release)
    assert asset is not None
    assert asset["name"] == "index-v1.0.0.zip"


def test_find_index_asset_not_found():
    """Test when no index asset exists."""
    release = {
        "assets": [
            {"name": "source.zip", "browser_download_url": "https://example.com/source.zip"},
        ]
    }
    asset = find_index_asset(release)
    assert asset is None


def test_find_index_asset_empty():
    """Test with empty assets."""
    release = {"assets": []}
    asset = find_index_asset(release)
    assert asset is None


# --- Mock GitHub API responses ---

MOCK_RELEASE_JSON = {
    "tag_name": "v1.1.0",
    "name": "Release 1.1.0",
    "assets": [
        {
            "name": "index-v1.1.0.zip",
            "browser_download_url": "https://github.com/test/repo/releases/download/v1.1.0/index-v1.1.0.zip",
            "size": 1024,
        }
    ],
}


def create_mock_zip_content():
    """Create a mock zip file in memory."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("metadata.json", '{"version": "1.1.0"}')
        zf.writestr("data/test.txt", "test content")
    buffer.seek(0)
    return buffer.read()


class MockResponse:
    """Mock HTTP response."""

    def __init__(self, data, status=200, headers=None):
        self.data = data if isinstance(data, bytes) else data.encode("utf-8")
        self.status = status
        self.headers = headers or {"Content-Length": str(len(self.data))}

    def read(self, size=None):
        if size is None:
            result = self.data
            self.data = b""
            return result
        result = self.data[:size]
        self.data = self.data[size:]
        return result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


# --- API call tests ---

def test_check_for_update_returns_release(monkeypatch):
    """Test check_for_update returns release dict with tag_name."""
    mock_response = MockResponse(json.dumps(MOCK_RELEASE_JSON))

    def mock_urlopen(request, timeout=None):
        return mock_response

    monkeypatch.setattr("app.services.updater.urlopen", mock_urlopen)

    result = check_for_update()

    assert "tag_name" in result
    assert result["tag_name"] == "v1.1.0"
    assert "assets" in result


def test_check_for_update_handles_404(monkeypatch):
    """Test check_for_update handles 404 error."""
    from urllib.error import HTTPError

    def mock_urlopen(request, timeout=None):
        raise HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr("app.services.updater.urlopen", mock_urlopen)

    with pytest.raises(UpdateError) as exc_info:
        check_for_update()

    assert "No releases found" in str(exc_info.value)


def test_check_for_update_handles_network_error(monkeypatch):
    """Test check_for_update handles network errors."""
    from urllib.error import URLError

    def mock_urlopen(request, timeout=None):
        raise URLError("Connection refused")

    monkeypatch.setattr("app.services.updater.urlopen", mock_urlopen)

    with pytest.raises(UpdateError) as exc_info:
        check_for_update()

    assert "Network error" in str(exc_info.value)


# --- Download tests ---

def test_download_index_asset(monkeypatch, tmp_path):
    """Test downloading an index asset."""
    zip_content = create_mock_zip_content()
    mock_response = MockResponse(zip_content)

    def mock_urlopen(request, timeout=None):
        return mock_response

    monkeypatch.setattr("app.services.updater.urlopen", mock_urlopen)

    asset = {
        "name": "index-v1.1.0.zip",
        "browser_download_url": "https://example.com/index.zip",
    }

    result_path = download_index_asset(asset, tmp_path)

    assert result_path.exists()
    assert result_path.name == "index-v1.1.0.zip"
    assert result_path.stat().st_size > 0


def test_download_index_asset_no_url():
    """Test download fails with no URL."""
    asset = {"name": "index.zip"}

    with pytest.raises(UpdateError) as exc_info:
        download_index_asset(asset)

    assert "no download URL" in str(exc_info.value)


# --- Apply update tests ---

def test_apply_index_update(tmp_path):
    """Test extracting index zip."""
    # Create a test zip file
    zip_path = tmp_path / "test.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("metadata.json", '{"version": "1.0.0"}')
        zf.writestr("data/file.txt", "content")

    dest_dir = tmp_path / "extracted"

    result = apply_index_update(zip_path, dest_dir)

    assert result is True
    assert (dest_dir / "metadata.json").exists()
    assert (dest_dir / "data" / "file.txt").exists()


def test_apply_index_update_invalid_zip(tmp_path):
    """Test apply fails with invalid zip."""
    zip_path = tmp_path / "invalid.zip"
    zip_path.write_text("not a zip file")

    dest_dir = tmp_path / "extracted"

    with pytest.raises(UpdateError) as exc_info:
        apply_index_update(zip_path, dest_dir)

    assert "not a valid zip" in str(exc_info.value)


def test_apply_index_update_path_traversal(tmp_path):
    """Test apply rejects path traversal attempts."""
    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        # This should be rejected
        zf.writestr("../../../etc/passwd", "evil content")

    dest_dir = tmp_path / "extracted"

    with pytest.raises(UpdateError) as exc_info:
        apply_index_update(zip_path, dest_dir)

    assert "Invalid path" in str(exc_info.value)


def test_apply_index_update_missing_file(tmp_path):
    """Test apply fails with missing zip file."""
    zip_path = tmp_path / "nonexistent.zip"
    dest_dir = tmp_path / "extracted"

    with pytest.raises(UpdateError) as exc_info:
        apply_index_update(zip_path, dest_dir)

    assert "not found" in str(exc_info.value)


# --- Integration tests ---

def test_ensure_latest_index_disabled(monkeypatch):
    """Test ensure_latest_index when updates are disabled."""
    monkeypatch.setattr("app.services.updater.settings.AUTO_UPDATE_ENABLED", False)

    result = ensure_latest_index()

    assert result["checked"] is False
    assert result["updated"] is False
    assert "disabled" in result["error"]


def test_ensure_latest_index_up_to_date(monkeypatch):
    """Test ensure_latest_index when already up to date."""
    monkeypatch.setattr("app.services.updater.settings.AUTO_UPDATE_ENABLED", True)
    monkeypatch.setattr("app.services.updater.settings.APP_VERSION", "1.1.0")

    mock_response = MockResponse(json.dumps(MOCK_RELEASE_JSON))

    def mock_urlopen(request, timeout=None):
        return mock_response

    monkeypatch.setattr("app.services.updater.urlopen", mock_urlopen)

    result = ensure_latest_index()

    assert result["checked"] is True
    assert result["updated"] is False
    assert result["latest_version"] == "v1.1.0"


def test_ensure_latest_index_downloads_update(monkeypatch, tmp_path):
    """Test ensure_latest_index downloads and applies update."""
    monkeypatch.setattr("app.services.updater.settings.AUTO_UPDATE_ENABLED", True)
    monkeypatch.setattr("app.services.updater.settings.APP_VERSION", "1.0.0")
    monkeypatch.setattr("app.services.updater.settings.INDEX_DIR", tmp_path / "index")

    zip_content = create_mock_zip_content()
    call_count = [0]

    def mock_urlopen(request, timeout=None):
        call_count[0] += 1
        if call_count[0] == 1:
            # First call: release info
            return MockResponse(json.dumps(MOCK_RELEASE_JSON))
        else:
            # Second call: download
            return MockResponse(zip_content)

    monkeypatch.setattr("app.services.updater.urlopen", mock_urlopen)

    result = ensure_latest_index()

    assert result["checked"] is True
    assert result["updated"] is True
    assert result["latest_version"] == "v1.1.0"
    assert (tmp_path / "index" / "metadata.json").exists()
