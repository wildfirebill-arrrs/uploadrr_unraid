from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import niquests

from src.progress import ProgressManager
from src.utils.console import console, log
from src.utils.meta import save_meta

LEGACY_URL = "https://ptgen.zhenzhen.workers.dev"
DEFAULT_URL = "https://pt-gen.hares.dpdns.org"
DEFAULT_AUTH_SECRET = "hares.23663"


@dataclass
class PtGenResponse:
    """Normalized PT-Gen response."""

    success: bool
    format: str = ""
    error: str | None = None
    raw: dict[str, Any] | None = None


def _hmac_signature(secret: str, timestamp: int) -> str:
    """Generate HMAC-SHA256 signature matching PT-Gen frontend."""
    message = str(timestamp).encode()
    key = secret.encode()
    sig = hmac.new(key, message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")


def _request_id() -> str:
    """Generate a PT-Gen requestId: req_{timestamp}_{random}."""
    ts = int(time.time() * 1000)
    rand = "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(8))
    return f"req_{ts}_{rand}"


async def _try_legacy(
    client: niquests.AsyncSession, url: str, meta: dict[str, Any], retry: int
) -> PtGenResponse:
    """Attempt the legacy ptgen.zhenzhen GET flow."""
    imdb_id = meta.get("imdb_id", "0")
    if int(imdb_id) == 0:
        return PtGenResponse(success=False, error="No IMDb id")

    params = {"search": meta["imdb"], "source": "imdb"}
    try:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return PtGenResponse(success=False, error=f"Legacy search failed: {exc}")

    if data.get("success") and "data" in data and len(data["data"]) > 0:
        params["url"] = data["data"][0]["link"]
    elif data.get("success") and "imdb_id" in data:
        return PtGenResponse(success=True, raw=data)
    else:
        return PtGenResponse(success=False, error=data.get("error", "Legacy search returned no results"))

    for _ in range(retry):
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            if data.get("error") is None:
                return PtGenResponse(success=True, raw=data)
        except Exception:
            continue

    return PtGenResponse(success=False, error="Legacy fetch failed after retries")


async def _try_new_api(
    client: niquests.AsyncSession, base_url: str, media_url: str, auth_secret: str, retry: int
) -> PtGenResponse:
    """Attempt the new PT-Gen /api/getData POST flow."""
    payload: dict[str, Any] = {"url": media_url, "requestId": _request_id()}
    ts = int(time.time() * 1000)
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "X-Timestamp": str(ts),
        "X-Signature": _hmac_signature(auth_secret, ts),
    }

    last_err: str | None = None
    for attempt in range(retry + 1):
        try:
            resp = await client.post(f"{base_url}/api/getData", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            last_err = f"POST attempt {attempt + 1} failed: {exc}"
            continue

        if not data.get("success"):
            last_err = data.get("error", "Unknown API error")
            continue

        fmt = data.get("format", "")
        if not fmt:
            last_err = "API returned empty format"
            continue

        return PtGenResponse(success=True, format=fmt, raw=data)

    return PtGenResponse(success=False, error=last_err or "New API failed")


async def ptdesc(
    meta: dict[str, Any], ptgen_site: str = "", ptgen_retry: int = 3, auth_secret: str | None = None
) -> str:
    """Fetch PT-Gen formatted description.

    Tries the new PT-Gen API first, falling back to the legacy endpoint
    if ``ptgen_site`` is explicitly set.  Returns the BBCode ``format``
    string with the cover image prepended, or "" on failure.
    """
    pm = ProgressManager.get_instance()
    imdb_id = meta.get("imdb_id", "0")
    has_imdb = int(imdb_id) != 0
    tmdb = meta.get("tmdb", "")
    category = meta.get("category", "MOVIE").lower()

    # Build candidate media URLs
    media_urls: list[str] = []
    if has_imdb:
        media_urls.append(f"https://www.imdb.com/title/tt{str(imdb_id).replace('tt', '')}/")
    if tmdb:
        media_urls.append(f"https://www.themoviedb.org/{category}/{tmdb}")

    if not media_urls:
        console.print("[red]No IMDb or TMDB id available for PT-Gen.")
        if meta.get("unattended", False):
            return ""
        url_input = ""
        if pm:
            with pm.pause_for_interaction():
                url_input = console.input("[red]Please enter [yellow]Douban[/yellow] link: ")
        else:
            url_input = console.input("[red]Please enter [yellow]Douban[/yellow] link: ")
        if url_input:
            media_urls.append(url_input.strip())
        else:
            return ""

    use_legacy = bool(ptgen_site)
    legacy_url = ptgen_site or LEGACY_URL
    new_url = DEFAULT_URL
    secret = auth_secret or DEFAULT_AUTH_SECRET

    async with niquests.AsyncSession(timeout=60, disable_http3=True) as client:
        result: PtGenResponse | None = None

        if not use_legacy:
            for media_url in media_urls:
                log.debug(f"PT-Gen trying new API POST for {media_url}")
                result = await _try_new_api(client, new_url, media_url, secret, ptgen_retry)
                if result.success:
                    break

        if use_legacy or (result is not None and not result.success):
            log.debug("PT-Gen falling back to legacy API")
            result = await _try_legacy(client, legacy_url, meta, ptgen_retry)

        if result is None or not result.success:
            err = result.error if result else "No result"
            console.print(f"[red]PT-Gen failed: {err}")
            return ""

        ptgen_data = result.raw if (result.raw and "format" in result.raw) else {"format": result.format}

        meta["ptgen"] = ptgen_data
        await save_meta(meta, Path(meta["base_dir"]), meta["uuid"])

        fmt = ptgen_data["format"]
        if "[/img]" in fmt:
            fmt = fmt.split("[/img]", 1)[1]

        cover = meta.get("imdb_info", {}).get("cover", meta.get("cover", ""))
        return f"[img]{cover}[/img]{fmt}"
