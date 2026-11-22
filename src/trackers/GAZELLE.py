from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import aiofiles
import niquests
from rich.pretty import Pretty
from selectolax.lexbor import LexborHTMLParser

from src.mediainfo import MediaInfoHandler
from src.screenshots.rehoster import RehostManager
from src.screenshots.up2host import ScreenshotUploader
from src.utils.console import console, log
from src.utils.cookieme import CookieMonster
from src.utils.descgen import DescGenerator
from src.utils.size_parser import parse_size
from src.utils.torrentman import edit_torrent
from src.version import user_agent

__uploadrr__ = Path(__file__).resolve().parent.parent.parent


class Gazelle:
    """
    Base class for Gazelle/Luminance trackers.
    Subclasses must define:
    - tracker: str
    - type: str -> "luminance" or "gazelle"
    - source_flag: str
    - domain: str
    - allowed_imghosts: list[str]
    - banned_groups: list[str] or dict

    And implement/override as needed:
    - async def get_cat_id(self, meta: Dict[str, Any]) -> str
    - async def upload(self, meta: Dict[str, Any]) -> bool
    - async def search_existing(self, meta: Dict[str, Any]) -> List[Dict[str, Any]]
    """

    simple_name: bool = False

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.timeout = niquests.TimeoutConfiguration(45.0)
        tracker_config = config["TRACKERS"][self.tracker]
        self.username = str(tracker_config["username"]).strip()
        self.password = str(tracker_config["password"]).strip()
        self.api_key = str(tracker_config.get("api_key", "")).strip() if "api_key" in tracker_config else None
        self.cookie = __uploadrr__ / f"data/cookies/{self.tracker}.pkl"
        self.cookie_monster = CookieMonster(self.cookie)
        try:
            self.announce = tracker_config["announce_url"]
            if not self.announce or self.announce == "Custom_Announce_URL":
                log.warning(f"No valid PID for {self.tracker} in config!")
                self.announce = "No Valid Announce FOUND - Check CONFIG"
        except KeyError:
            log.error(f"No announce_url for {self.tracker} in config!")
            self.announce = "No Valid Announce FOUND - Check CONFIG"
        if (self.username or self.password) == "None":
            log.warning(f"Invalid user/pass for {self.tracker}")
        # Search URL for API-based gazelle search
        self.search_url = f"{self.domain}/torrents.php"
        self.session: niquests.AsyncSession | None = None

    async def start_session(self):
        if self.session is not None:
            log.warning("Previous session was not closed properly. Closing it now.")
            await self.close_session()
        self.session = niquests.AsyncSession(disable_http3=True)

    async def close_session(self):
        if self.session is not None:
            await self.session.close()
            self.session = None

    async def _save_cookies(self, client: niquests.AsyncSession) -> bool:
        return await self.cookie_monster.save(client)

    @staticmethod
    def _is_authenticated_response(response_text: str) -> bool:
        """Check response body for indicators of an authenticated session.

        Gazelle trackers typically include authkey= or a logout link when logged in.
        """
        return "authkey=" in response_text or "logout.php?auth=" in response_text

    async def _login(self, client: niquests.AsyncSession) -> bool:
        """Default Gazelle login with CSRF token. Override for simpler login flows."""
        for attempt in range(2):
            try:
                login_url = f"{self.domain}/login"
                headers = {
                    "Referer": f"{self.domain}/upload.php",
                    "Origin": self.domain,
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:141.0) Gecko/20100101 Firefox/141.0"
                    ),
                }
                response = await client.get(login_url)
                tree = LexborHTMLParser(response.text)
                token_input = tree.css_first("input[name='token']")
                if not token_input or not token_input.attributes.get("value"):
                    log.error("Failed to find token in login form")
                    return False
                token = token_input.attributes["value"]
                data = {"token": token, "username": self.username, "password": self.password}
                if hasattr(self, "cinfo"):
                    data["cinfo"] = self.cinfo
                data.update({"iplocked": 0, "keeploggedin": 1, "submit": "login"})
                console.print(Pretty(data))
                response = await client.post(login_url, data=data, headers=headers, allow_redirects=True)
                if self._is_authenticated_response(response.text):
                    return True
                else:
                    tree = LexborHTMLParser(response.text)
                    error = (
                        tree.css_first("div.error")
                        or tree.css_first("div.flash.error")
                        or tree.css_first("p.error")
                    )
                    status = response.status_code
                    url = str(response.url)
                    if error:
                        log.error(f"Login failed ({status} @ {url}): {error.text(strip=True)}")
                    else:
                        preview = response.text[:500].replace("\n", " ")
                        log.error(f"Login failed ({status} @ {url}). Response preview: {preview}")
                    if attempt == 0:
                        log.info("Retrying login with fresh cookies...")
                        self.cookie.unlink(missing_ok=True)
                        client.cookies.clear()
                        continue
                    else:
                        return False
            except niquests.RequestException as e:
                log.error(f"Connection failed during login to {self.domain}: {e}")
                return False
        return False

    async def _get_auth(self, client: niquests.AsyncSession) -> str:
        url = f"{self.domain}/index.php"
        response = await client.get(url)
        auth = response.text.rsplit("authkey=", 1)[1][:32]
        return auth

    async def ensure_authenticated(self) -> bool:
        """Ensure we have a valid, authenticated session. Returns True on success."""
        if self.session is None:
            await self.start_session()
        # Try loading cookies
        if await self.cookie_monster.load(self.session):

            def check(response: niquests.Response) -> bool:
                return self._is_authenticated_response(response.text)

            if await self.cookie_monster.validate(self.session, f"{self.domain}/upload.php", check):
                return True
        if await self._login(self.session):
            await self.cookie_monster.save(self.session)
            return True
        return False

    async def get_cat_id(self, meta: dict[str, Any]) -> str:
        """Subclasses must implement"""
        raise NotImplementedError

    async def banned_content(self, meta: dict[str, Any]) -> tuple[bool, bool]:
        """Check if content is banned. Returns (True, True) for immediate ban, (True, False) for warning."""
        return False, False

    async def get_tags(self, meta: dict[str, Any]) -> str:
        sd = meta["sd"] == 1
        resolution = meta["resolution"].lower()
        service_longname = meta.get("service_longname", "").strip()
        audio_norm = meta["audio"].lower().replace("-", ".").replace(":", ".").replace(" ", ".")
        video_codec = (
            meta.get("video_codec", "").replace("AVC", "h264").replace("HEVC", "h265").replace("-", "")
        )
        group = meta.get("tag", "")
        scene = meta.get("scene", False)
        is_disc = meta.get("is_disc", "")
        category = meta["category"]

        tags = []

        if meta.get("genres"):
            genres = meta["genres"]
            if isinstance(genres, str):
                genres = [g.strip() for g in genres.split(",")]
            tags.extend(x.strip(", ").lower().replace(" ", ".") for x in genres)

        tags.append(resolution)
        if sd:
            tags.append("sd")
        elif resolution in ("2160p", "4320p"):
            tags.append("uhd")
        else:
            tags.append("hd")

        if service_longname:
            tags.append(f"{service_longname.lower().replace(' ', '.')}.source")

        source_set = {meta["source"].lower().replace("-", ""), meta["type"].lower().replace(".", "")}
        for each in ["remux", "web.dl", "webrip", "hdtv", "bluray", "dvd", "hddvd"]:
            if each in source_set:
                tags.append(each.upper() if each == "remux" else each.title().replace(".", ""))
                break

        if category == "TV":
            tv_pack = meta.get("tv_pack", 0)
            tags.append("season.release" if tv_pack else "episode.release")
            tags.append(("sd" if sd else "hd") + (".season" if tv_pack else ".episode"))
        elif category == "MOVIE":
            tags.append(("sd" if sd else "hd") + ".movie")

        audio_types = ["dd", "ddp", "aac", "truehd", "mp3", "mp2", "dts", "dts.hd", "dts.x"]
        for t in audio_types:
            if t in audio_norm:
                tags.append(f"{t}.audio")
                break
        if "atmos" in audio_norm:
            tags.append("atmos.audio")

        if video_codec:
            tags.append(video_codec)

        if group:
            tags.append(f"{group[1:].replace(' ', '.')}")
        else:
            tags.append("NOGRP.release")

        tags.append("scene.group.release" if scene else "p2p.group.release")

        if is_disc:
            if (mihandle := getattr(self, "_mihandle", None)) is None:
                mihandle = MediaInfoHandler()
                self._mihandle = mihandle
            mi = mihandle._cache.get(meta["uuid"])
            if mi:
                if is_disc != "BDMV":
                    if any(track.get("@type") == "Text" for track in mi["media"]["track"]):
                        tags.append("subtitles")
                else:
                    bd_subs = meta.get("bdinfo", {}).get("subtitles", [])
                    if len(bd_subs) >= 1:
                        tags.append("subtitles")

        return " ".join(tags)

    async def get_cover_url(self, meta: dict[str, Any]) -> str:
        candidates = [
            meta.get("gazelle_cover"),
            meta.get("cover_url"),
            meta.get("poster"),
            meta.get("imdb_info", {}).get("cover"),
        ]
        for url in candidates:
            if url and RehostManager._extract_host(url, set(self.allowed_imghosts)):
                return url

        upload_url = meta.get("poster") or meta.get("imdb_info", {}).get("cover") or meta.get("cover_url")
        if not upload_url:
            return ""

        tmp_dir = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
        cover_path = tmp_dir / "cover.jpg"
        try:
            # Download remote cover to temp file
            async with niquests.AsyncSession(disable_http3=True) as session:
                resp = await session.get(upload_url)
                if resp.status_code == 200:
                    cover_path.write_bytes(resp.content)
                else:
                    log.warning(f"Failed to download cover, status {resp.status_code}")
                    return ""

            uploader = ScreenshotUploader(self.config, Path(meta["base_dir"]))
            screens = {"main": [{"local_file": str(cover_path)}]}
            viable_hosts = self.allowed_imghosts

            async with uploader:
                result = await uploader.upload_screenshots(meta, screens, viable_hosts)

            if result["main"]:
                uploaded_url = result["main"][0]["img_url"]
                meta["gazelle_cover"] = uploaded_url
                return uploaded_url
        except Exception as e:
            log.warning(f"Poster upload failed {e}")
        return ""

    async def get_upload_data(self, meta: dict[str, Any]) -> dict[str, Any]:
        if (
            not (name := meta.get("manual_name"))
            and not (cached := meta.get(f"{self.tracker}_name"))
            and hasattr(self, "edit_name")
        ):
            name = await self.edit_name(meta)
        name = name or cached or meta["name"]

        desc_file = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / f"[{self.tracker}]DESCRIPTION.txt"
        if hasattr(self, "edit_desc"):
            await self.edit_desc(meta)
        else:
            descgen = DescGenerator(config=self.config, base_dir=meta["base_dir"])
            gazelle_desc = await descgen.gazelle_desc(meta, self.tracker, self.allowed_imghosts)
            async with aiofiles.open(desc_file, "w", encoding="utf-8") as descfile:
                await descfile.write(gazelle_desc)

        async with aiofiles.open(desc_file, encoding="utf-8") as descfile:
            desc = await descfile.read()

        anon = (
            1
            if (meta.get("anon", 0) or self.config["TRACKERS"][self.tracker].get("anon", False))
            or self.config["DEFAULT"].get("always_anon", False)
            else 0
        )

        compliant_cover = await self.get_cover_url(meta)

        return {
            "submit": "true",
            "auth": "",
            "category": await self.get_cat_id(meta),
            "title": name,
            "taglist": await self.get_tags(meta),
            "image": compliant_cover,
            "desc": desc,
            "anonymous": anon,
        }

    async def upload(self, meta: dict[str, Any]) -> bool | str:
        if not await self.ensure_authenticated():
            log.error("Authentication failed")
            return False

        data = await self.get_upload_data(meta)
        data["auth"] = await self._get_auth(self.session)

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
            with ExitStack() as stack:
                torrent_file = stack.enter_context(open(torrent_path, "rb"))
                files = {"file_input": ("torrent.torrent", torrent_file, "application/x-bittorrent")}
                response = await self.session.post(
                    f"{self.domain}/upload.php", data=data, files=files, headers=headers
                )
                response.raise_for_status()

                tree = LexborHTMLParser(response.text)
                head_div = tree.css_first("div.head")
                if head_div and "Error" in head_div.text():
                    box_pad = tree.css_first("div.box.pad")
                    if box_pad:
                        log.error(box_pad.text(strip=True))
                    else:
                        log.error("Upload error: Unknown error")
                    return False

                if "torrents.php" in str(response.url):
                    comment = f"Made by Uploadrr for {response.url}"
                    await edit_torrent(meta, None, self.tracker, self.source_flag, self.announce, comment)
                    return True
                elif "upload.php" in str(response.url):
                    messagebar = tree.css_first("div#messagebar")
                    if messagebar:
                        log.error(messagebar.text(strip=True))
                        return False
                    log.warning("Stayed on Upload Page, upload may have failed - Please Check.")
                    return "Unexpected Response"
                else:
                    log.warning(f"Unexpected redirect to {response.url}, assuming success.")
                    return True
        except niquests.HTTPError as http_err:
            resp = http_err.response
            if resp.status_code == 500 and not resp.text.strip():
                if await self._verify_upload_after_500(meta):
                    log.info("Upload verified as successful despite 500 error")
                    return True
                else:
                    log.warning("Server returned 500 with empty body. The upload may have succeeded.")
                    console.print("[bold yellow]It may have uploaded, please go check")
                    return "Unexpected Response"
            tree = LexborHTMLParser(resp.text)
            error = tree.css_first("div.error") or tree.css_first("p.error")
            if error:
                log.error(error.text(strip=True))
            else:
                log.error(f"Upload failed ({resp.status_code}): {resp.text!r}")
            return False

    async def _verify_upload_after_500(self, meta: dict[str, Any]) -> bool:
        # CRT has known issue atm so it'll override
        return False

    async def search_existing(self, meta: dict[str, Any]) -> list[dict[str, Any]]:
        if self.type == "gazelle":
            dupes = []
            params = {"t": "search", "apikey": self.api_key, "q": ""}
            imdb_id = meta.get("imdb_id")
            tmdb = meta.get("tmdb")
            tvdb = meta.get("tvdb_id")
            title = meta.get("title", "")
            if imdb_id not in ("0", "", None):
                params["imdbid"] = "tt" + imdb_id
            elif tmdb != 0:
                params["tmdbid"] = tmdb
            elif tvdb != 0:
                params["tvdbid"] = tvdb
            else:
                params["q"] = title.replace(": ", " ").replace("'", "").replace("'", "")
            try:
                response = await niquests.get(
                    self.search_url, params=params, timeout=self.timeout, disable_http3=True
                )
                response.raise_for_status()
                root = ET.fromstring(response.text)
                channel = root.find("channel")
                if channel is not None:
                    for item in channel.findall("item"):
                        title_elem = item.find("title")
                        size_elem = item.find("size")
                        files_elem = item.find("files")
                        link_elem = item.find("guid") or item.find("link")
                        dupes.append(
                            {
                                "name": title_elem.text if title_elem is not None else "",
                                "size": int(size_elem.text)
                                if size_elem is not None and size_elem.text
                                else None,
                                "files": None,
                                "file_count": files_elem.text if files_elem is not None else None,
                                "trumpable": None,
                                "link": link_elem.text if link_elem is not None else None,
                            }
                        )
            except Exception as e:
                console.print(f"[red]Unable to search for existing torrents: {e}[/red]")
                await asyncio.sleep(5)
            return dupes
        else:  # Luminance
            cat_id = await self.get_cat_id(meta)
            name = meta.get("title", "")
            params = {f"filter_cat[{int(cat_id)}]": 1, "searchtext": name}
            dupes_local: list[dict[str, Any]] = []
            try:
                response = await self.session.get(f"{self.domain}/torrents.php", params=params)
                response.raise_for_status()
                tree = LexborHTMLParser(response.text)
                torrent_table = tree.css_first("table#torrent_table")
                if torrent_table:
                    for row in torrent_table.css("tr"):
                        css_classes = row.attributes.get("class", "")
                        if css_classes and "torrent" in css_classes:
                            tds = list(row.css("td"))
                            if len(tds) >= 6:
                                name_td = tds[1]
                                size_td = tds[5]
                                file_count_td = tds[2]
                                name_link = name_td.css_first("a[href^='/torrents.php?id=']")
                                if name_link:
                                    torrent_name = name_link.text(strip=True)
                                    size_str = size_td.text(strip=True)
                                    size = parse_size(size_str)
                                    link = (
                                        f"{self.domain}{name_link.attributes['href']}" if name_link else None
                                    )
                                    file_count = file_count_td.text(strip=True) if file_count_td else None
                                    dupes_local.append(
                                        {
                                            "name": torrent_name,
                                            "size": size,
                                            "files": None,
                                            "file_count": file_count,
                                            "trumpable": None,
                                            "link": link,
                                        }
                                    )
            except Exception as e:
                log.error(f"Unable to search for existing torrents: {e}")
                await asyncio.sleep(5)
            return dupes_local
