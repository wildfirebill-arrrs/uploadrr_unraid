from __future__ import annotations

import asyncio
import contextlib
import re
from pathlib import Path
from typing import Any, ClassVar

import niquests
from rich.pretty import Pretty
from selectolax.lexbor import LexborHTMLParser

from src.databases.ptgen import ptdesc
from src.mediainfo import MediaInfoHandler
from src.utils.captcha import solve_nexusphp_captcha
from src.utils.console import console, log
from src.utils.cookieme import CookieMonster
from src.utils.country_groups import get_group
from src.utils.descgen import DescGenerator
from src.utils.size_parser import parse_size
from src.utils.torrentman import edit_torrent
from src.version import user_agent

__uploadrr__ = Path(__file__).resolve().parent.parent.parent


class NexusPHP:
    """Base class for NexusPHP trackers with cookie-based auth and CAPTCHA login.

    Subclasses must define:
    - tracker: str
    - source_flag: str
    - domain: str
    - allowed_imghosts: list[str]
    - banned_groups: list[str] or dict

    And implement:
    - async def get_cat_id(self, meta: dict[str, Any]) -> str
    - async def get_type_id(self, meta: dict[str, Any]) -> str

    Commonly overridden:
    - tracker_url: str | None = None      (used for announce URL construction)
    - simple_name: bool = False
    - MEDIUM_MAP, CODEC_MAP, TEAM_MAP     (ClassVar[dict[str, str]])
    - async def get_medium(self, meta) -> str
    - async def get_video_id(self, meta) -> str
    - async def get_team(self, meta) -> str
    - async def get_tags(self, meta) -> list[str]
    - async def edit_name(self, meta) -> str
    - async def edit_desc(self, meta) -> None
    - async def banned_content(self, meta) -> tuple[bool, bool]
    """

    simple_name: bool = False
    tracker_url: str | None = None
    AUDIOCODEC_MAP: ClassVar[dict[str, str]] = {}
    ZONE_MAP: ClassVar[dict[str, str]] = {}
    ZONE_DEFAULT: ClassVar[str] = ""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.type = "nexus"
        self.timeout = niquests.TimeoutConfiguration(45.0)
        tracker_config = config["TRACKERS"][self.tracker]
        self.username = str(tracker_config["username"]).strip()
        self.password = str(tracker_config["password"]).strip()
        self.two_step_code = str(tracker_config.get("two_step_code", "")).strip()
        self.otp_uri = str(tracker_config.get("otp_uri", "")).strip()
        passkey = str(tracker_config.get("passkey", "")).strip()
        if not passkey:
            log.warning(f"No valid pass_key for {self.tracker} in config!")
            self.announce = f"{self.domain}/INVALID_passkey_CHECK_CONFIG"
        elif passkey.startswith("http"):
            self.announce = passkey
        else:
            tracker_url = self.tracker_url or self.domain.replace("//", "//tracker.")
            self.announce = f"{tracker_url}/announce.php?passkey={passkey}"
        if (self.username or self.password) == "None":
            log.warning(f"Invalid user/pass for {self.tracker}")

        self.cookie = __uploadrr__ / f"data/cookies/{self.tracker}.pkl"
        self.cookie_monster = CookieMonster(self.cookie)
        self.session: niquests.AsyncSession | None = None

    async def start_session(self) -> None:
        if self.session is not None:
            log.warning("Previous session was not closed properly. Closing it now.")
            await self.close_session()
        self.session = niquests.AsyncSession(disable_http3=True)

    async def close_session(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None

    @staticmethod
    def _is_authenticated_response(response_text: str) -> bool:
        return "logout.php" in response_text.lower() or ">logout<" in response_text.lower()

    async def ensure_authenticated(self) -> bool:
        """Ensure we have a valid, authenticated session. Returns True on success."""
        if self.session is None:
            await self.start_session()
        if await self.cookie_monster.load(self.session):

            def check(response: niquests.Response) -> bool:
                return self._is_authenticated_response(response.text)

            if await self.cookie_monster.validate(self.session, f"{self.domain}/index.php", check):
                return True
        if await self._login(self.session):
            await self.cookie_monster.save(self.session)
            return True
        return False

    def _get_login_mode(self, tree: LexborHTMLParser) -> str:
        """Detect the login security mode from the login page HTML."""
        if tree.css_first("input[name='imagehash']"):
            return "captcha"
        if tree.css_first(".cf-turnstile") or tree.css_first("[data-turnstile-sitekey]"):
            return "turnstile"
        if tree.css_first(".g-recaptcha") or tree.css_first("[data-recaptcha-sitekey]"):
            return "recaptcha"
        if tree.css_first("input[name='username']") and tree.css_first("input[name='password']"):
            return "none"
        return "unknown"

    def _build_login_data(self, username: str, password: str, two_step_code: str = "") -> dict[str, str]:
        """Build the common login payload."""
        data: dict[str, str] = {
            "username": username,
            "password": password,
            "logout": "",
            "securelogin": "",
            "ssl": "yes",
            "trackerssl": "yes",
        }
        if two_step_code:
            data["two_step_code"] = two_step_code
        return data

    def _get_two_factor_code(self) -> str:
        """Generate or prompt for a 2FA code."""
        if self.otp_uri:
            import pyotp

            return pyotp.parse_uri(self.otp_uri).now()
        if self.two_step_code:
            return self.two_step_code
        return console.input(f"[yellow]{self.tracker} 2FA Code: ")

    async def _post_login(
        self, client: niquests.AsyncSession, data: dict[str, str], login_url: str
    ) -> niquests.Response:
        """Submit the login form and return the response."""
        safe_data = {**data, "password": "***"}
        log.debug(f"POST data to takelogin.php: {safe_data}")

        headers = {
            "Referer": login_url,
            "Origin": self.domain,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:141.0) Gecko/20100101 Firefox/141.0"
            ),
        }

        return await client.post(
            f"{self.domain}/takelogin.php", data=data, headers=headers, allow_redirects=True
        )

    async def _check_login_response(self, response: niquests.Response) -> bool:
        """Check if login was successful based on response."""
        url = str(response.url)
        return self._is_authenticated_response(response.text) or "index.php" in url.lower()

    async def _log_login_failure(self, response: niquests.Response, attempt: int) -> None:
        """Log details about a failed login attempt."""
        url = str(response.url)
        tree = LexborHTMLParser(response.text)
        error = tree.css_first("td.embedded") or tree.css_first("div.error")
        status = response.status_code
        if error:
            log.error(f"Login failed ({status} @ {url}): {error.text(strip=True)}")
        else:
            preview = response.text[:500].replace("\n", " ")
            log.error(f"Login failed ({status} @ {url}). Response preview: {preview}")

    async def _handle_two_factor(
        self, client: niquests.AsyncSession, response: niquests.Response
    ) -> niquests.Response | None:
        """Handle 2FA challenge if present. Returns new response or None if not a 2FA page."""
        url = str(response.url).lower()
        if "twofactor" not in url and "2fa" not in url:
            return None

        tree = LexborHTMLParser(response.text)
        token_input = tree.css_first('input[name="token"]') or tree.css_first(
            'input[name="authenticity_token"]'
        )
        token = token_input.attributes.get("value", "") if token_input else ""

        two_factor_data: dict[str, str] = {"code": self._get_two_factor_code(), "submit": "login"}
        if token:
            two_factor_data["token"] = token

        # Some sites require uid for 2FA
        if hasattr(self, "uid") and self.uid:
            two_factor_data["uid"] = self.uid

        two_factor_url = (
            f"{self.domain}/twofactor/login" if "twofactor" in url else f"{self.domain}/take2fa.php"
        )
        log.debug(f"Submitting 2FA code to {two_factor_url}")
        return await client.post(two_factor_url, data=two_factor_data)

    async def _login(self, client: niquests.AsyncSession) -> bool:
        """Form-based login supporting CAPTCHA, Turnstile, reCAPTCHA, and no verification."""
        for attempt in range(2):
            try:
                login_url = f"{self.domain}/login.php"
                response = await client.get(login_url)
                tree = LexborHTMLParser(response.text)
                mode = self._get_login_mode(tree)

                if mode in ("turnstile", "recaptcha"):
                    log.error(
                        f"Detected {mode} on {self.tracker}. "
                        f"Please save pre-authenticated cookies to {self.cookie_monster.netscape_file} "
                        f"or {self.cookie}. Exiting login."
                    )
                    return False

                if mode == "unknown":
                    log.error(f"Unknown login mode for {self.tracker}. Cannot proceed.")
                    return False

                if mode == "captcha":
                    success = await self._login_captcha(client, tree, attempt, login_url)
                else:
                    success = await self._login_simple(client, attempt, login_url)

                if success:
                    return True

                # Failed but might retry
                if attempt == 0:
                    log.debug("Retrying login with fresh cookies...")
                    self.cookie.unlink(missing_ok=True)
                    client.cookies.clear()
                    continue
                return False

            except niquests.RequestException as e:
                log.error(f"Connection failed during login to {self.domain}: {e}")
                return False
        return False

    async def _login_simple(self, client: niquests.AsyncSession, attempt: int, login_url: str) -> bool:
        """Login without any CAPTCHA/verification."""
        data = self._build_login_data(self.username, self.password)
        response = await self._post_login(client, data, login_url)
        log.debug(f"Attempt {attempt}: POST response status={response.status_code} url={response.url}")

        # Check for 2FA redirect
        two_factor_response = await self._handle_two_factor(client, response)
        if two_factor_response is not None:
            response = two_factor_response

        if await self._check_login_response(response):
            return True

        await self._log_login_failure(response, attempt)
        return False

    async def _login_captcha(
        self, client: niquests.AsyncSession, tree: LexborHTMLParser, attempt: int, login_url: str
    ) -> bool:
        """Login with imagehash CAPTCHA solving."""
        # Scrape imagehash
        imagehash_input = tree.css_first("input[name='imagehash']")
        if not imagehash_input:
            log.error("Failed to find imagehash in login form")
            return False
        imagehash = imagehash_input.attributes.get("value", "")
        if not imagehash:
            log.error("imagehash value is empty")
            return False

        # Scrape CAPTCHA image
        captcha_img = tree.css_first('img[alt="CAPTCHA"]')
        if not captcha_img:
            for img in tree.css("img"):
                src = img.attributes.get("src", "")
                if "captcha" in src.lower() or "imagehash" in src.lower():
                    captcha_img = img
                    break
        if not captcha_img:
            log.error("Failed to find CAPTCHA image")
            return False

        captcha_src = captcha_img.attributes.get("src", "")
        captcha_url = f"{self.domain}/{captcha_src}"
        log.debug(f"Attempt {attempt}: CAPTCHA src={captcha_src} url={captcha_url} imagehash={imagehash}")

        # Fetch and solve CAPTCHA
        captcha_resp = await client.get(captcha_url)
        if captcha_resp.status_code != 200:
            log.error(f"Failed to fetch CAPTCHA image: {captcha_resp.status_code}")
            return False

        try:
            imagestring = await asyncio.to_thread(solve_nexusphp_captcha, captcha_resp.content)
        except RuntimeError as exc:
            log.warning(f"CAPTCHA solve failed for imagehash={imagehash}: {exc}")
            return False

        log.info(f"Attempt {attempt}: CAPTCHA solved as imagestring={imagestring!r}")

        data = self._build_login_data(self.username, self.password)
        data["imagestring"] = imagestring
        data["imagehash"] = imagehash

        response = await self._post_login(client, data, login_url)
        log.debug(f"Attempt {attempt}: POST response status={response.status_code} url={response.url}")

        # Check for 2FA redirect
        two_factor_response = await self._handle_two_factor(client, response)
        if two_factor_response is not None:
            response = two_factor_response

        if await self._check_login_response(response):
            return True

        await self._log_login_failure(response, attempt)
        return False

    async def get_cat_id(self, meta: dict[str, Any]) -> str:
        category = meta["category"]
        if category == "MOVIE":
            return "401"
        if category == "TV":
            return "402"

    async def get_type_id(self, meta: dict[str, Any]) -> str:
        raise NotImplementedError

    async def banned_content(self, meta: dict[str, Any]) -> tuple[bool, bool]:
        """Check if content is banned on most NexusPHP trackers.

        Most NexusPHP trackers strictly prohibit porn.
        For MUSIC, lossy formats are typically banned.
        For video, lossy audio is only permitted if >= 5.1 channels; lossless 2.0 or mono is fine.

        Subclasses may override this to adjust rules per tracker.
        Returns (True, True) for immediate ban, (True, False) for warning, (False, False) for no ban.
        """
        has_ban = False
        has_warn = False

        # --- Porn / Adult content ---
        adult = meta.get("adult", False)
        if meta.get("xxx") or adult:
            log.warning(f"{self.tracker}: Adult/XXX content is banned.")
            has_ban = True

        # --- Music: lossy banned ---
        if meta.get("category") == "MUSIC" and meta.get("is_lossy"):
            log.warning(f"{self.tracker}: Lossy music formats are banned.")
            has_ban = True

        # --- Video: lossy audio only allowed if >= 5.1 channels ---
        if meta.get("category") in ("MOVIE", "TV"):
            channels_raw = meta.get("channels", "")
            audio_str = str(meta.get("audio", "")).upper()
            lossy_audio = any(
                codec in audio_str for codec in ("AAC", "DD", "DDP", "OPUS", "MP3", "MP2", "AC3", "EAC3")
            )

            if lossy_audio:
                # Cantonese / Mandarin dubs in 2.0 are allowed despite being lossy
                if meta.get("is_mandarin") or meta.get("is_cantonese"):
                    try:
                        ch_float = float(channels_raw)
                        if ch_float <= 2:
                            pass  # allowed, nothing to do
                        else:
                            # Mandarin/Cantonese dub > 2.0 channels: subject to normal lossy check below
                            pass
                    except (ValueError, TypeError):
                        pass

                try:
                    ch_float = float(channels_raw)
                    if ch_float < 5.1:
                        log.warning(
                            f"{self.tracker}: Lossy audio ({audio_str}) with {channels_raw} channels is banned. "
                            "Lossy audio must be 5.1 channels or higher."
                        )
                        has_ban = True
                except (ValueError, TypeError):
                    log.warning(
                        f"{self.tracker}: Could not parse channel count '{channels_raw}' for lossy audio check."
                    )
                    has_warn = True

        # --- No Chinese content warning ---
        if not meta.get("no_zh_check") and meta.get("category") in ("MOVIE", "TV"):
            has_chinese = meta.get("is_mandarin") or meta.get("is_cantonese") or meta.get("zh_subs")
            if not has_chinese:
                log.warning(f"{self.tracker}: No Chinese audio or subtitles detected.")
                console.print(
                    f"[yellow]{self.tracker}: No Mandarin/Cantonese audio or Chinese subtitles detected. "
                    "Most Chinese trackers prefer content with Chinese language support.[/yellow]"
                )
                has_warn = True

        if has_ban:
            return True, True
        if has_warn:
            return True, False
        return False, False

    async def get_small_descr(self, meta: dict[str, Any]) -> str:
        """Build short description: resolution @ video_bitrate - audio_codec channels @ audio_bitrate."""
        resolution = meta.get("resolution", "")
        audio = meta.get("audio", "")
        video_bitrate = ""
        audio_bitrate = ""

        uuid = meta.get("uuid", "")
        mi = MediaInfoHandler()._cache.get(uuid) if uuid else None
        if mi:
            try:
                tracks = mi["media"]["track"]
                video = next((t for t in tracks if t.get("@type") == "Video"), {})
                video_bitrate = video.get("BitRate_String", video.get("BitRate", ""))
                audio_track = next((t for t in tracks if t.get("@type") == "Audio"), {})
                audio_bitrate = audio_track.get("BitRate_String", audio_track.get("BitRate", ""))
            except (KeyError, IndexError, StopIteration):
                pass

        parts = []
        if resolution:
            parts.append(resolution)
        if video_bitrate:
            parts.append(f"@ {video_bitrate}")
        if audio:
            parts.append(f"- {audio}")
        if audio_bitrate:
            parts.append(f"@ {audio_bitrate}")

        return " ".join(parts)

    async def imdb_url(self, meta: dict[str, Any]) -> str:
        imdb_id = meta.get("imdb_id", "")
        if imdb_id and str(imdb_id) not in ("0", "", None):
            return f"https://www.imdb.com/title/tt{str(imdb_id).replace('tt', '')}/"
        return ""

    async def get_pt_gen(self, meta: dict[str, Any]) -> str:
        """Return empty; we generate the PT-Gen description ourselves in edit_desc."""
        return ""

    async def edit_desc(self, meta: dict[str, Any]) -> None:
        """Generate description with PT-Gen content prepended."""
        descgen = DescGenerator(config=self.config, base_dir=meta["base_dir"])
        await descgen.edit_desc(meta, self.tracker, img_size=0)

        tmp_dir = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
        desc_path = tmp_dir / f"[{self.tracker}]DESCRIPTION.txt"
        if not desc_path.exists():
            return

        base_desc = await asyncio.to_thread(desc_path.read_text, encoding="utf-8")
        ptgen_desc = ""

        if int(meta.get("imdb_id", "0")) != 0:
            ptgen_desc = await ptdesc(meta, ptgen_retry=3)

        if ptgen_desc:
            combined = f"{ptgen_desc}\n\n{base_desc}"
            await asyncio.to_thread(desc_path.write_text, combined, encoding="utf-8")
            log.debug(f"Prepended PT-Gen description for {self.tracker}")

    async def get_technical_info(self, meta: dict[str, Any]) -> str:
        tmp_dir = Path(meta["base_dir"]) / "tmp" / meta["uuid"]

        if meta.get("bdinfo") is not None:
            bd_path = tmp_dir / "BD_SUMMARY_00.txt"
            if bd_path.exists():
                return bd_path.read_text(encoding="utf-8")

        mi_path = tmp_dir / "MEDIAINFO.txt"
        if mi_path.exists():
            return mi_path.read_text(encoding="utf-8")

        return ""

    async def get_medium(self, meta: dict[str, Any]) -> str:
        return ""

    async def get_video_id(self, meta: dict[str, Any]) -> str:
        return ""

    async def get_audio_id(self, meta: dict[str, Any]) -> str:
        audio = meta.get("audio", "")
        audio_stripped = str(audio).strip()

        if audio_stripped:
            mapped = self.AUDIOCODEC_MAP.get(audio_stripped)
            if mapped:
                return mapped
            for codec_id, val in sorted(
                self.AUDIOCODEC_MAP.items(), key=lambda item: len(item[0]), reverse=True
            ):
                if codec_id in audio_stripped:
                    return val

        uuid = meta.get("uuid", "")
        mi = MediaInfoHandler()._cache.get(uuid) if uuid else None
        if mi:
            try:
                tracks = mi["media"]["track"]
                audio_track = next((t for t in tracks if t.get("@type") == "Audio"), {})
                fmt = audio_track.get("Format", "")
                commercial = audio_track.get("Format_Commercial", fmt)
                additional = audio_track.get("Format_AdditionalFeatures", "")

                for codec_id, val in self.AUDIOCODEC_MAP.items():
                    if codec_id in fmt or codec_id in commercial:
                        return val

                if additional and "Atmos" in additional:
                    ddp_atmos = self.AUDIOCODEC_MAP.get("DDP Atmos")
                    truehd_atmos = self.AUDIOCODEC_MAP.get("TrueHD Atmos")
                    if "DDP" in fmt or "E-AC-3" in fmt:
                        if ddp_atmos:
                            return ddp_atmos
                    if "TrueHD" in fmt:
                        if truehd_atmos:
                            return truehd_atmos
            except (KeyError, IndexError, StopIteration):
                pass

        for fallback in ("DDP", "TrueHD", "AAC"):
            if fallback in self.AUDIOCODEC_MAP:
                return self.AUDIOCODEC_MAP[fallback]
        return ""

    async def get_res_id(self, meta: dict[str, Any]) -> str:
        resolution = meta["resolution"]
        resolution_id = {
            "4320p": "1",
            "2160p": "2",
            "1440p": "3",
            "1080p": "4",
            "1080i": "5",
            "720p": "6",
            "576p": "7",
            "576i": "8",
            "480p": "9",
            "480i": "10",
        }.get(resolution, "4")
        return resolution_id

    def _resolve_zone(self, code: str) -> str | None:
        code = code.upper().strip()
        mapped = self.ZONE_MAP.get(code)
        if mapped:
            return mapped
        for group_name in (
            "EUROPE",
            "NORTH_AMERICA",
            "SOUTH_AMERICA",
            "CENTRAL_AMERICA",
            "CARIBBEAN",
            "ASIA",
            "AFRICA",
            "MIDDLE_EAST",
            "OCEANIA",
        ):
            if group_name in self.ZONE_MAP and code in get_group(group_name):
                return self.ZONE_MAP[group_name]
        return None

    async def get_region_id(self, meta: dict[str, Any]) -> str:
        imdb_info = meta.get("imdb_info", {})
        if isinstance(imdb_info, dict):
            origin_codes = imdb_info.get("origin_country_code", [])
        else:
            origin_codes = meta.get("origin_country_code", [])

        if origin_codes:
            for code in origin_codes:
                resolved = self._resolve_zone(code)
                if resolved:
                    return resolved

        regions = meta.get("region", [])
        if not regions:
            ptgen = meta.get("ptgen", {})
            if isinstance(ptgen, dict):
                regions = ptgen.get("region", [])

        if regions:
            for region in regions:
                resolved = self._resolve_zone(region)
                if resolved:
                    return resolved

        return self.ZONE_DEFAULT

    async def get_team(self, meta: dict[str, Any]) -> str:
        return ""

    async def get_tags(self, meta: dict[str, Any]) -> list[str]:
        """Return list of tag values for tags. Override per tracker."""
        return []

    async def is_anon(self, meta: dict[str, Any]) -> str:
        anon = (
            "yes"
            if (meta.get("anon", 0) or self.config["TRACKERS"][self.tracker].get("anon", False))
            or self.config["DEFAULT"].get("always_anon", False)
            else "no"
        )
        return anon

    async def get_upload_data(self, meta: dict[str, Any]) -> dict[str, Any]:
        """Build the standard upload payload. Subclasses may override."""
        name = (
            meta.get("manual_name")
            or meta.get(f"{self.tracker}_name")
            or (await self.edit_name(meta) if hasattr(self, "edit_name") else None)
        )
        name = name or meta["name"]

        tmp_dir = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
        desc_file = tmp_dir / f"[{self.tracker}]DESCRIPTION.txt"

        if hasattr(self, "edit_desc"):
            await self.edit_desc(meta)
        else:
            descgen = DescGenerator(config=self.config, base_dir=meta["base_dir"])
            await descgen.edit_desc(meta, self.tracker)

        desc = desc_file.read_text(encoding="utf-8") if desc_file.exists() else ""

        data: dict[str, Any] = {
            "name": name,
            "descr": desc,
            "type": await self.get_cat_id(meta),
            "source_sel": await self.get_type_id(meta),
            "uplver": await self.is_anon(meta),
            "small_descr": await self.get_small_descr(meta),
            "url": await self.imdb_url(meta),
            "pt_gen": await self.get_pt_gen(meta),
            "technical_info": await self.get_technical_info(meta),
            "medium_sel[4]": await self.get_medium(meta),
            "codec_sel[4]": await self.get_video_id(meta),
            "audiocodec_sel[4]": await self.get_audio_id(meta),
            "standard_sel[4]": await self.get_res_id(meta),
            "processing_sel[4]": await self.get_region_id(meta),
            "team_sel[4]": await self.get_team(meta),
            "tags[4][]": await self.get_tags(meta),
        }
        return data

    async def upload(self, meta: dict[str, Any]) -> bool | str:
        if not await self.ensure_authenticated():
            log.error("Authentication failed")
            return False

        data = await self.get_upload_data(meta)

        tmp_dir = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
        torrent_path = tmp_dir / f"[{self.tracker}]{meta['clean_name']}.torrent"
        await edit_torrent(meta, None, self.tracker, self.source_flag, self.announce)

        headers = {"User-Agent": user_agent()}

        if meta.get("debug", False):
            log.debug("[bright_magenta]DATA 2 SEND[/bright_magenta]")
            console.print(Pretty(data))
            if not meta.get("nohash", False):
                await edit_torrent(meta, None, self.tracker, "DEBUG", self.announce, "Uploadrr DEBUG-MODE")
            else:
                log.debug(
                    "[bright_magenta]SKIPPING torrent edit — nohash mode, no BASE.torrent[/bright_magenta]"
                )
            return "DEBUG"

        try:
            with contextlib.ExitStack() as stack:
                torrent_file = stack.enter_context(open(torrent_path, "rb"))
                files = {"file": ("torrent.torrent", torrent_file, "application/x-bittorrent")}
                response = await self.session.post(
                    f"{self.domain}/takeupload.php", data=data, files=files, headers=headers
                )
                response.raise_for_status()

                url = str(response.url)
                if "details.php?id=" in url:
                    comment = f"Made by Uploadrr for {url.replace('&uploaded=1', '')}"
                    await edit_torrent(torrent_path=torrent_path, comment=comment)
                    return True
                elif "upload.php" in url:
                    tree = LexborHTMLParser(response.text)
                    error = tree.css_first("td.embedded") or tree.css_first("div.error")
                    if error:
                        log.error(error.text(strip=True))
                    else:
                        log.warning("Stayed on upload page, upload may have failed")
                    return False
                else:
                    log.warning(f"Unexpected redirect to {url}, assuming success.")
                    return True
        except niquests.HTTPError as http_err:
            resp = http_err.response
            tree = LexborHTMLParser(resp.text)
            error = tree.css_first("td.embedded") or tree.css_first("div.error")
            if error:
                log.error(error.text(strip=True))
            else:
                log.error(f"Upload failed ({resp.status_code}): {resp.text!r}")
            return False
        except Exception as exc:
            log.error(f"Upload error: {exc}")
            return False

    async def search_existing(self, meta: dict[str, Any]) -> dict[str, Any]:
        dupes: dict[str, Any] = {}
        search_term = meta.get("title", "")
        imdb_id = meta.get("imdb_id", "")
        if imdb_id and imdb_id not in ("0", "", None):
            search_term = f"tt{imdb_id}"

        cat_id = await self.get_cat_id(meta)
        params: dict[str, str] = {"incldead": "1", "search": search_term, "search_area": "4"}
        if cat_id != "EXIT":
            params["cat"] = cat_id

        try:
            if self.session is None:
                await self.start_session()
            if not await self.ensure_authenticated():
                log.error("Cannot search: not authenticated")
                return dupes

            response = await self.session.get(f"{self.domain}/torrents.php", params=params)
            response.raise_for_status()
            tree = LexborHTMLParser(response.text)
            for row in tree.css("table.torrents tr"):
                if not row.css_first("table.torrentname"):
                    continue
                title_link = row.css_first('a[href^="details.php?id="]')
                if not title_link:
                    continue
                name = title_link.attributes.get("title", "") or title_link.text(strip=True)
                if not name:
                    continue
                size_text = ""
                _size_pattern = re.compile(r"\d+(?:\.\d+)?\s*(?:TB|GB|MB|KB|B)\b")
                for td in row.css("td.rowfollow"):
                    text = td.text(strip=True)
                    if _size_pattern.search(text):
                        size_text = text
                        break
                dupes[name] = parse_size(size_text) if size_text else 0
        except niquests.RequestException as exc:
            log.error(f"Search request failed: {exc}")
        except Exception as exc:
            log.error(f"Search error: {exc}")
        return dupes
