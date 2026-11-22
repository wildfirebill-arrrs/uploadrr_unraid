from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import niquests

from src.screenshots.resizer import MAX_COVER_SIZE, resize_cover_image
from src.trackers.UNIT3D import Unit3d
from src.utils.console import console, log


class ZNTH(Unit3d):
    tracker = "ZNTH"
    source_flag = "znth.cx"
    domain = "https://znth.cx"
    allowed_imghosts: ClassVar[list[str]] = []
    # fallback if API is unavailable
    banned_groups: ClassVar[dict[str, Any]] = [  # updated on 26-05-29 == https://znth.cx/wikis/2
        "4K4U", "Alcaide_Kira", "AROMA", "aXXo", "BRrip", "CM8", "CrEwSaDe", "d3g", "DNL", "EVO",
        "FaNGDiNG0", "FGT", "FRDS", "GalaxyTV", "HD2DVD", "HDTime", "Hi10", "ION10", "iPlanet",
        "KiNGDOM", "LAMA", "MeGusta", "mHD", "mSD", "NhaNc3", "nHD", "nikt0", "nSD", "OFT",
        "PRODJi", "RARBG", "SANTi", "SPDVD", "STUTTERSHIT", "Telly", "TGx", "TSP", "TSPxL",
        "WAF", "x0r", "YIFY", "YTS",
    ]  # fmt:off

    async def get_cat_id(self, meta: dict[str, Any]) -> str:
        category = meta["category"]
        music_files = meta.get("music_files", {})
        if category == "MUSIC" and len(music_files) == 1:
            return "4"
        category_id = {"MOVIE": "1", "TV": "2", "MUSIC": "5"}.get(category, "9")
        return category_id

    async def get_type_id(self, meta: dict[str, Any]) -> str:
        media_type = meta["type"]
        type_id = {
            "DISC": "1",
            "REMUX": "2",
            "WEBDL": "4",
            "WEBRIP": "5",
            "UHDTV": "9",
            "HDTV": "6",
            "SDTV": "10",
            "ENCODE": "3",
            "FLAC": "7",
            "MP3": "8",
            "DVDRIP": "11"
        }.get(media_type, "0")  # fmt:off
        return type_id

    async def banned_content(self, meta: dict[str, Any]) -> tuple[bool, bool]:
        uuid = meta.get("uuid", "")
        adult = meta.get("adult", False)
        keywords = meta.get("keywords") or ""
        keyword_list = [k.strip() for k in keywords.lower().split(",")]
        if meta.get("xxx") or (
            (
                "XXX" in uuid
                or any(
                    keyword in keyword_list for keyword in ["porn", "hentai", "erotic", "animation", "sex"]
                )
            )
            and adult
        ):
            console.print("[bold red]Zenith strictly prohibits Porn/Hentai.")
            return True, True
        return False, False

    async def edit_name(self, meta: dict[str, Any]) -> str:
        if meta.get("category") != "MUSIC":
            name = meta["name"]
            if meta.get("lang") == "DUAL":
                name = name.replace(" DUAL ", " Dual-Audio ")
            if (tag := meta.get("tag")) and tag.upper() in ("-NOGRP", "-NOGROUP"):
                name = name.removesuffix(tag).strip()
            if meta.get("category") == "TV" and not meta.get("same_name_series"):
                year = meta.get("year", "")
                if year and f" {year}" in name:
                    name = name.replace(f" {year}", "", 1)
            name = name.replace("DDP", "DD+")
            name = " ".join(name.split())
            meta[f"{self.tracker}_name"] = name
            return name

        from src.name import NameGenerator

        custom_templates = {
            "MUSIC": {
                "lossless": "{artist} - {album} ({edition} {year}) - [{service} {source} {type} {bit_depth} {sampling_rate}]",
                "lossy": "{artist} - {album} ({edition} {year}) - [{service} {source} {type} {bitrate}]",
            }
        }

        name = await NameGenerator().generate_tracker_name(meta, self.tracker, custom_templates)
        if (tag := meta.get("tag", "")) and tag.upper() in ("-NOGRP", "-NOGROUP"):
            name = name.removesuffix(tag).strip()
        meta[f"{self.tracker}_name"] = name
        return name

    async def get_extra_files(self, meta: dict[str, Any]) -> dict[str, tuple[str, str, str]]:
        if not meta.get("is_music"):
            return {}

        cover = meta.get("user_cover") or meta.get("album_cover")
        if not cover:
            return {}

        tmp_dir = Path(meta["base_dir"]) / "tmp" / meta["uuid"]

        cover_path = Path(cover)
        if cover_path.exists():
            return self._cover_payload(cover_path, tmp_dir)

        if isinstance(cover, str) and cover.startswith(("http://", "https://")):
            try:
                async with niquests.AsyncSession() as client:
                    resp = await client.get(cover)

                if resp.status_code != 200:
                    log.warning(f"ZNTH: Failed to download cover: HTTP {resp.status_code}")
                    return {}

                cover_path = tmp_dir / "album_cover.jpg"
                cover_path.write_bytes(resp.content)

                return self._cover_payload(cover_path, tmp_dir)

            except Exception as e:
                log.warning(f"ZNTH: Failed to download cover: {e}")

        return {}

    @staticmethod
    def _cover_payload(
        cover_path: Path,
        tmp_dir: Path,
    ) -> dict[str, tuple[str, str, str]]:
        if cover_path.stat().st_size > MAX_COVER_SIZE:
            cover_path = resize_cover_image(cover_path, tmp_dir)

        mime = "image/png" if cover_path.suffix.lower() == ".png" else "image/jpeg"

        return {
            "torrent-cover": (
                cover_path.name,
                str(cover_path),
                mime,
            )
        }
