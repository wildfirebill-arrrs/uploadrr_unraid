from __future__ import annotations

import contextlib
import platform
import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

import niquests

from src.utils.console import log

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BIN_DIR = _PROJECT_ROOT / "bin"

_ASSET_MAP: dict[tuple[str, str], str] = {
    ("linux", "x86_64"): "BDInfo-linux-x64.tar.gz",
    ("linux", "aarch64"): "BDInfo-linux-arm64.tar.gz",
    ("linux", "arm64"): "BDInfo-linux-arm64.tar.gz",
    ("darwin", "x86_64"): "BDInfo-osx-x64.tar.gz",
    ("darwin", "arm64"): "BDInfo-osx-arm64.tar.gz",
    ("win32", "x86_64"): "BDInfo-win-x64.zip",
    ("win32", "amd64"): "BDInfo-win-x64.zip",
}


def get_bdinfo_path() -> Path | None:
    if found := shutil.which("BDInfo"):
        return Path(found)
    ext = ".exe" if sys.platform == "win32" else ""
    local_bin = _BIN_DIR / f"BDInfo{ext}"
    if local_bin.exists() and (sys.platform == "win32" or local_bin.stat().st_mode & 0o111):
        return local_bin
    return None


def download_bdinfo() -> Path | None:
    machine = platform.machine().lower()
    if machine in ("amd64", "x64"):
        machine = "x86_64"
    platform_key = (sys.platform, machine)
    asset_name = _ASSET_MAP.get(platform_key)
    if not asset_name:
        log.warning(f"No BDInfo binary available for {platform_key}")
        return None

    try:
        resp = niquests.get("https://api.github.com/repos/tetrahydroc/BDInfoCLI/releases/latest", timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning(f"Failed to query BDInfo latest release: {e}")
        return None

    asset_url = None
    for asset in data.get("assets", []):
        if asset.get("name") == asset_name:
            asset_url = asset.get("browser_download_url")
            break

    if not asset_url:
        log.warning(f"BDInfo release asset '{asset_name}' not found")
        return None

    try:
        resp = niquests.get(asset_url, timeout=120)
        resp.raise_for_status()
    except Exception as e:
        log.warning(f"Failed to download BDInfo binary: {e}")
        return None

    _BIN_DIR.mkdir(parents=True, exist_ok=True)
    ext = ".exe" if sys.platform == "win32" else ""
    target_path = _BIN_DIR / f"BDInfo{ext}"

    tmp_path = ""
    try:
        suffix = ".tar.gz" if asset_name.endswith(".tar.gz") else ".zip"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(resp.content)
            tmp_path = tmp.name

        if asset_name.endswith(".tar.gz"):
            with tarfile.open(tmp_path, "r:gz") as tar:
                for member in tar.getmembers():
                    if member.isfile() and Path(member.name).name == f"BDInfo{ext}":
                        if ".." in member.name or Path(member.name).is_absolute():
                            continue
                        tar.extract(member, _BIN_DIR)
                        extracted = _BIN_DIR / member.name
                        if extracted != target_path:
                            extracted.rename(target_path)
                        break
        elif asset_name.endswith(".zip"):
            with zipfile.ZipFile(tmp_path, "r") as zf:
                for name in zf.namelist():
                    if not name.endswith("/") and Path(name).name == f"BDInfo{ext}":
                        zf.extract(name, _BIN_DIR)
                        extracted = _BIN_DIR / name
                        if extracted != target_path:
                            extracted.rename(target_path)
                        break

        if target_path.exists() and sys.platform != "win32":
            target_path.chmod(target_path.stat().st_mode | 0o111)

        if target_path.exists():
            return target_path

        log.warning("BDInfo binary not found after extraction")
        return None

    except Exception as e:
        log.warning(f"Failed to extract BDInfo binary: {e}")
        return None

    finally:
        if tmp_path:
            with contextlib.suppress(Exception):
                Path(tmp_path).unlink(missing_ok=True)


def ensure_bdinfo() -> Path | None:
    return get_bdinfo_path() or download_bdinfo()
