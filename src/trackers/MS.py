from __future__ import annotations

from typing import Any, ClassVar

from src.trackers.UNIT3D import Unit3d
from src.utils.console import console


class MS(Unit3d):
    tracker = "MS"
    source_flag = "MidnightScene"
    domain = "https://midnightscene.cc"
    allowed_imghosts: ClassVar[list[str]] = []
    # fmt:off
    banned_groups: ClassVar[dict[str, Any]] =  [ # updated 26-05-21 == https://midnightscene.cc/wikis/3
        "4K4U", "AROMA", "aXXo", "BONE", "BRrip", "CK4", "CM8", "core", "CrEwSaDe",
        "d3g", "DNL", "EMBER", "EVO", "FaNGDiNG0", "FGT", "FooKaS", "FRDS", "FROZEN",
        "GalaxyRG", "Grym", "GrymLegacy", "HD2DVD", "HDTime", "ION10", "Judas", "LAMA",
        "Leffe", "LycanHD", "MeGusta", "MezRips", "mHD", "msd", "mSD", "NeXus", "NhaNc3",
        "nHD", "nikt0", "nSD", "OFT", "OsC", "PRODJi", "ProRes", "PYC", "QxR", "RARBG",
        "RCDiVX", "RDN", "SAMPA", "SANTi", "Sicario", "Silence", "SM737", "STUTTERSHIT",
        "Tigole", "TSP", "TSPxL", "UTR", "ViSION", "WAF", "Will1869", "x0r", "YIFY", "YTS", "ZMNT"
    ]
    # fmt:on

    async def edit_name(self, meta: dict[str, Any]) -> str:
        if meta.get("category") != "MUSIC":
            meta[f"{self.tracker}_name"] = meta["name"]
            return meta["name"]
        from src.name import NameGenerator

        template = {
            "lossless": "{artist} - {album} ({year}) [{catalog} {edition}] [{service} {source} {type} {bit_depth} {sampling_rate}]{tag}",
            "lossy": "{artist} - {album} ({year}) [{catalog} {edition}] [{service} {source} {type} {bitrate}]{tag}",
        }
        tag = meta.get("tag", "")
        overrides = {"catalog": meta.get("catalog", "")}
        if not tag or tag.upper() in ("-NOGRP", "-NOGROUP"):
            overrides["tag"] = ""
        name = await NameGenerator().generate_tracker_name(meta, self.tracker, template, overrides)
        meta[f"{self.tracker}_name"] = name
        return name

    async def banned_content(self, meta: dict[str, Any]) -> tuple[bool, bool]:
        path = meta.get("uuid", "")
        adult = meta.get("adult", False)
        keywords = meta.get("keywords") or ""
        keyword_list = [k.strip() for k in keywords.lower().split(",")]
        if meta.get("xxx") or (
            (
                "XXX" in path
                or any(
                    keyword in keyword_list for keyword in ["porn", "hentai", "erotic", "animation", "sex"]
                )
            )
            and adult
        ):
            console.print("[bold red]MidnightScene strictly prohibits Porn/Hentai.")
            return True, True
        elif adult:
            console.print(
                "[bold red]Flagged as adult content. MidnightScene generally doesn't permit this content. Please check rules."
            )
            return True, False
        elif "upscale" in path or "telesync" in path:
            console.print(
                "[bold red]Flagged as banned content. MidnightScene doesn't allow TELESYNC nor Upscales. Is this allowed?"
            )
            return True, False
        return False, False

    async def get_type_id(self, meta: dict[str, Any]) -> str:
        media_type = meta["type"]
        genres = {g.strip().lower() for g in (meta.get("genres") or "").split(",") if g.strip()}
        if "documentary" in genres:
            return "13"
        type_id = {
            "DISC": "1",
            "REMUX": "2",
            "WEBDL": "4",
            "WEBRIP": "5",
            "HDTV": "6",
            "ENCODE": "3",
            "FLAC": "8",
            "MP3": "7",
        }.get(media_type, "16")
        return type_id
