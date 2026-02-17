"""App update checker — queries GitHub releases for new versions."""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.settings import settings
from app.services.updater import parse_version, is_newer_version


def check_for_update(current_version: str) -> dict:
    """
    Check GitHub releases for a newer app version.

    Fetches the releases list (not /latest, which is unreliable with
    force-pushed tags) and finds the highest semver among published releases.

    Returns:
        dict with keys: available, current_version, latest_version,
        download_url, release_notes, html_url, error
    """
    result = {
        "available": False,
        "current_version": current_version,
        "latest_version": None,
        "download_url": None,
        "release_notes": None,
        "html_url": None,
        "error": None,
    }

    try:
        url = f"https://api.github.com/repos/{settings.GITHUB_REPO}/releases?per_page=20"
        req = Request(url, headers={"Accept": "application/vnd.github.v3+json"})

        with urlopen(req, timeout=15) as response:
            releases = json.loads(response.read().decode("utf-8"))

        if not releases:
            return result

        # Find highest version among non-draft, non-prerelease entries
        best_release = None
        best_version = parse_version(current_version)

        for release in releases:
            if release.get("draft") or release.get("prerelease"):
                continue

            tag = release.get("tag_name", "")
            release_ver = parse_version(tag)

            if release_ver > best_version:
                best_version = release_ver
                best_release = release

        if best_release is None:
            return result

        result["available"] = True
        result["latest_version"] = best_release.get("tag_name", "")
        result["release_notes"] = best_release.get("body", "")
        result["html_url"] = best_release.get("html_url", "")

        # Find Windows zip asset
        for asset in best_release.get("assets", []):
            name = asset.get("name", "")
            if "casearch-windows" in name.lower() and name.endswith(".zip"):
                result["download_url"] = asset.get("browser_download_url")
                break

    except (HTTPError, URLError, json.JSONDecodeError, Exception) as e:
        result["error"] = str(e)

    return result
