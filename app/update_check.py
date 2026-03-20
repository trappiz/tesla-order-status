#!/usr/bin/env python3
# coding: utf-8
"""Shared update helpers for startup checks and explicit release actions."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import traceback
import webbrowser
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse

import requests

from app.config import (
    APP_DIR,
    BASE_DIR,
    PUBLIC_DIR,
    TESLA_STORES_FILE,
    VERSION,
    cfg as Config,
)
from app.utils.colors import color_text
from app.utils.connection import request_with_retry
from app.utils.locale import t

FILES_TO_CHECK: List[Path] = [
    BASE_DIR / "tesla_order_status.py",
    TESLA_STORES_FILE,
    PUBLIC_DIR / "lang" / "de.json",
    PUBLIC_DIR / "lang" / "en.json",
    PUBLIC_DIR / "lang" / "pl.json",
    PUBLIC_DIR / "lang" / "sv.json",
    APP_DIR / "config.py",
    APP_DIR / "update.py",
    APP_DIR / "utils" / "auth.py",
    APP_DIR / "utils" / "colors.py",
    APP_DIR / "utils" / "connection.py",
    APP_DIR / "utils" / "helpers.py",
    APP_DIR / "utils" / "history.py",
    APP_DIR / "utils" / "orders.py",
    APP_DIR / "utils" / "params.py",
    APP_DIR / "utils" / "timeline.py",
]

DOWNLOAD_TIMEOUT = 30
MAX_REDIRECTS = 5
RELEASE_API_URL = (
    "https://api.github.com/repos/trappiz/tesla-order-status/releases/latest"
)
RELEASE_PAGE_URL = "https://github.com/trappiz/tesla-order-status/releases/latest"
ISSUES_URL = "https://github.com/trappiz/tesla-order-status/issues"
GITHUB_DOWNLOAD_ALLOWED_HOSTS = {
    "api.github.com",
    "github.com",
    "codeload.github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}


def _copytree_compat(src: Path, dst: Path) -> None:
    if sys.version_info >= (3, 8):
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return

    if dst.exists() and not dst.is_dir():
        raise ValueError(f"Target path {dst} exists and is not a directory")
    dst.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        target = dst / child.name
        if child.is_dir():
            _copytree_compat(child, target)
        else:
            shutil.copy2(child, target)


def _parse_version(tag: str) -> Optional[Tuple[int, ...]]:
    if not isinstance(tag, str):
        return None
    text = tag.strip()
    if not text:
        return None
    if text[0] in {"v", "V", "p", "P"}:
        text = text[1:]
    parts = text.split(".")
    parsed: List[int] = []
    for part in parts:
        if not part.isdigit():
            return None
        parsed.append(int(part))
    return tuple(parsed)


def _is_newer_version(candidate: str, current: str) -> bool:
    candidate_version = _parse_version(candidate)
    current_version = _parse_version(current)
    if candidate_version is None or current_version is None:
        return candidate.strip() != current.strip()
    return candidate_version > current_version


def _get_latest_release() -> Dict[str, Any]:
    response = request_with_retry(
        RELEASE_API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        exit_on_error=False,
        network_scope="update",
    )
    if response is None:
        raise RuntimeError("Could not load latest release metadata")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Invalid latest release metadata")
    return payload


def _open_release_page() -> None:
    try:
        opened = webbrowser.open(RELEASE_PAGE_URL)
        if not opened:
            print(t("Could not open browser. Please visit the following URL manually:"))
            print(RELEASE_PAGE_URL)
    except Exception:
        print(t("Could not open browser. Please visit the following URL manually:"))
        print(RELEASE_PAGE_URL)


def _missing_files() -> List[Path]:
    return [path for path in FILES_TO_CHECK if not path.exists() or not path.is_file()]


def _status_mode_enabled() -> bool:
    from app.utils.params import STATUS_MODE

    return STATUS_MODE


def _update_mode_value() -> Optional[str]:
    from app.utils.params import UPDATE_MODE

    return UPDATE_MODE


def _update_sha256_value() -> Optional[str]:
    from app.utils.params import UPDATE_SHA256

    return UPDATE_SHA256


def ask_for_update() -> int:
    if _status_mode_enabled():
        print(2)
        sys.exit()

    if Config.get("update_method") == "automatically":
        Config.set("update_method", "manual")
        print(
            color_text(
                t(
                    "Automatic in-place updates have been disabled for security reasons."
                ),
                "93",
            )
        )

    print(color_text(t("[UPDATE AVAILABLE]"), "93"))
    print(
        t(
            "Updates are applied only from a locally downloaded archive that you verify first."
        )
    )
    answer = input(t("Open the GitHub releases page now? (y/n): ")).strip().lower()
    if answer == "y":
        _open_release_page()
    return 1


def ask_for_update_consent() -> None:
    print(color_text(t("New Feature: Update Settings"), "93"))
    print(color_text(t("Please select how you want to handle updates:"), "93"))
    print(
        color_text(
            t(
                "- [m]anual update notifications: You will be told when a new verified release is available."
            ),
            "93",
        )
    )
    print(
        color_text(
            t("- [b]lock updates: Update checks will be disabled completely"), "93"
        )
    )
    print(
        color_text(
            t(
                'You can change your mind everytime by removing "update_method" from your "data/private/settings.json":'
            ),
            "93",
        )
    )
    consent = input(t("Please choose an option (m/b): ")).strip().lower()

    if consent == "b":
        Config.set("update_method", "block")
    else:
        Config.set("update_method", "manual")


def check_for_updates(respect_preferences: bool = True) -> int:
    status_mode = _status_mode_enabled()
    missing = _missing_files()
    if missing:
        if status_mode:
            print(2)
            sys.exit()
        print(t("[PACKAGE CORRUPT]"))
        print(
            t(
                "Your Project is missing some files. Please restore the complete local project or apply a verified update archive."
            )
        )
        for path in missing:
            print(t("[WARN] File missing: {path}").format(path=path))
        return ask_for_update()

    if respect_preferences:
        if not Config.has("update_method") or Config.get("update_method") == "":
            if status_mode:
                print(2)
                sys.exit()
            ask_for_update_consent()

        if Config.get("update_method") == "automatically":
            Config.set("update_method", "manual")
            if not status_mode:
                print(
                    color_text(
                        t(
                            "Automatic in-place updates have been disabled for security reasons."
                        ),
                        "93",
                    )
                )

        if Config.get("update_method") == "block":
            return 0

    try:
        latest_release = _get_latest_release()
    except Exception as error:
        if status_mode:
            print(-1)
            sys.exit()
        print(
            t("[ERROR] Could not load latest release information: {error}").format(
                error=error
            ),
            file=sys.stderr,
        )
        return 2

    latest_tag = str(latest_release.get("tag_name") or "").strip()
    if latest_tag and _is_newer_version(latest_tag, VERSION):
        if not status_mode:
            release_name = latest_release.get("name") or latest_tag
            print(t("Latest Release: {release}").format(release=release_name))
        return ask_for_update()

    if not status_mode:
        print(color_text(t("You are already on the latest version."), "92"))

    return 0


def maybe_run_update_from_main_cli() -> bool:
    update_value = _update_mode_value()
    if update_value is None:
        return False

    if update_value == "":
        raise SystemExit(cli_main([]))

    argv = ["--apply", update_value]
    sha256_value = _update_sha256_value()
    if sha256_value:
        argv.extend(["--sha256", sha256_value])
    raise SystemExit(cli_main(argv))


def _is_allowed_download_url(url: str) -> bool:
    hostname = (urlparse(url).hostname or "").lower()
    return hostname in GITHUB_DOWNLOAD_ALLOWED_HOSTS


def _sanitize_tag(tag: str) -> str:
    allowed = [
        char if char.isalnum() or char in {".", "-", "_"} else "-" for char in tag
    ]
    sanitized = "".join(allowed).strip("-")
    return sanitized or "latest"


def _select_release_archive(release: Dict[str, Any]) -> Optional[Dict[str, str]]:
    assets = release.get("assets")
    if isinstance(assets, list):
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "").strip()
            url = str(asset.get("browser_download_url") or "").strip()
            if name.endswith(".zip") and url:
                return {"name": name, "url": url}

    zipball_url = str(release.get("zipball_url") or "").strip()
    if not zipball_url:
        return None
    tag = _sanitize_tag(str(release.get("tag_name") or "latest"))
    return {"name": f"tesla-order-status-{tag}.zip", "url": zipball_url}


def _select_release_checksum_asset(
    release: Dict[str, Any], archive_name: str
) -> Optional[Dict[str, str]]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        return None

    checksum_assets = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "").strip()
        url = str(asset.get("browser_download_url") or "").strip()
        if name.endswith((".sha256", ".sha256sum")) and url:
            checksum_assets.append({"name": name, "url": url})

    for asset in checksum_assets:
        if asset["name"].startswith(archive_name):
            return asset

    if len(checksum_assets) == 1:
        return checksum_assets[0]
    return None


def _download_url_to_file(url: str, destination: Path) -> Path:
    session = requests.Session()
    current_url = url

    for _ in range(MAX_REDIRECTS + 1):
        if not _is_allowed_download_url(current_url):
            raise ValueError(f"Blocked download host: {current_url}")

        response = session.get(
            current_url,
            timeout=DOWNLOAD_TIMEOUT,
            verify=True,
            allow_redirects=False,
            stream=True,
            headers={"Accept": "application/octet-stream"},
        )

        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise ValueError("Redirect response did not include a Location header")
            current_url = urljoin(current_url, location)
            continue

        response.raise_for_status()
        if not _is_allowed_download_url(response.url):
            response.close()
            raise ValueError(f"Blocked final download host: {response.url}")

        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp_destination = destination.with_suffix(destination.suffix + ".tmp")
        with tmp_destination.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
        response.close()
        tmp_destination.replace(destination)
        return destination

    raise ValueError("Too many redirects while downloading the release archive")


def download_latest_release(
    destination_dir: Path = BASE_DIR,
) -> Tuple[Path, Optional[Path]]:
    release = _get_latest_release()
    archive = _select_release_archive(release)
    if archive is None:
        raise ValueError("No downloadable release archive found")

    archive_path = destination_dir / archive["name"]
    _download_url_to_file(archive["url"], archive_path)

    checksum_info = _select_release_checksum_asset(release, archive["name"])
    if checksum_info is None:
        return archive_path, None

    checksum_path = destination_dir / checksum_info["name"]
    _download_url_to_file(checksum_info["url"], checksum_path)
    return archive_path, checksum_path


def _is_within_directory(root: Path, candidate: Path) -> bool:
    try:
        return os.path.commonpath([str(root), str(candidate)]) == str(root)
    except ValueError:
        return False


def _is_symlink(member: zipfile.ZipInfo) -> bool:
    return ((member.external_attr >> 16) & 0o170000) == 0o120000


def _safe_extract_zip(zf: zipfile.ZipFile, target_dir: Path) -> None:
    root = target_dir.resolve()
    for member in zf.infolist():
        if not member.filename:
            continue
        target_path = (target_dir / member.filename).resolve()
        if not _is_within_directory(root, target_path):
            raise ValueError(f"Unsafe archive entry: {member.filename}")
        if _is_symlink(member):
            raise ValueError(f"Symlink entries are not allowed: {member.filename}")
        if member.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as source, open(target_path, "wb") as destination:
            shutil.copyfileobj(source, destination)


def _prompt_archive_path() -> Path:
    archive_path = input("Enter the path to a local release ZIP: ").strip()
    if not archive_path:
        raise ValueError("No archive path provided")
    return Path(archive_path).expanduser()


def _compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_sha256(text: str, archive_name: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) == 1 and len(parts[0]) == 64:
            return parts[0].lower()
        if len(parts) >= 2 and len(parts[0]) == 64:
            candidate_name = parts[-1].lstrip("*./")
            if candidate_name == archive_name:
                return parts[0].lower()
    raise ValueError("No valid SHA-256 checksum found")


def _get_expected_sha256(archive_path: Path, provided: Optional[str] = None) -> str:
    if provided is not None:
        normalized = provided.strip().lower()
        if len(normalized) == 64:
            return normalized
        raise ValueError("Provided SHA-256 checksum must be a 64-character hex string")

    sidecar_candidates = [
        archive_path.with_suffix(archive_path.suffix + ".sha256"),
        archive_path.with_suffix(".sha256"),
        archive_path.with_suffix(archive_path.suffix + ".sha256sum"),
    ]
    for sidecar in sidecar_candidates:
        if sidecar.exists() and sidecar.is_file():
            return _extract_sha256(
                sidecar.read_text(encoding="utf-8"), archive_path.name
            )

    prompt = (
        "Enter the expected SHA-256 checksum for this archive "
        "(required, 64 hex chars): "
    )
    normalized = input(prompt).strip().lower()
    if len(normalized) == 64:
        return normalized
    raise ValueError("A valid SHA-256 checksum is required to apply the update archive")


def apply_release_archive(
    archive_path: Path,
    expected_sha256: Optional[str] = None,
    target_dir: Path = BASE_DIR,
) -> None:
    archive_path = archive_path.expanduser().resolve()
    if not archive_path.exists() or not archive_path.is_file():
        raise FileNotFoundError(f"Archive not found: {archive_path}")

    expected = _get_expected_sha256(archive_path, expected_sha256)
    actual = _compute_sha256(archive_path)
    if actual != expected:
        raise ValueError(
            "Archive checksum mismatch. " f"Expected {expected}, got {actual}"
        )

    print("\nValidating and extracting archive...")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        with zipfile.ZipFile(archive_path) as zf:
            _safe_extract_zip(zf, tmp_path)

        extracted_dirs = [path for path in tmp_path.iterdir() if path.is_dir()]
        if len(extracted_dirs) != 1:
            raise ValueError("Expected a single top-level directory inside the archive")
        extracted_dir = extracted_dirs[0]
        for item in extracted_dir.iterdir():
            target = target_dir / item.name
            if item.is_dir():
                _copytree_compat(item, target)
            else:
                shutil.copy2(item, target)


def _print_apply_success() -> None:
    print("...Release update applied. Please rerun tesla_order_status.py")
    print(
        "\nIf the problem persists, please create an issue including the complete output of tesla_order_status.py"
    )
    print(f"GitHub Issues: {ISSUES_URL}")


def _print_apply_failure(script_name: str, error: Exception) -> None:
    print(f"...Release update failed: {error}\n")
    traceback.print_exc()
    print(
        f"\nIf the problem persists, please create an issue including the complete output of {script_name}"
    )
    print(f"GitHub Issues: {ISSUES_URL}")


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check for releases, download release archives, or apply a verified local update archive.",
    )
    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument(
        "--check",
        action="store_true",
        help="Check whether a newer trusted release is available.",
    )
    action_group.add_argument(
        "--download-latest",
        action="store_true",
        help="Download the latest release archive and optional checksum sidecar.",
    )
    action_group.add_argument(
        "--apply",
        metavar="ARCHIVE",
        help="Apply a verified local release ZIP archive.",
    )
    parser.add_argument(
        "--sha256",
        help="Expected SHA-256 checksum for --apply.",
    )
    return parser.parse_args(argv)


def _print_download_result(archive_path: Path, checksum_path: Optional[Path]) -> None:
    print(f"\nDownloaded latest release archive to: {archive_path}")
    if checksum_path is not None:
        print(f"Downloaded checksum file to: {checksum_path}")
        print(
            "Apply it with: python3 tesla_order_status.py --update " f'"{archive_path}"'
        )
        return
    print(
        "No release checksum file was found. The archive was downloaded but cannot be applied automatically."
    )
    print(
        "Verify the archive manually and rerun tesla_order_status.py --update with the expected SHA-256 checksum."
    )


def _interactive_update_flow() -> int:
    print(
        "This update flow can check for releases, download the latest release archive, or apply a local release ZIP archive."
    )
    print(f"Latest releases: {RELEASE_PAGE_URL}")

    choice = (
        input(
            "Choose action: [c] check for updates, [g] download latest release, [l] apply local ZIP, [q] quit: "
        )
        .strip()
        .lower()
    )

    if choice == "q":
        print("\nRelease update canceled...")
        return 1
    if choice == "c":
        return check_for_updates(respect_preferences=False)
    if choice == "g":
        archive_path, checksum_path = download_latest_release(BASE_DIR)
        _print_download_result(archive_path, checksum_path)
        if checksum_path is None:
            return 0
        answer = input("Apply the downloaded archive now? (y/n): ").strip().lower()
        if answer != "y":
            return 0
        apply_release_archive(archive_path)
        _print_apply_success()
        return 0
    if choice == "l":
        apply_release_archive(_prompt_archive_path())
        _print_apply_success()
        return 0

    print("\nRelease update canceled...")
    return 1


def cli_main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)

    try:
        if args.check:
            return check_for_updates(respect_preferences=False)

        if args.download_latest:
            archive_path, checksum_path = download_latest_release(BASE_DIR)
            _print_download_result(archive_path, checksum_path)
            return 0

        if args.apply:
            apply_release_archive(Path(args.apply), args.sha256)
            _print_apply_success()
            return 0

        return _interactive_update_flow()

    except Exception as error:  # noqa: BLE001 - best effort, minimal deps
        _print_apply_failure("tesla_order_status.py --update", error)
        return 1


__all__ = [
    "FILES_TO_CHECK",
    "RELEASE_API_URL",
    "RELEASE_PAGE_URL",
    "ask_for_update",
    "ask_for_update_consent",
    "apply_release_archive",
    "check_for_updates",
    "cli_main",
    "download_latest_release",
    "maybe_run_update_from_main_cli",
    "_get_latest_release",
    "_is_allowed_download_url",
    "_is_newer_version",
    "_parse_version",
    "_safe_extract_zip",
    "_sanitize_tag",
    "_select_release_archive",
    "_select_release_checksum_asset",
]
