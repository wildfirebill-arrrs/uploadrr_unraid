from __future__ import annotations

import asyncio
import http.cookiejar
import pickle
from pathlib import Path
from typing import Any, Callable

import aiofiles
import niquests

from src.utils.console import log


def _jar_to_dict(jar: http.cookiejar.CookieJar) -> dict[str, dict[str, Any]]:
    """Convert a CookieJar to the canonical dict format used for pickle persistence."""
    cookie_dict: dict[str, dict[str, Any]] = {}
    for cookie in jar:
        cookie_dict[cookie.name] = {
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path,
            "expires": cookie.expires,
            "secure": cookie.secure,
        }
    return cookie_dict


class CookieMonster:
    """Utility for handling cookie loading, saving, and validation across trackers.

    Always saves as a custom pickle dict (name -> {value, domain, path, expires, secure}).
    Automatically converts legacy Netscape (.txt) files if found.
    """

    def __init__(self, cookie_file: Path) -> None:
        """:param cookie_file: Path to the cookie file (must end with .pkl)."""
        if cookie_file.suffix != ".pkl":
            raise ValueError("Cookie file must have a .pkl extension for pickle format.")
        self.cookie_file = cookie_file
        self.netscape_file = cookie_file.with_suffix(".txt")

    async def load(self, client: niquests.AsyncSession) -> bool:
        """Load cookies into the client session.

        Tries .pkl first.  If missing and a legacy Netscape .txt exists, converts
        .txt -> .pkl, deletes .txt, then loads.
        """
        if self.cookie_file.exists():
            return await self._load_pickle(client)

        if self.netscape_file.exists():
            try:
                await self._convert_netscape_to_pickle()
                if await self._load_pickle(client):
                    try:
                        self.netscape_file.unlink()
                        log.info(f"Deleted legacy Netscape file {self.netscape_file}")
                    except OSError as e:
                        log.warning(f"Failed to delete {self.netscape_file}: {e}")
                    return True
                log.warning("Converted pickle file failed to load; retaining .txt")
            except Exception as e:
                log.error(f"Failed to convert {self.netscape_file}: {e}")
            return False

        log.warning(f"No cookie file found (.pkl or legacy .txt): {self.cookie_file}")
        return False

    async def save(self, client: niquests.AsyncSession) -> bool:
        """Save the client's cookies to file in the canonical pickle dict format."""
        self._ensure_cookie_dir()
        try:
            cookie_dict = _jar_to_dict(client.cookies)
            async with aiofiles.open(self.cookie_file, "wb") as f:
                await f.write(pickle.dumps(cookie_dict))
            log.debug(f"Cookies saved to {self.cookie_file}")
            return True
        except Exception as e:
            log.error(f"Failed to save cookies to {self.cookie_file}: {e}")
            return False

    async def _load_pickle(self, client: niquests.AsyncSession) -> bool:
        try:
            async with aiofiles.open(self.cookie_file, "rb") as f:
                content = await f.read()

            if not content:
                log.error(f"Cookie file {self.cookie_file} is empty. Deleting file.")
                self.cookie_file.unlink(missing_ok=True)
                return False

            cookie_dict = pickle.loads(content)
            if not isinstance(cookie_dict, dict):
                log.error(f"Invalid pickle file {self.cookie_file}: not a dict. Deleting file.")
                self.cookie_file.unlink(missing_ok=True)
                return False

            jar = http.cookiejar.CookieJar()
            for name, attrs in cookie_dict.items():
                if not isinstance(attrs, dict) or "value" not in attrs:
                    got = type(attrs).__name__
                    raise KeyError(
                        f"Invalid cookie entry for '{name}': expected dict with 'value', got {got}"
                    )
                cookie = http.cookiejar.Cookie(
                    version=0,
                    name=name,
                    value=attrs["value"],
                    port=None,
                    port_specified=False,
                    domain=attrs["domain"],
                    domain_specified=bool(attrs["domain"]),
                    domain_initial_dot=attrs["domain"].startswith("."),
                    path=attrs["path"],
                    path_specified=bool(attrs["path"]),
                    secure=attrs["secure"],
                    expires=attrs["expires"],
                    discard=False,
                    comment=None,
                    comment_url=None,
                    rest={},
                    rfc2109=False,
                )
                jar.set_cookie(cookie)
            client.cookies = jar
            log.info(f"Cookies loaded from {self.cookie_file}")
            return True

        except asyncio.CancelledError:
            raise
        except (OSError, pickle.UnpicklingError, TypeError, KeyError) as e:
            log.error(f"Failed to load cookies from {self.cookie_file}: {e}")
            try:
                self.cookie_file.unlink(missing_ok=True)
            except Exception as e2:
                log.warning(f"Could not delete corrupt cookie file {self.cookie_file}: {e2}")
            return False

    async def validate(
        self,
        client: niquests.AsyncSession,
        validate_url: str,
        check_func: Callable[[niquests.Response], bool],
        extra_headers: dict | None = None,
    ) -> bool:
        """Validate cookies by making a GET request.

        :param client: niquests session with loaded cookies.
        :param validate_url: URL to request (e.g. index or upload page).
        :param check_func: callable taking Response -> bool.
        :param extra_headers: optional extra request headers.
        :returns: True if cookies are valid.
        """
        headers = extra_headers or {}
        try:
            response = await client.get(validate_url, headers=headers)
            response.raise_for_status()
            is_valid = check_func(response)
            if is_valid:
                log.debug(f"Cookies validated successfully for {validate_url}")
            else:
                log.warning(f"Cookies invalid for {validate_url}")
            return is_valid
        except Exception as e:
            log.error(f"Validation request failed for {validate_url}: {e}")
            return False

    async def _convert_netscape_to_pickle(self) -> None:
        if not self.netscape_file.exists():
            raise FileNotFoundError(f"Legacy Netscape file {self.netscape_file} does not exist")

        jar = http.cookiejar.MozillaCookieJar(str(self.netscape_file))
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
        except Exception as e:
            raise ValueError(f"Invalid legacy Netscape cookie file {self.netscape_file}: {e}") from e

        cookie_dict = _jar_to_dict(jar)
        self._ensure_cookie_dir()
        try:
            async with aiofiles.open(self.cookie_file, "wb") as f:
                await f.write(pickle.dumps(cookie_dict))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            raise RuntimeError(f"Failed to write pickle cookie file {self.cookie_file}: {e}") from e

    def _ensure_cookie_dir(self) -> None:
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
