from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar

import niquests

from src.trackers.UNIT3D import Unit3d
from src.utils.console import console, log


class PB(Unit3d):
    tracker = "PB"
    source_flag = "PornBytes"
    domain = "https://pornbytes.site"
    allowed_imghosts: ClassVar[list[str]] = []
    banned_groups: ClassVar[dict[str, Any]] = [""]

    # fmt: off
    async def get_cat_id(self, meta: dict[str, Any]) -> str:
        performers = meta.get("porndb", {}).get("performers", "").split(", ")
        tags = [tag.strip() for tag in meta.get("porndb", {}).get("tags", "").split(",") if tag.strip() and tag.strip() not in performers]

        prio_oder = [
            (["trans", "shemale", "futa", "transexual", "tgirl", "tguy"], "11"),
            (["vr", "virtual reality"], "21"),
            (["gangbang", "orgy"], "10"),
            (["gay", "bisexual", "homosexual", "homo", "bb", "bareback", "all guy", "boy on boy", "man on man"], "17"),
            (["lesbian", "gg", "all girl", "girl on girl"], "7"),
        ]

        for keywords, cat_id in prio_oder:
            for tag in tags:
                if any(keyword == tag.lower() for keyword in keywords):
                    return cat_id

        category_id = {
            "Anal": "1",
            "Amateur": "3",
            "Asian": "2",
            "Blowjob": "18",
            "Creampie": "14",
            "Gang Bang": "10",
            "Gay / Bi": "17",
            "Hardcore": "5",
            "Latina": "6",
            "Lesbian": "7",
            "Milf": "8",
            "Shemale" : "11",
            "Siterip" : "13",
            "Masturbation" : "19",
            "Straight" : "15",
            "Teen" : "16",
            "VR" : "21"
            }
        for tag in tags:
            if tag in category_id:
                return category_id[tag]
        return "15"
    # fmt: on

    async def search_existing(self, meta: dict[str, Any]) -> dict[str, Any]:
        dupes: dict[str, Any] = {}
        console.print(f"[yellow]Searching for existing torrents on {self.tracker}...")

        async def run_search(params: dict[str, Any], label: str) -> dict[str, Any]:
            results: dict[str, Any] = {}
            async with niquests.AsyncSession(timeout=self.timeout, disable_http3=True) as client:
                try:
                    response = await client.get(self.search_url, params=params)
                    if response.status_code == 200:
                        response_json = response.json()
                        for item in response_json.get("data", []):
                            name = item["attributes"]["name"]
                            size = item["attributes"]["size"]
                            results[name] = size
                    else:
                        log.warning(f"[red]Failed to search {label}: HTTP {response.status_code}[/red]")
                except Exception as e:
                    log.error(f"[red]Unable to search {label}: {e}[/red]")
                    await asyncio.sleep(5)
            return results

        # --- Search by file_name (exact match) ---
        if meta.get("video_files"):
            file_name_params = {
                "api_token": self.config["TRACKERS"][self.tracker]["api_key"].strip(),
                "file_name": Path(meta["video_files"][0]).name,
            }
            dupes.update(await run_search(file_name_params, "file_name"))

        # --- Search by title/types/resolutions (broader match) ---
        title_params = {
            "api_token": self.config["TRACKERS"][self.tracker]["api_key"].strip(),
            "resolutions[]": await self.get_res_id(meta),
            "name": meta.get("title", ""),
        }
        dupes.update(await run_search(title_params, "title/resolutions"))
        return dupes
