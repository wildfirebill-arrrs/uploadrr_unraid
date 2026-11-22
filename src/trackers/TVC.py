from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, ClassVar

import aiofiles
import niquests

from src.utils.bbcode import BBCODE
from src.utils.console import console, log
from src.utils.torrentman import edit_torrent
from src.version import user_agent

from .UNIT3D import Unit3d


class TVC(Unit3d):
    tracker = "TVC"
    source_flag = "TVCHAOS"
    domain = "https://tvchaosuk.com"
    allowed_imghosts: ClassVar[list[str]] = []
    banned_groups: ClassVar[dict[str, Any]] = []

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.torrent_url = f"{self.domain}/torrents/"
        self.tmdb_semaphore = asyncio.Semaphore(40)
        self.tmdb_client = niquests.AsyncSession(
            base_url="https://api.themoviedb.org/3", timeout=10.0, disable_http3=True
        )
        self.tmdb_api_key = config["DEFAULT"]["tmdb"]
        self.images = {
            "imdb75": "https://ptpimg.me/2fj3q7.png",
            "tmdb75": "https://ptpimg.me/483260.png",
            "tvdb75": "https://ptpimg.me/1b2g30.png",
            "tvm75": "https://ptpimg.me/51439c.png",
            "mal75": "https://ptpimg.me/s3p634.png",
        }

    async def get_cat_id(self, meta: dict[str, Any]) -> str:
        genre_to_id = {
            "comedy": "29",
            "documentary": "5",
            "drama": "11",
            "entertainment": "14",
            "factual": "19",
            "foreign": "42",
            "kids": "32",
            "movies": "44",
            "news": "45",
            "radio": "51",
            "reality": "52",
            "soaps": "30",
            "sci-fi": "33",
            "sport": "42",
            "holding bin": "53",
        }
        genres = (meta.get("genres") or "").split(", ")
        for genre in genres:
            genre = genre.lower().replace(",", "")
            for tv_type, cat_id in genre_to_id.items():
                if tv_type in genre:
                    return cat_id
        return "14" if meta.get("category") == "TV" else "44"

    async def get_res_id(self, meta: dict[str, Any]) -> str:
        resolution = meta["resolution"]
        tv_pack = meta.get("tv_pack", False)
        resolution_map = {
            "1080p": "HD1080p Pack" if tv_pack else "HD1080p",
            "1080i": "HD1080p Pack" if tv_pack else "HD1080p",
            "720p": "HD720p Pack" if tv_pack else "HD720p",
            "576p": "SD Pack" if tv_pack else "SD",
            "576i": "SD Pack" if tv_pack else "SD",
            "540p": "SD Pack" if tv_pack else "SD",
            "540i": "SD Pack" if tv_pack else "SD",
            "480p": "SD Pack" if tv_pack else "SD",
            "480i": "SD Pack" if tv_pack else "SD",
        }
        return resolution_map.get(resolution, "SD")

    async def tmdb_rate_limit(self) -> None:
        async with self.tmdb_semaphore:
            await asyncio.sleep(0.25)

    async def get_type_id(self, meta: dict[str, Any]) -> str:
        media_type = meta["type"]
        if media_type == "ENCODE" and (
            "bluray" in meta["uuid"].lower()
            or "brrip" in meta["uuid"].lower()
            or "bdrip" in meta["uuid"].lower()
        ):
            return "BRRip"
        return media_type.replace("WEBDL", "WEB-DL")

    async def edit_name(self, meta: dict[str, Any]) -> str:
        year = meta["year"]
        if meta.get("no_year", False):
            year = ""
        elif meta.get("search_year", ""):
            year = meta["search_year"]

        type_ = await self.get_type_id(meta)
        video_codec = str(meta["video"][-3:]).upper()
        tvc_name = meta["title"]

        if meta["category"] == "MOVIE":
            tvc_name = f"{meta['title']} ({year}) [{meta['resolution']} {type_} {video_codec}]"
        else:
            season = "" if meta.get("no_season", False) else meta["season"]

            if meta["tv_pack"]:
                season_year = (
                    meta["season_air_first_date"][:4]
                    if "season_air_first_date" in meta and len(meta["season_air_first_date"]) >= 4
                    else year
                )
                tvc_name = f"{meta['title']} ({season_year}) Series {meta['season_int']} [{meta['resolution']} {type_} {video_codec}]"
            else:
                episode_part = f"{season}{meta['episode']}"
                if "episode_airdate" in meta:
                    episode_part += f" ({meta['episode_airdate']})"
                tvc_name = (
                    f"{meta['title']} ({year}) {episode_part} [{meta['resolution']} {type_} {video_codec}]"
                )

        # Clean up extra spaces and empty parentheses
        tvc_name = tvc_name.replace("  ", " ").replace(" () ", " ")

        # Add codec and subtitle tags
        if meta.get("video_codec") == "HEVC":
            tvc_name = tvc_name.replace("]", " HEVC]")
        if meta.get("eng_subs", False):
            tvc_name = tvc_name.replace("]", " SUBS]")
        if meta.get("sdh_subs", False):
            if meta.get("eng_subs", False):
                tvc_name = tvc_name.replace(" SUBS]", " (ENG + SDH SUBS)]")
            else:
                tvc_name = tvc_name.replace("]", " (SDH SUBS)]")

        # Add country tags
        country_codes = meta.get("origin_country_code", [])
        country_tags = {"IE": "IRL", "AU": "AUS", "NZ": "NZ", "CA": "CA"}
        for code in country_codes:
            if code in country_tags:
                tvc_name += f" [{country_tags[code]}]"

        meta[f"{self.tracker}_name"] = tvc_name
        return tvc_name

    async def banned_content(self, meta: dict[str, Any]) -> tuple[bool, bool]:
        if meta["resolution"] == "2160p":
            console.print("[bold red]No 4K at TVC -.-")
            return True, True
        if meta.get("is_disc", False):
            console.print("[bold red]Discs are not allowed at TVC!")
            return True, True
        if meta["type"] == "REMUX":
            console.print("[bold red]No Remuxes at TVC, wyd?")
            return True, True
        if meta.get("video_codec") == "HEVC" and meta["resolution"] != "1080p":
            console.print("[bold red]HEVC encodes MUST be 1080p at TVC.")
            return True, True
        return False, False

    async def get_tmdb_data(self, meta: dict[str, Any]) -> None:
        try:
            await self.tmdb_rate_limit()
            if meta["category"] == "MOVIE":
                response = await self.tmdb_client.get(
                    f"/movie/{meta['tmdb']}", params={"api_key": self.tmdb_api_key}
                )
                response.raise_for_status()
                data = response.json()
            else:
                response = await self.tmdb_client.get(
                    f"/tv/{meta['tmdb']}", params={"api_key": self.tmdb_api_key}
                )
                response.raise_for_status()
                data = response.json()
                if "networks" in data and data["networks"] and "name" in data["networks"][0]:
                    meta["networks"] = data["networks"][0]["name"]

            # Fetch episode or season info for TV
            if meta["category"] == "TV":
                if meta.get("tv_pack", False):
                    await self.tmdb_rate_limit()
                    season_response = await self.tmdb_client.get(
                        f"/tv/{meta['tmdb']}/season/{meta['season_int']}",
                        params={"api_key": self.tmdb_api_key},
                    )
                    season_response.raise_for_status()
                    season_data = season_response.json()
                    meta["season_air_first_date"] = season_data.get("air_date", f"{meta['year']}-N/A-N/A")
                    meta["first_air_date"] = data.get("first_air_date")
                else:
                    await self.tmdb_rate_limit()
                    episode_response = await self.tmdb_client.get(
                        f"/tv/{meta['tmdb']}/season/{meta['season_int']}/episode/{meta['episode_int']}",
                        params={"api_key": self.tmdb_api_key},
                    )
                    episode_response.raise_for_status()
                    episode_data = episode_response.json()
                    meta["episode_airdate"] = episode_data.get("air_date", "")
                    meta["episode_name"] = episode_data.get("name", "")
                    meta["episode_overview"] = episode_data.get("overview", "")

            # Fetch release dates for movies
            if meta["category"] == "MOVIE":
                await self.tmdb_rate_limit()
                release_response = await self.tmdb_client.get(
                    f"/movie/{meta['tmdb']}/release_dates", params={"api_key": self.tmdb_api_key}
                )
                release_response.raise_for_status()
                meta["release_dates"] = release_response.json()

            # Determine origin country code
            meta["origin_country_code"] = []
            if data.get("origin_country"):
                meta["origin_country_code"] = (
                    data["origin_country"]
                    if isinstance(data["origin_country"], list)
                    else [data["origin_country"]]
                )
            elif data.get("production_countries"):
                meta["origin_country_code"] = [
                    i["iso_3166_1"] for i in data["production_countries"] if "iso_3166_1" in i
                ]
            elif data.get("production_companies"):
                meta["origin_country_code"] = [data["production_companies"][0].get("origin_country", "")]

        except Exception as e:
            console.print(f"[red]Unable to get TMDB data: {e}[/red]")
            if meta["category"] == "TV" and not meta.get("tv_pack", False):
                console.print(
                    f"[red]Ensure episode {meta['season']}{meta['episode']} exists in TMDB: "
                    f"https://www.themoviedb.org/{meta['category'].lower()}/{meta['tmdb']}/season/{meta['season_int']}[/red]"
                )
            meta["season_air_first_date"] = f"{meta['year']}-N/A-N/A"
            meta["first_air_date"] = f"{meta['year']}-N/A-N/A"

    async def edit_desc(
        self, meta: dict[str, Any], tracker: str, signature: str = "", comparison: bool = False
    ) -> None:
        from src.utils.descgen import DescGenerator

        descgen = DescGenerator(config=self.config, base_dir=Path(meta["base_dir"]))
        bbcode = BBCODE()
        desc_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / "DESCRIPTION.txt"
        base = desc_path.read_text(encoding="utf-8") if desc_path.exists() else ""
        desc = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / f"[{tracker}]DESCRIPTION.txt"
        async with aiofiles.open(desc, "w", encoding="utf-8") as descfile:
            # Write disc info using DescGenerator
            await descgen._write_disc_info(descfile, meta.get("discs", []))

            # Write release info
            rd_info = ""
            if meta["category"] != "TV" and meta.get("release_dates"):
                for cc in meta["release_dates"]["results"]:
                    for rd in cc["release_dates"]:
                        if rd["type"] == 6:
                            channel = rd.get("note", "N/A Channel")
                            rd_info += (
                                f"[color=orange][size=15]{cc['iso_3166_1']} TV Release info[/size][/color]\n"
                                f"{rd['release_date'][:10]} on {channel}\n"
                            )
            if rd_info:
                descfile.write("[color=green][size=25]Release Info[/size][/color]\n\n" + rd_info + "\n\n")
            elif meta["category"] == "TV" and meta.get("tv_pack", False) and meta.get("first_air_date"):
                channel = meta.get("networks", "N/A")
                descfile.write(
                    "[color=green][size=25]Release Info[/size][/color]\n\n"
                    f"[color=orange][size=15]First episode of this season aired {meta['season_air_first_date']} on channel {channel}[/size][/color]\n\n"
                )
            elif meta["category"] == "TV" and not meta.get("tv_pack", False) and meta.get("episode_airdate"):
                channel = meta.get("networks", "N/A")
                descfile.write(
                    "[color=green][size=25]Release Info[/size][/color]\n\n"
                    f"[color=orange][size=15]Episode aired on channel {channel} on {meta['episode_airdate']}[/size][/color]\n\n"
                )
            else:
                descfile.write(
                    "[color=green][size=25]Release Info[/size][/color]\n\n"
                    "[color=orange][size=15]TMDB has No TV release info for this[/size][/color]\n\n"
                )

            # Write plot
            if meta["category"] == "TV" and not meta.get("tv_pack", False) and meta.get("episode_overview"):
                descfile.write(
                    f"[color=green][size=25]PLOT[/size][/color]\nEpisode Name: {meta['episode_name']}\n{meta['episode_overview']}\n\n"
                )
            else:
                descfile.write(
                    "[color=green][size=25]PLOT[/size][/color]\n" + meta.get("overview", "") + "\n\n"
                )

            # Write base description (notes/extra info)
            if base and meta.get("description") != "PTP":
                desc = bbcode.convert_pre_to_code(base)
                desc = bbcode.convert_hide_to_spoiler(desc)
                if not comparison:
                    desc = bbcode.convert_comparison_to_collapse(desc, 1000)
                descfile.write("[color=green][size=25]Notes/Extra Info[/size][/color]\n\n" + desc + "\n\n")

            # Write links
            descfile.write(self.get_links(meta, "[color=green][size=25]", "[/size][/color]"))

            # Write images (limit to 2 per TVC rules)
            meta["screens"] = min(int(meta.get("screens", 0)), 2)  # Enforce TVC's max 2 screenshots
            await descgen._write_images(
                descfile, meta, tracker, whitelist=self.allowed_imghosts, img_size=350
            )

            # Write signature
            await descgen._write_signature(descfile, meta, tracker)

    def get_links(self, meta: dict[str, Any], subheading: str, heading_end: str) -> str:
        """Generate BBCode links for IMDb, TMDB, TVDB, TVMaze, and MAL."""
        description = f"\n\n{subheading}Links{heading_end}\n"
        if meta.get("imdb_id", "0") != "0":
            description += f"[URL=https://www.imdb.com/title/tt{meta['imdb_id']}][img]{self.images['imdb75']}[/img][/URL]"
        if meta.get("tmdb", "0") != "0":
            description += f" [URL=https://www.themoviedb.org/{meta['category'].lower()}/{meta['tmdb']}][img]{self.images['tmdb75']}[/img][/URL]"
        if meta.get("tvdb_id", 0) != 0:
            description += f" [URL=https://www.thetvdb.com/?id={meta['tvdb_id']}&tab=series][img]{self.images['tvdb75']}[/img][/URL]"
        if meta.get("tvmaze_id", 0) != 0:
            description += f" [URL=https://www.tvmaze.com/shows/{meta['tvmaze_id']}][img]{self.images['tvm75']}[/img][/URL]"
        if meta.get("mal_id", 0) != 0:
            description += f" [URL=https://myanimelist.net/anime/{meta['mal_id']}][img]{self.images['mal75']}[/img][/URL]"
        return description + "\n\n"

    def get_subs_info(self, meta: dict[str, Any], mi: dict[str, Any]) -> None:
        """Extract subtitle information from MediaInfo for naming purposes."""
        subs_num = sum(1 for track in mi.get("media", {}).get("track", []) if track["@type"] == "Text")
        meta["has_subs"] = 1 if subs_num >= 1 else 0
        meta["eng_subs"] = 0
        meta["sdh_subs"] = 0

        for track in mi.get("media", {}).get("track", []):
            if track["@type"] == "Text" and "Language" in track:
                if track["Language"].lower() == "en":
                    meta["eng_subs"] = 1
                if "sdh" in str(track).lower():
                    meta["sdh_subs"] = 1

    async def upload(self, meta: dict[str, Any]) -> bool:
        await self.get_tmdb_data(meta)
        await self.edit_desc(meta, self.tracker)
        cat_id = await self.get_cat_id(meta)
        type_id = await self.get_type_id(meta)

        tmp_dir = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
        mi_dump = None
        bd_dump = None
        if meta.get("bdinfo") is not None:
            bd_dump = (tmp_dir / "BD_SUMMARY_00.txt").read_text(encoding="utf-8")
        else:
            mi_dump = (tmp_dir / "MEDIAINFO.txt").read_text(encoding="utf-8")
        desc = (tmp_dir / f"[{self.tracker}]DESCRIPTION.txt").read_text(encoding="utf-8")
        torrent_path = tmp_dir / f"[{self.tracker}].torrent"

        if not meta.get("is_disc", False):
            with open(tmp_dir / "MediaInfo.json", encoding="utf-8") as f:
                mi = json.load(f)
            self.get_subs_info(meta, mi)

        anon = (
            1
            if meta.get("anon", 0)
            or self.config["TRACKERS"][self.tracker].get("anon", False)
            or self.config["DEFAULT"].get("always_anon", False)
            else 0
        )

        # Build payload
        name = meta.get(f"{self.tracker}_name") or await self.edit_name(meta)
        data = {
            "name": name,
            "description": desc.replace("\n", "<br>").replace("\r", "<br>"),
            "mediainfo": mi_dump,
            "bdinfo": bd_dump,
            "category_id": cat_id,
            "type": type_id,
            "tmdb": meta.get("tmdb", 0),
            "imdb": meta.get("imdb_id", 0),
            "tvdb": meta.get("tvdb_id", 0),
            "mal": meta.get("mal_id", 0),
            "igdb": 0,
            "anonymous": anon,
            "stream": meta.get("stream", 0),
            "sd": meta.get("sd", 0),
            "keywords": meta.get("keywords", ""),
            "personal_release": int(meta.get("personalrelease", False)),
            "internal": 0,
            "featured": 0,
            "free": 0,
            "doubleup": 0,
            "sticky": 0,
        }
        if meta.get("category") == "TV":
            data["season_number"] = int(meta.get("season_int", 0))
            data["episode_number"] = int(meta.get("episode_int", 0))

        headers = {"User-Agent": user_agent()}
        params = {"api_token": self.config["TRACKERS"][self.tracker]["api_key"].strip()}

        if meta.get("debug", False):
            log.debug("[magenta]DATA 2 SEND[/magenta]:")
            console.print(data)
            if not meta.get("nohash", False):
                await edit_torrent(
                    meta,
                    self.tracker,
                    "DEBUG",
                    "https://TVCHAOSUK.DEBUG/INFOHASH/DEBUG-ONLY",
                    "Uploadrr DEBUG-MODE",
                )
            else:
                log.debug("[magenta]SKIPPING torrent edit — nohash mode, no BASE.torrent[/magenta]")
            return "DEBUG"

        async with niquests.AsyncSession(timeout=self.timeout, disable_http3=True) as client:
            files = {}
            with open(torrent_path, "rb") as torrent_file:
                files["torrent"] = ("torrent.torrent", torrent_file, "application/x-bittorrent")

                try:
                    response = await client.post(
                        self.upload_url, data=data, files=files, headers=headers, params=params
                    )
                    response.raise_for_status()
                    # TVC returns response prefixed with 'application/x-bittorrent\n'
                    response_text = response.text.removeprefix("application/x-bittorrent\n")
                    try:
                        response_json = json.loads(response_text)
                    except ValueError:
                        log.error(f"Unexpected response:\n{response_text}")
                        return False

                    success = response_json.get("success", False)
                    if success:
                        torrent_url = response_json.get("data", "")
                        if torrent_url:
                            # Update torrent file
                            async with niquests.AsyncSession(
                                timeout=self.timeout, disable_http3=True
                            ) as client:
                                torrent_response = await client.get(torrent_url)
                                torrent_response.raise_for_status()
                                torrent_data = torrent_response.content
                                new_torrent = tmp_dir / f"[{self.tracker}]{meta['clean_name']}.torrent"
                                new_torrent.write_bytes(torrent_data)
                                console.print("[bold green]Torrent uploaded successfully!")
                        return True
                    else:
                        message = response_json.get("message", "No message provided")
                        console.print(f"[red]Upload failed: {message}[/red]")
                        if "data" in response_json:
                            console.print(f"[cyan]Error details:[/cyan] {response_json['data']}")
                        return False
                except niquests.HTTPError as http_err:
                    log.error(f"HTTP error during upload: {http_err}")
                    console.print(f"[red]Upload failed: {http_err.response.text}[/red]")
                    return False
                except Exception as e:
                    log.error(f"[red]Unexpected error during upload: {e}[/red]")
                    return False

    async def search_existing(self, meta: dict[str, Any]) -> dict[str, Any]:
        """Search for existing torrents on TVC. Currently disabled due to API issues."""
        log.warning("[red]Cannot search for dupes as search API is not working...[/red]")
        log.warning("[red]Please make sure you are not uploading duplicates.[/red]")
        return {}
