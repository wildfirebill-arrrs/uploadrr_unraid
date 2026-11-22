from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, ClassVar

from src.progress import ProgressManager
from src.trackers.GAZELLE import Gazelle
from src.utils.console import console, log
from src.utils.torrentman import edit_torrent


class CRT(Gazelle):
    tracker = "CRT"
    type = "luminance"
    source_flag = "CRT"
    domain = "https://www.cathode-ray.tube"
    banned_groups: ClassVar[dict[str, Any]] = []
    allowed_imghosts: ClassVar[list[str]] = ["imgbox.com", "iili.io", "catbox.moe"]
    cinfo = "2550|1350|1.25|24|-60"
    simple_name = True

    async def get_cat_id(self, meta: dict[str, Any]) -> str:
        category_name = meta["category"]
        category_id = {"MOVIE": "1", "TV": "2", "GAMES": "13", "MISC": "4", "WOC": "3"}.get(
            category_name, "0"
        )
        return category_id

    async def banned_content(self, meta: dict[str, Any]) -> tuple[bool, bool]:
        uuid = meta.get("uuid", "")
        adult = meta.get("adult", False)
        forbidden_keywords = {"porn", "hentai", "sex", "xxx", "porno"}
        date_str = meta.get("last_air_date") or meta.get("full_date") or meta.get("year")

        if meta.get("xxx") or (
            ("XXX" in uuid or any(k.lower() in forbidden_keywords for k in meta.get("keywords", [])))
            and adult
        ):
            console.print("[bold red]Porn is strictly prohibited on cathode-ray.tube")
            return True, True

        if date_str:
            try:
                date = datetime.fromisoformat(date_str)
            except ValueError:
                date = datetime(int(date_str), 1, 1)

            cutoff = datetime.now() - timedelta(days=365 * 10)
            if date > cutoff:
                console.print("[bold red]cathode-ray.tube requires uploads to be more than 10 Years old!")
                return True, True

        return False, False

    async def edit_name(self, meta: dict[str, Any]) -> str:
        crt_name = [meta["title"]]
        is_tv = meta["category"] == "TV"
        season_int = meta.get("season_int")
        if aka := meta.get("aka"):
            crt_name.append(aka)
        if is_tv:
            crt_name.append(f"- Season {season_int}")
        if year := meta.get("year"):
            crt_name.append(f"({year})")
        for key in ("edition", "cut", "ratio"):
            if val := meta.get(key):
                crt_name.append(val)
        if is_tv and season_int == 1 and meta.get("total_seasons") == 1:
            crt_name.append("Complete Series")
        crt_name = " ".join(crt_name)
        meta[f"{self.tracker}_name"] = crt_name
        return crt_name

    async def _verify_upload_after_500(self, meta: dict[str, Any]) -> bool:
        import asyncio

        from selectolax.lexbor import LexborHTMLParser

        cat_id = await self.get_cat_id(meta)
        uuid = meta["uuid"]
        params = {f"filter_cat[{int(cat_id)}]": 1, "searchtext": uuid}

        pm = ProgressManager.get_instance()
        # yes it takes this long >.<
        if pm:
            await pm.countdown(60, message_prefix="Waiting for CRT to response..")
        else:
            await asyncio.sleep(60)
        try:
            response = await self.session.get(f"{self.domain}/torrents.php", params=params)
            response.raise_for_status()
            tree = LexborHTMLParser(response.text)
            torrent_table = tree.css_first("table#torrent_table")

            if torrent_table:
                torrent_row = None
                for row in torrent_table.css("tr"):
                    css_classes = row.attributes.get("class", "")
                    if css_classes and "torrent" in css_classes:
                        if torrent_row is not None:
                            log.info("No torrent_table")
                            return False
                        torrent_row = row

                if torrent_row is not None:
                    link = torrent_row.css_first("a[href^='/torrents.php?id=']")
                    if link is not None:
                        href = link.attributes.get("href", "")
                        comment = f"Made by Uploadrr for {self.domain}{href}"
                        await edit_torrent(meta, None, self.tracker, self.source_flag, self.announce, comment)
                        return True

        except Exception as e:
            log.debug(f"Verification failed: {e}")

        return False
