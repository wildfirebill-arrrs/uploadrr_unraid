from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar

import niquests

from src.screenshots.resizer import MAX_COVER_SIZE, resize_cover_image
from src.trackers.UNIT3D import Unit3d
from src.utils.console import log


class HHD(Unit3d):
    tracker = "HHD"
    source_flag = "HHD"
    domain = "https://homiehelpdesk.net"
    allowed_imghosts: ClassVar[list[str]] = []
    banned_groups: ClassVar[dict[str, Any]] = {  # Updated 25-08-29
        "ALL_TYPES": [
            "aXXo",
            "BONE",
            "BRrip",
            "CM8",
            "CrEwSaDe",
            "CTFOH",
            "dAV1nci",
            "d3g",
            "DNL",
            "FaNGDiNG0",
            "GalaxyTV",
            "HD2DVD",
            "HDTime",
            "iHYTECH",
            "ION10",
            "iPlanet",
            "KiNGDOM",
            "LAMA",
            "MeGusta",
            "mHD",
            "mSD",
            "nHD",
            "NaNi",
            "NhaNc3",
            "nikt0",
            "nSD",
            "OFT",
            "PSA",
            "PRODJi",
            "RARBG",
            "Rifftrax",
            "SANTi",
            "SasukeducK",
            "ShAaNiG",
            "Sicario",
            "STUTTERSHIT",
            "TGALAXY",
            "TORRENTGALAXY",
            "TSP",
            "TSPxL",
            "ViSION",
            "VXT",
            "WAF",
            "WKS",
            "x0r",
            "YAWNiX",
            "YIFY",
            "YTS",
        ],
        "EVO": ["ENCODE", "WEBRIP", "HDTV", "REMUX"],
        "HDT": ["REMUX"],
    }

    # fmt: off
    async def get_cat_id(self, meta: dict[str, Any]) -> str:
        category_name = meta["category"]
        anime = meta.get("anime", False)
        category_id = {
            "MOVIE": "1",
            "TV": "2",
            "Anime": "3",
            "MUSIC": "4",
            "XXX": "10"
        }.get(category_name, "0")
        if anime:
            return "3"
        return category_id

    async def get_type_id(self, meta: dict[str, Any]) -> str:
        media_type = meta["type"]
        type_id = {
            "DISC": "1",
            "REMUX": "2",
            "WEBDL": "4",
            "WEBRIP": "5",
            "ENCODE": "3",
            "HDTV": "6",
            "FLAC": "7",
            "AAC": "8",
            "ALAC": "9",
            "M4A": "10",
            "M4B": "11",
            "MP4": "12",
            "MP3": "13",
            "Other": "7",
        }.get(media_type, "0")
        if media_type in ("OPUS", "WAVE", "MPEG4", "OGG", "AC3", "DSF"):
            type_id = "7"
        return type_id
    # fmt: on

    async def get_extra_files(self, meta: dict[str, Any]) -> dict[str, tuple[str, str, str]]:
        tmp_dir = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
        cover_path = tmp_dir / "cover.jpg"
        banner_path = tmp_dir / "back.jpg"
        imgs = {}
        if meta["category"] == "MUSIC":
            mbid = meta.get("mbid")
            discogs_id = meta.get("discogs_id")
            if mbid or discogs_id:
                return {}
            user_cover = meta.get("user_cover")
            album_cover = meta.get("album_cover")
            cover = user_cover or album_cover
            if cover:
                cover_path_local = Path(cover)
                if cover_path_local.exists():
                    processed = await asyncio.to_thread(self._process_cover, cover_path_local, tmp_dir)
                    return processed
                if isinstance(cover, str) and cover.startswith(("http://", "https://")):
                    downloaded = tmp_dir / "album_cover.jpg"
                    try:
                        async with niquests.AsyncSession() as client:
                            resp = await client.get(cover)
                        if resp.status_code == 200:
                            downloaded.write_bytes(resp.content)
                            processed = await asyncio.to_thread(self._process_cover, downloaded, tmp_dir)
                            return processed
                        log.warning(f"HHD: Failed to download album_cover: HTTP {resp.status_code}")
                    except Exception as e:
                        log.warning(f"HHD: Failed to download album_cover: {e}")

        if cover_path.exists():
            imgs["torrent-cover"] = ("cover.jpg", str(cover_path), "image/jpeg")
        if banner_path.exists():
            imgs["torrent-banner"] = ("banner.jpg", str(banner_path), "image/jpeg")

        return imgs

    @staticmethod
    def _process_cover(cover_path: Path, tmp_dir: Path) -> dict[str, tuple[str, str, str]]:
        if cover_path.stat().st_size > MAX_COVER_SIZE:
            cover_path = resize_cover_image(cover_path, tmp_dir)
        return {"torrent-cover": ("cover.jpg", str(cover_path), "image/jpeg")}

    async def get_extra_data(self, meta: dict[str, Any]) -> dict[str, Any]:
        extras = {}
        if meta["category"] != "MUSIC":
            return extras
        mbid = meta.get("mbid")
        discogs_id = meta.get("discogs_id")
        if mbid:
            extras["musicbrainz"] = mbid
        if discogs_id:
            extras["discogs"] = discogs_id
        return extras
