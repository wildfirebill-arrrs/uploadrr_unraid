from __future__ import annotations

import asyncio
import contextlib
import re
from pathlib import Path
from typing import Any, Callable

import niquests
from rich.pretty import Pretty
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from src.utils.console import console, log
from src.utils.descgen import DescGenerator
from src.utils.torrentman import edit_torrent
from src.version import user_agent


class Unit3d:
    """
    Base class for Unit3D trackers.
    Subclasses must define:
    - tracker: str
    - source_flag: str
    - domain: str
    - allowed_imghosts: list[str]
    - banned_groups: list[str] or dict

    And implement/override as needed:
    - async def get_cat_id(self, meta: Dict[str, Any]) -> str
    - async def get_type_id(self, meta: Dict[str, Any]) -> str
    - async def get_res_id(self, meta: Dict[str, Any]) -> str
    - async def banned_content(self, meta: Dict[str, Any]) -> Tuple[bool, bool]
    - async def edit_name(self, meta: Dict[str, Any]) -> str (optional, for custom naming)
    - async def upload(self, meta: Dict[str, Any]) -> bool | str
    - async def search_existing(self, meta: Dict[str, Any]) -> Dict[str, Any]
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.type = "unit3d"
        self.timeout = niquests.TimeoutConfiguration(45.0)
        self.upload_url = f"{self.domain}/api/torrents/upload"
        self.search_url = f"{self.domain}/api/torrents/filter"
        self.bannedRG_url = f"{self.domain}/api/bannedReleaseGroups"

    def has_valid_api_key(self) -> bool:
        """Check whether a non-empty API key is configured for this tracker."""
        tracker_cfg = self.config.get("TRACKERS", {}).get(self.tracker, {})
        key = tracker_cfg.get("api_key")
        return bool(key and str(key).strip())

    async def get_banned_groups(self) -> list[str] | dict[str, Any]:
        """Fetch banned groups from tracker API; fallback to static list on any failure."""
        params = {"api_token": self.config["TRACKERS"][self.tracker]["api_key"].strip()}
        headers = {"User-Agent": "Uploadrr/1.0"}
        try:
            async with niquests.AsyncSession() as client:
                resp = await client.get(self.bannedRG_url, headers=headers, params=params, timeout=10)
                if resp.status_code != 200:
                    log.debug(
                        f"{self.tracker} bannedReleaseGroups returned HTTP {resp.status_code}, using static fallback"
                    )
                    return self.banned_groups
                data = resp.json()
                if not data.get("success"):
                    log.debug(
                        f"{self.tracker} bannedReleaseGroups response failed, using static fallback. Response: {data}"
                    )
                    return self.banned_groups
                api_data = data.get("data")
                if not api_data:
                    log.debug(
                        f"{self.tracker} bannedReleaseGroups returned empty list, using static fallback"
                    )
                    return self.banned_groups
                log.debug(f"{self.tracker}: fetched {len(api_data)} banned groups from API")
                return api_data
        except niquests.HTTPError as e:
            log.warning(f"{self.tracker} bannedReleaseGroups HTTP error: {e}, using static fallback")
        except Exception as e:
            log.warning(f"{self.tracker} bannedReleaseGroups failed: {e}, using static fallback")
        return self.banned_groups

    # fmt: off
    async def get_cat_id(self, meta: dict[str, Any]) -> str:
        category_name = meta["category"]
        category_id = {
            "MOVIE": "1",
            "TV": "2",
            "MUSIC": "3"
        }.get(category_name, "0")
        return category_id

    async def get_type_id(self, meta: dict[str, Any]) -> str:
        media_type = meta["type"]
        type_id = {
            "DISC": "1",
            "REMUX": "2",
            "WEBDL": "4",
            "WEBRIP": "5",
            "HDTV": "6",
            "ENCODE": "3",
            "FLAC": "7"
        }.get(media_type, "0")
        return type_id

    async def get_res_id(self, meta: dict[str, Any]) -> str:
        resolution = meta["resolution"]
        resolution_id = {
            "8640p": "10",
            "4320p": "1",
            "2160p": "2",
            "1440p": "3",
            "1080p": "3",
            "1080i": "4",
            "720p": "5",
            "576p": "6",
            "576i": "7",
            "480p": "8",
            "480i": "9",
        }.get(resolution, "10")
        return resolution_id
    # fmt: on
    async def banned_content(self, meta: dict[str, Any]) -> tuple[bool, bool]:
        """
        Check if content is banned.
        Returns (True, True) for immediate ban, (True, False) for warning, (False, False) for no ban.
        """
        return False, False

    async def get_extra_data(self, meta: dict[str, Any]) -> dict[str, Any]:
        """Hook for extra data payload fields. Override in Trackers."""
        return {}

    async def get_extra_files(self, meta: dict[str, Any]) -> dict[str, tuple[str, str, str]]:
        """
        Hook for extra file uploads. Override in subclasses for specific trackers.
        Return a dict where keys are form field names (e.g., 'cover', 'banner'),
        and values are tuples: (filename, path_to_file, mime_type).
        """
        return {}

    def _parse_response(self, response_json: dict) -> tuple[bool, str, str]:
        success = response_json.get("success", False)
        raw_url = response_json.get("data", "")
        return success, raw_url, raw_url

    async def _execute_upload(
        self,
        meta: dict[str, Any],
        data: dict[str, Any],
        files: dict[str, tuple[str, Any, str]],
        parse_response: Callable[[dict], tuple[bool, str, str]],
        torrent_path: Path,
    ) -> bool | str:
        tmp_dir = torrent_path.parent
        headers = {"User-Agent": user_agent()}
        params = {"api_token": self.config["TRACKERS"][self.tracker]["api_key"].strip()}

        try:
            async with niquests.AsyncSession(timeout=self.timeout, disable_http3=True) as client:
                response = await client.post(
                    self.upload_url,
                    data=data,
                    files=files if files else None,
                    headers=headers,
                    params=params,
                )
                response.raise_for_status()
                if 200 <= response.status_code < 300:
                    try:
                        response_json = response.json()
                    except ValueError:
                        log.error(f"Unexpected response:\n{response.text}")
                        return False
                    success, raw_url, details_url = parse_response(response_json)
                    if success:
                        try:
                            if not raw_url:
                                console.print(
                                    "[yellow]Tracker returned an empty download URL; "
                                    "torrent comment will not be added.[/yellow]"
                                )
                            else:
                                async with niquests.AsyncSession(
                                    timeout=self.timeout, disable_http3=True
                                ) as dl_client:
                                    torrent_response = await dl_client.get(raw_url)
                                    torrent_response.raise_for_status()
                                    torrent_data = torrent_response.content
                                    new_torrent = tmp_dir / f"[{self.tracker}]{meta['clean_name']}.torrent"
                                    new_torrent.write_bytes(torrent_data)
                                    comment_url = details_url or raw_url
                                    url = comment_url.replace("/download", "s")
                                    url = re.sub(r"/(\d+)\..*$", r"/\1", url)
                                    comment = f"Made by Uploadrr for: {url}"
                                    await edit_torrent(torrent_path=new_torrent, comment=comment)
                                    console.print("[bold green]Torrent uploaded successfully!")
                            await asyncio.sleep(0)
                        except niquests.HTTPError as http_err:
                            console.print(
                                f"[yellow]Torrent upload succeeded, but failed to download the torrent file: {http_err}[/yellow]"
                            )
                            return True
                        except Exception as e:
                            console.print(
                                f"[red]Torrent upload succeeded, but failed to save the torrent file: {e}[/red]"
                            )
                            return True
                    else:
                        message = response_json.get("message", "No message provided")
                        console.print(f"[red]Upload failed: {message}[/red]")
                        if "data" in response_json:
                            console.print(f"[cyan]Error details:[/cyan] {response_json['data']}")
                    return success
                return False
        except niquests.HTTPError as http_err:
            if http_err.response.status_code in (404, 500):
                tree = HTMLParser(http_err.response.text)
                error_heading = tree.css_first("h1.error__heading")
                error_body = tree.css_first("div.error__body")

                if error_heading:
                    log.error(error_heading.text().strip())
                if error_body:
                    log.error(error_body.text().strip())
                if not error_heading and not error_body:
                    console.print(f"[blue]Raw Response:[/blue] {http_err.response.text}")
                return False
            else:
                log.warning(f"[yellow]Unexpected Status Code ({http_err.response.status_code}):[/yellow]")
                console.print(http_err.response.text)
                return False
        except Exception:
            console.print_exception()
            return False

    async def upload(self, meta: dict[str, Any]) -> bool | str:
        descgen = DescGenerator(config=self.config, base_dir=meta["base_dir"])
        cat_id = await self.get_cat_id(meta)
        type_id = await self.get_type_id(meta)
        resolution_id = await self.get_res_id(meta)

        await descgen.edit_desc(meta, self.tracker)
        xanon = (
            1
            if (
                self.config["TRACKERS"][self.tracker].get("xanon", False)
                or self.config.get("ADULT", {}).get("always_anon", False)
            )
            and meta.get("xxx")
            else 0
        )
        anon = (
            1
            if (meta.get("anon", 0) or self.config["TRACKERS"][self.tracker].get("anon", False))
            or self.config["DEFAULT"].get("always_anon", False)
            or xanon
            else 0
        )
        modq = 1 if meta.get("modq", False) or self.config["TRACKERS"][self.tracker].get("modq", False) else 0

        # Prepare file contents
        tmp_dir = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
        bd_path = tmp_dir / "BD_SUMMARY_00.txt"
        bd_dump = (
            bd_path.read_text(encoding="utf-8")
            if meta.get("bdinfo") is not None and bd_path.exists()
            else None
        )
        mi_path = tmp_dir / "MEDIAINFO.txt"
        mi_dump = mi_path.read_text(encoding="utf-8") if mi_path.exists() else ""
        desc_path = tmp_dir / f"[{self.tracker}]DESCRIPTION.txt"
        desc = desc_path.read_text(encoding="utf-8") if desc_path.exists() else ""
        torrent_path = tmp_dir / "BASE.torrent"
        nfo_file = meta.get("nfo_file")
        # cover = meta.get('album_cover', '')
        is_music = meta["is_music"]

        name = (
            meta.get("manual_name")
            or meta.get(f"{self.tracker}_name")
            or (await self.edit_name(meta) if hasattr(self, "edit_name") else None)
            or meta["name"]
        )

        performers = meta.get("porndb", {}).get("performers", "") or ""
        tags = meta.get("porndb", {}).get("tags", "") or ""

        adult_keywords = f"{performers}, {tags}" if performers and tags else performers or tags
        keywords = meta["keywords"]

        if meta["category"] in ["TV", "MOVIE"]:  # safety for now due to music/sports issue
            keywords = meta["keywords"].split(", ") if meta["keywords"] else []
            imdb_keywords = meta.get("imdb_info", {}).get("keywords", "").split(", ")
            keywords.extend(imdb_keywords)
            unique_keywords = list({kw.strip() for kw in keywords if kw.strip()})
            keywords = ", ".join(unique_keywords)

        # Build payload
        data = {
            "name": name,
            "description": desc,
            "mediainfo": mi_dump,
            "category_id": cat_id,
            "type_id": type_id,
            "tmdb": meta.get("tmdb", 0),
            "imdb": meta.get("imdb_id", 0),
            "tvdb": meta.get("tvdb_id", 0),
            "mal": meta.get("mal_id", 0),
            "igdb": 0,
            "anonymous": anon,
            "stream": meta["stream"],
            "sd": meta["sd"],
            "keywords": keywords or adult_keywords,
            "personal_release": int(meta.get("personalrelease") or False),
            "internal": int(meta.get("internal") or 0),
            "featured": 0,
            "free": int(meta.get("freeleech") or 0),
            "doubleup": 0,
            "sticky": 0,
            "mod_queue_opt_in": modq,
        }

        if not is_music:
            data.update({"resolution_id": resolution_id, "bdinfo": bd_dump})

        data.update(await self.get_extra_data(meta))

        # Internal
        if self.config["TRACKERS"][self.tracker].get("internal", False):
            if meta.get("tag", "") and meta["tag"][1:] in self.config["TRACKERS"][self.tracker].get(
                "internal_groups", []
            ):
                data["internal"] = "1"

        if meta.get("region_id", 0) != 0:
            data["region_id"] = meta["region_id"]
        if meta.get("distributor_id", 0) != 0:
            data["distributor_id"] = meta["distributor_id"]
        if meta.get("category") == "TV":
            data["season_number"] = int(meta.get("season_int", 0))
            data["episode_number"] = int(meta.get("episode_int", 0))

        extra_files_meta = await self.get_extra_files(meta)

        if meta.get("debug", False):
            log.debug("[bright_magenta]DATA 2 SEND[/bright_magenta]:")
            console.print(Pretty(data))
            log.debug("[bright_magenta]FILES 2 SEND[/bright_magenta]:")
            files_info = {"torrent": torrent_path.name}
            if nfo_file:
                files_info["nfo"] = str(Path(nfo_file).name)
            for field, (_fname, path, _mime) in extra_files_meta.items():
                files_info[field] = str(Path(path).name)
            console.print(Pretty(files_info))
            if not meta.get("nohash", False):
                await edit_torrent(
                    meta,
                    None,
                    self.tracker,
                    "DEBUG",
                    "https://UNIT3D.CHANGES/INFOHASH/DEBUG-ONLY",
                    "Uploadrr DEBUG-MODE",
                )
            else:
                log.debug(
                    "[bright_magenta]SKIPPING torrent edit — nohash mode, no BASE.torrent[/bright_magenta]"
                )
            return "DEBUG"

        with contextlib.ExitStack() as stack:
            files: dict[str, tuple[str, Any, str]] = {}
            torrent_file = stack.enter_context(open(torrent_path, "rb"))
            files["torrent"] = ("torrent.torrent", torrent_file, "application/x-bittorrent")

            if (
                nfo_file
                and not meta.get("no_nfo", False)
                and self.config["TRACKERS"][self.tracker].get("nfo", True)
            ):
                nfo_content = stack.enter_context(open(nfo_file, "rb"))
                files["nfo"] = ("nfo.nfo", nfo_content, "application/octet-stream")

            for field, (fname, path, mime) in extra_files_meta.items():
                f = stack.enter_context(open(path, "rb"))
                files[field] = (fname, f, mime)

            return await self._execute_upload(meta, data, files, self._parse_response, torrent_path)

    async def search_existing(self, meta: dict[str, Any]) -> dict[str, Any]:
        dupes: dict[str, Any] = {}
        if self.tracker in ("LCD", "LT", "TTR"):
            console.print(f"[yellow]Buscando torrents existentes en {self.tracker}...")
        else:
            console.print(f"[yellow]Searching for existing torrents on {self.tracker}...")

        name = ""
        if meta["category"] == "TV":
            name += f" {meta.get('season', '')}{meta.get('episode', '')}"
        if meta.get("edition", ""):
            name += f" {meta['edition']}"

        params: dict[str, str | int] = {
            "api_token": self.config["TRACKERS"][self.tracker]["api_key"].strip(),
        }

        if meta.get("is_music", False):
            music_files = meta.get("music_files", [])
            if music_files:
                params["file_name"] = Path(music_files[0]).name
        else:
            params.update(
                {
                    "tmdbId": meta.get("tmdb", 0),
                    "categories[]": await self.get_cat_id(meta),
                    "types[]": await self.get_type_id(meta),
                    "resolutions[]": await self.get_res_id(meta),
                    "name": name,
                }
            )

        log.debug(f"{self.tracker} dupe search params: {params}")
        async with niquests.AsyncSession(timeout=self.timeout, disable_http3=True) as client:
            try:
                response = await client.get(self.search_url, params=params)
                log.debug(f"{self.tracker} dupe search status: {response.status_code}")
                if response.status_code == 200:
                    response_json = response.json()
                    log.debug(f"{self.tracker} dupe search response: {response_json}")
                    for item in response_json.get("data", []):
                        name = item["attributes"]["name"]
                        size = item["attributes"]["size"]
                        dupes[name] = size
                else:
                    log.error(f"[red]Failed to search: HTTP {response.status_code}[/red]")
                    log.debug(f"{self.tracker} dupe search raw response: {response.text}")
            except Exception as e:
                if self.tracker in ("LCD", "LT", "TTR"):
                    console.print(f"[red]No se pudo buscar torrents existentes: {e}[/red]")
                else:
                    console.print(f"[red]Unable to search for existing torrents: {e}[/red]")
                await asyncio.sleep(5)
        log.debug(f"{self.tracker} dupes found: {dupes}")
        return dupes
