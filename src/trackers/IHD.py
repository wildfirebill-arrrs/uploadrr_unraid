from __future__ import annotations

from typing import Any, ClassVar

from src.trackers.UNIT3D import Unit3d
from src.utils.console import console


class IHD(Unit3d):
    tracker = "IHD"
    source_flag = "InfinityHD"
    domain = "https://infinityhd.net"
    allowed_imghosts: ClassVar[list[str]] = []
    banned_groups: ClassVar[dict[str, Any]] = [""]

    async def banned_content(self, meta: dict[str, Any]) -> tuple[bool, bool]:
        category = meta["category"]
        resolution = meta.get("resolution")
        sd = meta.get("sd", 0)
        if category not in ("MOVIE", "TV"):
            console.print("Only Movie/TV/Anime Allowed on InfinityHD")
            return True, True
        if sd or resolution == "720p":
            console.print("[bold yellow]Resolution must be atleast 1080p on InfinityHD.")
            return True, True
        return False, False

    # fmt: off
    async def get_cat_id(self, meta: dict[str, Any]) -> str:
        category_name = meta["category"]
        anime = meta.get("anime")
        if anime:
            if category_name == "MOVIE":
                return "4"
            else:
                return "3"
        category_id = {
            "MOVIE": "1",
            "TV": "2",
            "Anime": "3",
            "Anime Movie": "4"
        }.get(category_name, "0")
        return category_id
    # fmt: on

    async def edit_name(self, meta: dict[str, Any]) -> str:
        ihd_name = meta["name"]
        ihd_name = ihd_name.replace("DUAL", "Dual-Audio").replace("MULTI", "Multi")
        meta[f"{self.tracker}_name"] = ihd_name
        return ihd_name
