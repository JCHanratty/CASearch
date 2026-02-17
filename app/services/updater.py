"""Updater service - checks for updates and downloads index from GitHub releases."""

import json
import tempfile
import zipfile
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.settings import settings


class UpdateError(Exception):
    """Raised when an update operation fails."""
    pass


def check_for_update() -> dict:
    """
    Check GitHub releases for the latest version.

    Returns:
        dict with release info including tag_name and assets list

    Raises:
        UpdateError: If the API call fails
    """
    url = f"https://api.github.com/repos/{settings.GITHUB_REPO}/releases/latest"

    try:
        req = Request(url, headers={"Accept": "application/vnd.github.v3+json"})
        with urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data
    except HTTPError as e:
        if e.code == 404:
            raise UpdateError(f"No releases found for {settings.GITHUB_REPO}")
        raise UpdateError(f"GitHub API error: {e.code} {e.reason}")
    except URLError as e:
        raise UpdateError(f"Network error checking for updates: {e.reason}")
    except json.JSONDecodeError as e:
        raise UpdateError(f"Invalid JSON response from GitHub: {e}")


def parse_version(version_str: str) -> tuple:
    """
    Parse version string into comparable tuple.

    Args:
        version_str: Version like "1.0.0" or "v1.0.0"

    Returns:
        Tuple of integers for comparison
    """
    # Remove 'v' prefix if present
    clean = version_str.lstrip("v")
    try:
        parts = [int(p) for p in clean.split(".")]
        # Pad to 3 parts
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts[:3])
    except ValueError:
        return (0, 0, 0)


def is_newer_version(remote_tag: str, local_version: str) -> bool:
    """
    Compare versions to check if remote is newer.

    Args:
        remote_tag: Version tag from GitHub (e.g., "v1.1.0")
        local_version: Local version string (e.g., "1.0.0")

    Returns:
        True if remote is newer than local
    """
    remote = parse_version(remote_tag)
    local = parse_version(local_version)
    return remote > local


def find_index_asset(release: dict, pattern: str = "index") -> Optional[dict]:
    """
    Find the index asset in release assets.

    Args:
        release: Release dict from GitHub API
        pattern: Pattern to match in asset name

    Returns:
        Asset dict or None if not found
    """
    assets = release.get("assets", [])
    for asset in assets:
        name = asset.get("name", "")
        if pattern in name.lower() and name.endswith(".zip"):
            return asset
    return None


def download_index_asset(asset: dict, dest_dir: Optional[Path] = None) -> Path:
    """
    Download an index asset from GitHub release.

    Args:
        asset: Asset dict with browser_download_url
        dest_dir: Directory to save file (uses temp if None)

    Returns:
        Path to downloaded file

    Raises:
        UpdateError: If download fails
    """
    url = asset.get("browser_download_url")
    if not url:
        raise UpdateError("Asset has no download URL")

    name = asset.get("name", "index.zip")

    if dest_dir is None:
        dest_dir = Path(tempfile.mkdtemp())
    else:
        dest_dir.mkdir(parents=True, exist_ok=True)

    dest_path = dest_dir / name

    try:
        req = Request(url, headers={"Accept": "application/octet-stream"})
        with urlopen(req, timeout=300) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) == 0:
                raise UpdateError("Asset file is empty")

            with open(dest_path, "wb") as f:
                # Read in chunks for large files
                chunk_size = 8192
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)

        # Verify file was written
        if not dest_path.exists() or dest_path.stat().st_size == 0:
            raise UpdateError("Downloaded file is empty or missing")

        return dest_path

    except HTTPError as e:
        raise UpdateError(f"Download failed: {e.code} {e.reason}")
    except URLError as e:
        raise UpdateError(f"Network error during download: {e.reason}")


def apply_index_update(zip_path: Path, dest_dir: Path) -> bool:
    """
    Extract index zip to destination directory.

    Args:
        zip_path: Path to the downloaded zip file
        dest_dir: Directory to extract to

    Returns:
        True if extraction succeeded

    Raises:
        UpdateError: If extraction fails
    """
    if not zip_path.exists():
        raise UpdateError(f"Zip file not found: {zip_path}")

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path, "r") as zf:
            # Security check: no path traversal
            for member in zf.namelist():
                if member.startswith("/") or ".." in member:
                    raise UpdateError(f"Invalid path in zip: {member}")

            zf.extractall(dest_dir)

        return True

    except zipfile.BadZipFile:
        raise UpdateError("Downloaded file is not a valid zip")
    except Exception as e:
        raise UpdateError(f"Extraction failed: {e}")


def ensure_latest_index() -> dict:
    """
    Check for updates and apply if available.

    Returns:
        dict with status info: checked, updated, version, error
    """
    result = {
        "checked": False,
        "updated": False,
        "current_version": settings.APP_VERSION,
        "latest_version": None,
        "error": None,
    }

    if not settings.AUTO_UPDATE_ENABLED:
        result["error"] = "Auto-update is disabled"
        return result

    try:
        # Check for latest release
        print(f"[Updater] Checking for updates from {settings.GITHUB_REPO}...")
        release = check_for_update()
        result["checked"] = True

        tag = release.get("tag_name", "")
        result["latest_version"] = tag

        # Compare versions
        if not is_newer_version(tag, settings.APP_VERSION):
            print(f"[Updater] Already up to date (v{settings.APP_VERSION})")
            return result

        print(f"[Updater] New version available: {tag}")

        # Find and download index asset
        asset = find_index_asset(release)
        if not asset:
            print("[Updater] No index asset found in release")
            result["error"] = "No index asset in release"
            return result

        print(f"[Updater] Downloading {asset.get('name')}...")
        zip_path = download_index_asset(asset)

        # Apply update
        print(f"[Updater] Extracting to {settings.INDEX_DIR}...")
        apply_index_update(zip_path, settings.INDEX_DIR)

        # Cleanup temp file
        try:
            zip_path.unlink()
        except Exception:
            pass

        result["updated"] = True
        print(f"[Updater] Successfully updated to {tag}")

    except UpdateError as e:
        result["error"] = str(e)
        print(f"[Updater] Update check failed: {e}")
    except Exception as e:
        result["error"] = f"Unexpected error: {e}"
        print(f"[Updater] Unexpected error: {e}")

    return result


async def async_ensure_latest_index() -> dict:
    """
    Async wrapper for ensure_latest_index.

    This runs the sync function but allows it to be called from async context.
    For true async, would need aiohttp or similar (avoided per requirements).
    """
    return ensure_latest_index()
