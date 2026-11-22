from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlencode

import aiofiles
import niquests
from rich.pretty import Pretty
from selectolax.parser import HTMLParser

from src.mediainfo import MediaInfoHandler
from src.trackers.GAZELLE import Gazelle
from src.utils.bbcode import BBCODE
from src.utils.console import console, log
from src.utils.descgen import DescGenerator


class AR(Gazelle):
    tracker = "AR"
    type = "gazelle"
    source_flag = "AlphaRatio"
    domain = "https://alpharatio.cc"
    allowed_imghosts: ClassVar[list[str]] = []
    banned_groups: ClassVar[dict[str, Any]] = [""]

    async def _login(self, client: niquests.AsyncSession) -> bool:
        for attempt in range(2):
            try:
                login_url = f"{self.domain}/login.php"
                response = await client.get(login_url)
                tree = HTMLParser(response.text)
                data = {
                    "username": self.username,
                    "password": self.password,
                    "keeplogged": "1",
                    "login": "Login",
                }

                console.print(Pretty(data))
                response = await client.post(login_url, data=data, allow_redirects=True)
                if self._is_authenticated_response(response.text):
                    return True
                else:
                    tree = HTMLParser(response.text)
                    error = (
                        tree.css_first("div.error")
                        or tree.css_first("div.flash.error")
                        or tree.css_first("p.error")
                    )
                    if error:
                        log.error(f"Login failed: {error.text().strip()}")
                    else:
                        log.error("Login failed")

                    if attempt == 0:
                        log.info("Retrying login with fresh cookies...")
                        self.cookie.unlink(missing_ok=True)
                        client.cookies.clear()
                        continue
                    else:
                        return False
            except niquests.RequestException as e:
                log.error(f"Login failed: {e}")
                return False
        return False

    async def get_cat_id(self, meta: dict, for_search: bool = False) -> str:
        category = meta.get("category")
        resolution = meta.get("resolution", "")
        anime = meta.get("anime", False)
        sd = meta.get("sd", False)
        is_disc = meta.get("is_disc", False)
        tv_pack = meta.get("tv_pack", False)

        if category == "MUSIC":
            return "28"
        if anime:
            if sd:
                return "16" if for_search else "15"
            else:
                return "17" if for_search else "16"
        if is_disc:
            return "15" if for_search else "14"
        if category == "MOVIE":
            if sd:
                return "8" if for_search else "7"
            elif resolution in ("2160p", "4320p", "8640p"):
                return "10" if for_search else "9"
            else:
                return "9" if for_search else "8"
        elif category == "TV":
            if sd:
                return "1" if for_search else "0"
            elif tv_pack:
                if resolution in ("2160p", "4320p", "8640p"):
                    return "7" if for_search else "6"
                else:
                    return "6" if for_search else "5"
            else:
                if resolution in ("2160p", "4320p", "8640p"):
                    return "3" if for_search else "2"
                else:
                    return "2" if for_search else "1"
        return "0"

    async def edit_desc(self, meta: dict):
        bbcode = BBCODE()
        tmp_dir = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
        desc_path = tmp_dir / f"[{self.tracker}]DESCRIPTION.txt"
        bd_path = tmp_dir / "BD_SUMMARY_00.txt"
        mi_path = tmp_dir / "MEDIAINFO.txt"

        descgen = DescGenerator(config=self.config, base_dir=meta["base_dir"])

        if meta.get("is_music", False):
            desc = await descgen.create_music_description(meta, self.tracker)
            async with aiofiles.open(desc_path, encoding="utf-8") as file:
                desc = await file.read()
            desc = bbcode.clean_for_ar(desc)
            async with aiofiles.open(desc_path, "w", encoding="utf-8") as file:
                await file.write(desc)
            return
        else:
            base_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / "DESCRIPTION.txt"
            async with aiofiles.open(base_path, encoding="utf-8") as base_file:
                base = await base_file.read()
            base = bbcode.clean_for_ar(base)

            mihandle = MediaInfoHandler()
            micache = mihandle._cache.get(meta["uuid"])
            mi = micache["media"]["track"]
            mi_data = await descgen.simple_mi_desc(mi=mi, meta=meta)

            desc_parts = []

            if self.config["DEFAULT"].get("add_logo", False) and meta.get("logo"):
                desc_parts.append(f"[align=center][img]{meta['logo']}[/img][/align]\n")

            desc_parts.append(
                f"[code]Filename........: {mi_data['filename']}\n"
                f"Duration........: {mi_data['duration']}\n"
                f"Video...........: {mi_data['video_details_str']}\n"
                f"Audio...........: {mi_data['audio_details_str']}\n"
                f"Subtitles.......: {mi_data['subtitles']}\n"
                f"Size............: {mi_data['size']}\n"
                f"IMDB............:[/code] [url]https://www.imdb.com/title/{meta.get('imdb')}[/url]\n"
            )
            desc_parts.append(
                f"[code]TMDB............:[/code] [url]https://www.themoviedb.org/{meta['category'].lower()}/{meta.get('tmdb')}[/url]\n"
            )
            desc_parts.append(
                f"[code]Plot............: "
                f"{meta.get('overview', '') or meta.get('imdb_info', {}).get('plot', '')}[/code]\n\n"
            )

            if meta.get("bdinfo") is not None:
                async with aiofiles.open(bd_path, encoding="utf-8") as bd_file:
                    bd_dump = await bd_file.read()
                desc_parts.append(f"[hide=BD iNFO]{bd_dump}[/hide]\n\n")
            else:
                async with aiofiles.open(mi_path, encoding="utf-8") as mi_file:
                    mi_dump = (await mi_file.read())[:-65].strip()
                desc_parts.append(f"[hide=Mediainfo]{mi_dump}[/hide]\n\n")

            full_desc = "".join(desc_parts)
            full_desc = bbcode.clean_for_ar(full_desc)
            signature = (
                "\n[align=right][size=2]"
                "[url=https://codeberg.org/CvT/Uploadrr]Uploadrr v3.0.0[/url][/size][/align]"
            )

            async with aiofiles.open(desc_path, "w", encoding="utf-8") as desc:
                await desc.write(full_desc)
                await descgen._write_images(desc, meta, self.tracker, img_size=0)
                await desc.write(f"\n\n{base}\n{signature}")

            async with aiofiles.open(desc_path, encoding="utf-8") as desc:
                final_desc = await desc.read()
            final_desc = bbcode.clean_for_ar(final_desc)
            async with aiofiles.open(desc_path, "w", encoding="utf-8") as desc:
                await desc.write(final_desc)
            return

    async def edit_name(self, meta: dict) -> str:
        manual_name = meta.get("manual_name")
        if manual_name:
            return manual_name
        ar_name = meta["name"]
        ar_name = ar_name.replace(":", "").replace("'", "").replace(" ", ".")
        meta[f"{self.tracker}_name"] = ar_name
        return ar_name

    async def get_upload_data(self, meta: dict[str, Any]) -> dict[str, Any]:
        data = await super().get_upload_data(meta)
        data["type"] = data.pop("category")
        data["tags"] = data.pop("taglist")
        return data

    async def search_existing(self, meta: dict[str, Any]) -> list[dict[str, Any]]:
        await self.ensure_authenticated()
        dupes = []
        console.print(f"[yellow]Searching for existing torrents on {self.tracker}...")
        category_id = await self.get_cat_id(meta, for_search=True)
        title = meta.get("title")
        params = {"action": "browse", "searchstr": title, f"filter_cat[{category_id}]": 1}
        search_url = f"{self.domain}/ajax.php?{urlencode(params)}"

        try:
            response = await self.session.get(search_url)
            response.raise_for_status()
            response_json = response.json()
            if response_json.get("status") == "success":
                for torrent in response_json["response"]["results"]:
                    name = torrent["groupName"]
                    size = torrent["size"]
                    file_count = torrent["fileCount"]
                    link = (
                        f"{self.domain}/torrents.php?id={torrent['groupId']}&torrentid={torrent['torrentId']}"
                    )
                    dupes.append(
                        {
                            "name": name,
                            "size": size,
                            "files": None,
                            "file_count": file_count,
                            "trumpable": None,
                            "link": link,
                        }
                    )
        except Exception as e:
            console.print(f"[red]Unable to search for existing torrents: {e}[/red]")
            await asyncio.sleep(5)

        # console.print(Pretty(dupes))
        return dupes
