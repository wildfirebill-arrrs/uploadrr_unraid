from __future__ import annotations

from typing import Any, ClassVar

from src.trackers.UNIT3D import Unit3d


class CBR(Unit3d):
    tracker = "CBR"
    source_flag = "CapybaraBR"
    domain = "https://capybarabr.com"
    allowed_imghosts: ClassVar[list[str]] = []
    banned_groups: ClassVar[dict[str, Any]] = [
        "3LTON",
        "4yEo",
        "ADE",
        "AFG",
        "AROMA",
        "AniHLS",
        "AniURL",
        "AnimeRG",
        "BLUDV",
        "CHD",
        "CM8",
        "Comando",
        "CrEwSaDe",
        "DNL",
        "DeadFish",
        "ELiTE",
        "FGT",
        "FRDS",
        "FUM",
        "FaNGDiNG0",
        "Flights",
        "HAiKU",
        "HD2DVD",
        "HDS",
        "HDTime",
        "Hi10",
        "Hiro360",
        "ION10",
        "JIVE",
        "KiNGDOM",
        "LEGi0N",
        "LOAD",
        "Lapumia",
        "Leffe",
        "MeGusta",
        "NOIVTC",
        "NhaNc3",
        "OFT",
        "Oj",
        "PRODJi",
        "PiRaTeS",
        "PlaySD",
        "RAPiDCOWS",
        "RARBG",
        "RDN",
        "REsuRRecTioN",
        "RMTeam",
        "RetroPeeps",
        "SANTi",
        "SPASM",
        "SPDVD",
        "STUTTERSHIT",
        "SicFoI",
        "TGx",
        "TM",
        "TRiToN",
        "Telly",
        "UPiNSMOKE",
        "URANiME",
        "WAF",
        "XS",
        "YIFY",
        "ZKBL",
        "ZMNT",
        "ZmN",
        "aXXo",
        "d3g",
        "eSc",
        "iPlanet",
        "mHD",
        "mSD",
        "nHD",
        "nSD",
        "nikt0",
        "playXD",
        "x0r",
        "xRed",
    ]

    async def get_cat_id(self, meta: dict[str, Any]) -> str:
        category_name = meta["category"]
        keywords = meta.get("keywords") or ""
        anime = meta.get("anime", False)
        keywords = list(map(str.strip, keywords.lower().split(",")))
        category_id = {"MOVIE": "1", "TV": "2", "Anime": "4"}.get(category_name, "0")
        if category_name == "MOVIE":
            category_id = "4" if anime or "anime" in keywords else "1"
        elif category_name == "TV":
            category_id = "4" if anime or "anime" in keywords else "2"
        return category_id
