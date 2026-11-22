from __future__ import annotations

import asyncio
from pathlib import Path

import aiofiles
import niquests
from rich.pretty import Pretty

# from src.version import user_agent
from src.utils.console import console, log
from src.utils.descgen import DescGenerator
from src.utils.torrentman import edit_torrent


class DC:
    def __init__(self, config):
        self.config = config
        self.type = "api"
        self.tracker = "DC"
        self.source_flag = "DigitalCore.club"
        self.domain = "https://digitalcore.club"
        self.upload_url = f"{self.domain}/api/v1/torrents/upload"
        self.search_url = f"{self.domain}/api/v1/torrents"
        self.banned_groups = []
        self.session = None

        tracker_conf = config["TRACKERS"].get(self.tracker, {})
        PID = tracker_conf.get("passkey", "PASSKEY_HERE")
        if PID.startswith("https://"):
            self.announce_url = PID
        else:
            self.announce_url = f"https://tracker.digitalcore.club/announce/{PID}"
        self.api_key = tracker_conf.get("api_key", "").strip()
        self.anon = tracker_conf.get("anon", False)

    async def _ensure_session(self):
        if self.session is None:
            self.session = niquests.AsyncSession(disable_http3=True)

    async def _close_session(self):
        if self.session is not None:
            await self.session.close()
            self.session = None

    def _auth_headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ValueError("DC api_key is not configured.")
        return {"X-API-KEY": self.api_key}

    async def get_cat_id(self, meta: dict) -> str:
        category = meta.get("category")
        resolution = meta.get("resolution", "")
        sd = meta.get("sd", False)
        is_disc = meta.get("is_disc", False)
        tv_pack = meta.get("tv_pack", False)
        uhd = meta.get("uhd", False)
        source = meta.get("source", "")
        is_sports = meta.get("is_sports", False)
        type_ = meta.get("type", "")
        title = meta.get("title", "").lower()

        if category == "MOVIE":
            if is_disc == "DVD" or source in ("PAL DVD", "NTSC DVD", "DVD"):
                return "1"
            if is_disc in ("BDMV", "HDDVD") or (type_ == "REMUX" and source in ("BluRay", "HDDVD")):
                return "38" if uhd else "3"
            if resolution in ("2160p", "4320p", "8640p") or uhd:
                return "4"
            if resolution == "1080p":
                return "6"
            if resolution == "720p":
                return "5"
            if sd:
                return "2"
            return "7"

        if category == "TV":
            if is_disc == "DVD":
                return "11"
            if is_disc in ("BDMV", "HDDVD") or (type_ == "REMUX" and source in ("BluRay", "HDDVD")):
                return "14"
            if resolution in ("2160p", "4320p", "8640p") or uhd:
                return "13"
            if resolution == "1080p":
                return "9"
            if resolution == "720p":
                return "8"
            if tv_pack:
                return "12"
            if sd:
                return "10"
            if is_sports:
                return "15"
            return "9"

        if category == "MUSIC":
            if "mp3" in title or "mp3" in source.lower():
                return "22"
            if "flac" in title or "flac" in source.lower():
                return "23"
            if "mtv" in title or "music video" in title:
                return "24"
            if tv_pack or "pack" in title:
                return "29"
            if is_disc == "DVD" or "dvd" in source.lower():
                return "39"
            if is_disc in ("BDMV", "HDDVD"):
                return "40"
            return "23"

        if category == "XXX":
            # imagesets 34
            if tv_pack or "pack" in title:
                return "41"
            if "movie" in title or "feature" in title:
                if resolution in ("2160p", "4320p", "8640p") or uhd:
                    return "37"
                if resolution == "1080p":
                    return "36"
                return "35"
            if resolution in ("2160p", "4320p", "8640p") or uhd:
                return "32"
            if resolution == "1080p":
                return "31"
            return "30"

        # if category == "APPS":
        #     return "18"

        # if category == "GAMES":
        #     return "25"

        # if category == "EBOOKS":
        #     return "28"

        # if category == "AUDIOBOOKS":
        #     return "44"

        return "17"

    async def edit_name(self, meta: dict) -> str:
        manual_name = meta.get("manual_name")
        if manual_name:
            return manual_name
        dc_name = meta["name"]
        dc_name = dc_name.replace(":", "").replace("'", "").replace(" ", ".")
        meta[f"{self.tracker}_name"] = dc_name
        return dc_name

    async def edit_desc(self, meta: dict):
        descgen = DescGenerator(config=self.config, base_dir=meta["base_dir"])
        tmp_dir = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
        desc_path = tmp_dir / f"[{self.tracker}]DESCRIPTION.txt"

        desc = await descgen.dc_desc(meta, self.tracker, whitelist=[])
        async with aiofiles.open(desc_path, "w", encoding="utf-8") as f:
            await f.write(desc)

    async def upload(self, meta: dict):
        await edit_torrent(
            meta=meta, tracker=self.tracker, source_flag=self.source_flag, announce_url=self.announce_url
        )
        await self.edit_desc(meta)

        cat_id = await self.get_cat_id(meta)
        tmp_dir = Path(meta["base_dir"]) / "tmp" / meta["uuid"]

        if meta.get("bdinfo") is not None:
            bd_path = tmp_dir / "BD_SUMMARY_00.txt"
            bd_text = bd_path.read_text(encoding="utf-8") if bd_path.exists() else None

        mi_path = tmp_dir / "MEDIAINFO.txt"
        mi_text = mi_path.read_text(encoding="utf-8") if mi_path.exists() else None

        desc_path = tmp_dir / f"[{self.tracker}]DESCRIPTION.txt"
        desc = desc_path.read_text(encoding="utf-8") if desc_path.exists() else ""

        torrent_path = tmp_dir / f"[{self.tracker}]{meta['clean_name']}.torrent"
        if not torrent_path.exists():
            log.error(f"DC upload failed: torrent file not found at {torrent_path}")
            return False

        imdb = meta.get("imdb_id", "")
        imdb_id = f"tt{imdb}" if imdb and not str(imdb).startswith("tt") else str(imdb) if imdb else ""

        data = {
            "category": cat_id,
            "imdbId": imdb_id,
            "nfo": desc,
            "mediainfo": mi_text or bd_text or "",
            "reqid": "0",
            "section": "new",
            "frileech": "1" if self.config["TRACKERS"][self.tracker].get("freeleech", False) else "0",
            "anonymousUpload": "1" if (meta.get("anon", 0) or self.anon) else "0",
            "p2p": "0",
        }

        headers = self._auth_headers()

        if meta.get("debug", False):
            log.debug("[magenta]DATA 2 SEND[/magenta]:")
            console.print(Pretty(data))
            return "DEBUG"

        try:
            await self._ensure_session()
            with open(torrent_path, "rb") as tf:
                files = {"file": (f"{meta['clean_name']}.torrent", tf, "application/x-bittorrent")}

                response = await self.session.post(self.upload_url, data=data, files=files, headers=headers)
                response.raise_for_status()

                try:
                    resp_json = response.json()
                except ValueError:
                    resp_json = {}

                if isinstance(resp_json, dict):
                    upload_id = resp_json.get("id")
                    if upload_id:
                        comment = f"Made by Uploadrr for {self.domain}/torrent/{upload_id}"
                        await edit_torrent(
                            meta=meta,
                            tracker=self.tracker,
                            source_flag=self.source_flag,
                            announce_url=self.announce_url,
                            comment=comment,
                        )
                        console.print("[bold green]Torrent uploaded successfully to DigitalCore!")
                        return True
                    error = resp_json.get("error") or resp_json.get("message", "Unknown error")
                    log.error(f"DC upload failed: {error}")
                    return False

                return response.status_code == 200
        except niquests.RequestException as e:
            log.error(f"DC upload error: {e}")
            return False
        finally:
            await self._close_session()

    async def search_existing(self, meta: dict) -> dict[str, int]:
        dupes: dict[str, int] = {}
        console.print("[yellow]Searching for existing torrents on DigitalCore...")

        cat_id = await self.get_cat_id(meta)
        params = {
            "categories[]": cat_id,
            "dead": "true",
            "extendedDead": "true",
            "extendedSearch": "false",
            "index": "0",
            "limit": "100",
            "order": "desc",
            "page": "search",
            "section": "all",
            "sort": "d",
            "stereoscopic": "false",
            "watchview": "false",
        }
        console.print(Pretty(params))
        imdb = meta.get("imdb_id", "")
        imdb_str = f"tt{imdb}" if imdb and not str(imdb).startswith("tt") else str(imdb) if imdb else ""

        if imdb_str:
            params["searchText"] = imdb_str
        elif meta.get("category") == "TV":
            if meta.get("tv_pack"):
                params["searchText"] = f"{meta.get('title', '')} {meta.get('season', '')}".strip()
            else:
                params["searchText"] = (
                    f"{meta.get('title', '')} {meta.get('season', '')}{meta.get('episode', '')}"
                ).strip()
        else:
            params["searchText"] = meta.get("title", "")

        try:
            await self._ensure_session()
            headers = self._auth_headers()
            response = await self.session.get(self.search_url, params=params, headers=headers)
            response.raise_for_status()

            if not response.text:
                return dupes
            data = response.json()
            if not isinstance(data, list):
                log.warning(f"Unexpected DC search response format: {type(data)}")
                return dupes

            for item in data:
                name = item.get("name", "")
                size = item.get("size", 0)
                if name:
                    dupes[name] = int(size)
        except niquests.RequestException as e:
            log.error(f"DC search error: {e}")
            await asyncio.sleep(5)
        except Exception as e:
            log.error(f"DC search parsing error: {e}")
            await asyncio.sleep(5)
        finally:
            await self._close_session()

        return dupes
