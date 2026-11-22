from __future__ import annotations

import asyncio
import json
from typing import Any, ClassVar

import niquests

from src.trackers.UNIT3D import Unit3d
from src.utils.console import console, log


class ZDF(Unit3d):
    tracker = "ZDF"
    source_flag = "0DayFiles"
    domain = "https://0dayfiles.net"
    allowed_imghosts: ClassVar[list[str]] = []
    banned_groups: ClassVar[dict[str, Any]] = {}

    # fmt: off
    async def get_cat_id(self, meta: dict[str, Any]) -> str:
        # Category taxonomy derived from 0DayFiles (UNIT3D v9.2.0)
        if meta.get("is_music", False):
            return "38"
        if meta.get("anime", False) or meta.get("mal_id"):
            return "43"
        category = (meta.get("category") or "").upper()
        resolution = meta.get("resolution", "")
        media_type = (meta.get("type") or "").upper()
        is_sd = resolution in ("480p", "480i", "576p", "576i")
        is_hevc = any(
            t in (meta.get("video_codec", "") + " " + meta.get("video_encode", "")).upper()
            for t in ("HEVC", "H.265", "H265", "X265", "265")
        )
        source = (meta.get("source") or "").lower()
        tv_pack = meta.get("tv_pack", 0)

        if category == "TV":
            if tv_pack:
                return "28"
            if resolution in ("2160p", "4320p"):
                return "22"
            if is_sd:
                return "21"
            if media_type in ("DISC", "REMUX"):
                return "23"
            if media_type == "ENCODE" and is_hevc:
                return "31"
            if media_type == "ENCODE":
                return "30"
            if source == "bluray":
                return "23"
            return "59"

        # MOVIE (default)
        if resolution in ("2160p", "4320p"):
            return "10"
        if is_sd:
            return "9"
        if media_type == "DISC":
            return "12"
        if media_type == "REMUX":
            return "11"
        if media_type == "ENCODE" and is_hevc:
            return "19"
        if media_type == "ENCODE":
            return "18"
        if source == "bluray":
            return "11"
        return "58"

    async def get_type_id(self, meta: dict[str, Any]) -> str:
        # 0DayFiles encodes type in category_id; API still expects a value.
        return "0"

    async def get_res_id(self, meta: dict[str, Any]) -> str:
        return "0"
    # fmt: on

    async def search_existing(self, meta: dict[str, Any]) -> dict[str, Any]:
        dupes: dict[str, Any] = {}
        console.print(f"[yellow]Searching for existing torrents on {self.tracker}...")

        cat_id = await self.get_cat_id(meta)
        params = {
            "api_token": self.config["TRACKERS"][self.tracker]["api_key"].strip(),
            "tmdbId": meta.get("tmdb", 0),
            "categories[]": cat_id,
            "name": "",
        }
        if meta.get("category") == "TV":
            params["name"] = f" {meta.get('season', '')}{meta.get('episode', '')}"

        async with niquests.AsyncSession(timeout=self.timeout, disable_http3=True) as client:
            try:
                response = await client.get(self.search_url, params=params)
                if response.status_code == 200:
                    response_json = response.json()
                    for item in response_json.get("data", []):
                        name = item["attributes"]["name"]
                        size = item["attributes"]["size"]
                        dupes[name] = size
                else:
                    log.error(f"[red]Failed to search: HTTP {response.status_code}[/red]")
            except Exception as e:
                console.print(
                    f"[bold red]Unable to search for existing torrents on site. "
                    f"Either the site is down or your API key is incorrect. Error: {e}[/bold red]"
                )
                await asyncio.sleep(5)

        return dupes