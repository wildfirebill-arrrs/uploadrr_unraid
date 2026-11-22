from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any, ClassVar

import aiofiles
from rich.pretty import Pretty
from rich.prompt import Confirm

from src.progress import ProgressManager
from src.trackers.UNIT3D import Unit3d
from src.utils.console import console, log
from src.utils.descgen import DescGenerator
from src.utils.torrentman import edit_torrent


class HUNO(Unit3d):
    tracker = "HUNO"
    source_flag = "HUNO"
    domain = "https://hawke.uno"
    allowed_imghosts: ClassVar[list[str]] = []
    banned_groups: ClassVar[dict[str, list[str]]] = {
        "4K4U": ["REMUX", "WEBDL", "WEBRIP", "ENCODE"],
        "FGT": ["REMUX", "WEBDL", "WEBRIP", "ENCODE"],
        "Telly": ["REMUX", "WEBDL", "WEBRIP"],
        "AHAVA": ["WEBDL", "WEBRIP"],
        "EVO": ["WEBDL", "WEBRIP", "ENCODE"],
        "WKS": ["WEBDL", "WEBRIP"],
        "Bearfish": ["ENCODE"],
        "BiTOR": ["ENCODE"],
        "BONE": ["ENCODE"],
        "D3FiL3R": ["ENCODE"],
        "d3g": ["ENCODE"],
        "DTR": ["ENCODE"],
        "ELiTE": ["ENCODE"],
        "eztv": ["ENCODE"],
        "EzzRips": ["ENCODE"],
        "HashMiner": ["ENCODE"],
        "HETeam": ["ENCODE"],
        "HEVCBay": ["ENCODE"],
        "HiQVE": ["ENCODE"],
        "HR-DR": ["ENCODE"],
        "iFT": ["ENCODE"],
        "ION265": ["ENCODE"],
        "iVy": ["ENCODE"],
        "JATT": ["ENCODE"],
        "Joy": ["ENCODE"],
        "LAMA": ["ENCODE"],
        "m3th": ["ENCODE"],
        "MeGusta": ["ENCODE"],
        "MRN": ["ENCODE"],
        "Musafirboy": ["ENCODE"],
        "OEPlus": ["ENCODE"],
        "Pahe.in": ["ENCODE"],
        "PHOCiS": ["ENCODE"],
        "PoF": ["ENCODE"],
        "PSA": ["ENCODE"],
        "RARBG": ["ENCODE"],
        "RMTeam": ["ENCODE"],
        "ShieldBearer": ["ENCODE"],
        "SiQ": ["ENCODE"],
        "TBD": ["ENCODE"],
        "TSP": ["ENCODE"],
        "VXT": ["ENCODE"],
        "YAWNiX": ["ENCODE"],
        "YIFY": ["ENCODE"],
        "YTS": ["ENCODE"],
    }

    async def banned_content(self, meta: dict[str, Any]) -> tuple[bool, bool]:
        pm = ProgressManager.get_instance()

        # Reject WEBDL/WEBRIP with no streaming service
        if meta.get("type") in {"WEBDL", "WEBRIP"} and not meta.get("service"):
            console.print("[bold red]WEBDL/WEBRIP requires a streaming service for HUNO.")
            return True, True

        # Reject non-HEVC ENCODE/WEBRIP
        if meta.get("video_codec", "") != "HEVC" and meta["type"] in {"ENCODE", "WEBRIP"}:
            console.print("[bold red]Only x265/HEVC encodes are allowed")
            return True, True  # banned, and skip

        json_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / "MediaInfo.json"
        try:
            async with aiofiles.open(json_path, encoding="utf-8") as f:
                mi = json.loads(await f.read())
        except FileNotFoundError:
            console.print("[bold red]MediaInfo.json not found—cannot check bitrate. Assuming not banned.")
            return False, False
        except json.JSONDecodeError:
            console.print("[bold red]Invalid MediaInfo.json—cannot check bitrate. Assuming not banned.")
            return False, False

        for track in mi.get("media", {}).get("track", []):
            if track.get("@type") == "Video":
                bitrate = int(track.get("BitRate", 3000000))
                bitrate_str = track.get("BitRate_String", f"{bitrate // 1000} kbps")
                if bitrate < 3000000:
                    if meta.get("unattended"):
                        console.print(f"[bold yellow]Skipped due to low bitrate ({bitrate_str}).")
                        return False, True  # not banned, but skip
                    else:
                        console.print(
                            f"[bold yellow]This encode is bitstarved ({bitrate_str}), should be above 3000 kb/s."
                        )
                        with pm.pause_for_interaction():
                            if not Confirm.ask("Upload anyway?", default=False):
                                return False, True  # user said no, so skip
                        return False, False  # user allowed upload
                break

        return False, False

    async def get_res_id(self, meta: dict[str, Any]) -> str:
        resolution = meta["resolution"]
        resolution_id = {
            "8640p": "10",
            "4320p": "1",
            "2160p": "2",
            "1080p": "3",
            "1080i": "4",
            "720p": "5",
            "576p": "6",
            "576i": "7",
            "540p": "11",
            "540i": "11",
            "480p": "8",
            "480i": "9",
        }.get(resolution, "10")
        return resolution_id

    async def get_type_id(self, meta: dict[str, Any]) -> str:
        media_type = meta["type"]
        if media_type == "REMUX":
            return "2"
        elif media_type in ("WEBDL", "WEBRIP"):
            vcode = meta.get("video_encode", "").upper()
            return "15" if "x265" in vcode else "3"
        elif media_type in ("ENCODE", "HDTV"):
            return "15"
        elif media_type == "DISC":
            return "1"
        else:
            return "0"

    def _parse_huno_response(self, response_json: dict) -> tuple[bool, str, str]:
        data_obj = response_json.get("data") if isinstance(response_json.get("data"), dict) else {}
        torrent_obj = data_obj.get("torrent", {})
        attributes = torrent_obj.get("attributes", torrent_obj)
        raw_url = attributes.get("download_link", "")
        details_url = attributes.get("details_link", raw_url)
        success = response_json.get("success", False)
        return success, raw_url, details_url

    async def get_auto_overrides(self, meta: dict[str, Any]) -> dict[str, Any]:
        overrides = self._get_overrides(meta)
        result: dict[str, Any] = {}

        if overrides.get("hdr") and overrides["hdr"] != "SDR":
            result["video_format"] = overrides["hdr"]

        if meta.get("audio"):
            result["audio_format"] = meta["audio"]

        if meta.get("audio_channels"):
            result["audio_channels"] = meta["audio_channels"]

        if overrides.get("source"):
            result["source_type"] = overrides["source"]

        if meta.get("service"):
            result["streaming_service"] = meta["service"]

        if overrides.get("tag"):
            result["release_group"] = overrides["tag"]

        if overrides.get("lang") and overrides["lang"] not in {"", "None"}:
            result["media_language"] = overrides["lang"]

        if overrides.get("year") and str(overrides["year"]) not in {"", "0"}:
            result["release_year"] = overrides["year"]

        if meta.get("repack"):
            result["release_tag"] = meta["repack"]
        if meta.get("distributor") and meta.get("is_disc"):
            result["distributor"] = meta["distributor"]
        if meta.get("edition"):
            result["edition"] = meta["edition"]
        if meta.get("region"):
            result["region"] = meta["region"]

        return result

    async def edit_name(self, meta: dict[str, Any]) -> str:  # NAMING GUIDE == https://hawke.uno/pages/4
        from src.name import NameGenerator

        template = {
            "MOVIE": "{title} ({year}) {distributor} {edition} {hc} ({resolution} {scale} {service} {3D} {uhd} {source} {profile} {hfr} {hdr} {audio} {lang} - {tag}) {repack}",
            "TV": "{title} ({year}) {season}{episode} {hc} ({resolution} {scale} {service} {3D} {uhd} {source} {profile} {hfr} {hdr} {audio} {lang} - {tag}) {repack}",
        }

        overrides = self._get_overrides(meta)

        name = await NameGenerator().generate_tracker_name(meta, self.tracker, template, overrides)
        meta[f"{self.tracker}_name"] = name
        return name or meta["name"]

    def _get_overrides(self, meta: dict[str, Any]) -> dict[str, Any]:
        dvd_size = meta.get("dvd_size", "")
        hdr = meta.get("hdr")
        hc = meta.get("hardcoded_subs", False)
        is_disc = meta.get("is_disc", "")
        is_silent = meta.get("is_silent", False)
        lang = meta.get("lang", "")
        media_type = meta.get("type", "")
        repack = meta.get("repack")
        tag = meta.get("tag", "")
        vcode = meta.get("video_codec")
        vecode = meta.get("video_encode")

        uuid = meta["uuid"].upper()
        scale = "DS4K" if "DS4K" in uuid else "RM4K" if "RM4K" in uuid else ""

        source = meta.get("source", "")
        source = self._get_source(media_type, source, is_disc, dvd_size)

        if is_disc == "DVD" and not meta.get("region"):
            system = source.replace(" DVD", "").strip()
            if system in ("NTSC", "PAL"):
                meta["region"] = system

        profile = vecode.replace(".", "") if media_type in {"WEBDL", "WEBRIP", "ENCODE"} else vcode

        distributor = ""
        if is_disc and meta.get("distributor"):
            distributor = meta["distributor"]

        return {
            "hdr": hdr or "SDR",
            "hc": "Hardsubbed" if hc else "",
            "lang": "None" if is_silent else (lang.capitalize() if lang else ""),
            "profile": profile,
            "repack": f"[{repack}]" if repack else "",
            "scale": scale,
            "source": source,
            "tag": tag[1:] if tag else "",
            "3D": "3D" if meta.get("3D") else "",
            "distributor": distributor,
            "year": meta.get("tmdb_year") or meta.get("year", ""),
        }

    def _get_source(self, media_type: str, source: str, is_disc: str, dvd_size: str) -> str:
        src = source or ""

        if media_type == "REMUX":
            if is_disc == "BDMV":
                return "BluRay REMUX"
            if is_disc == "DVD":
                dvd_part = dvd_size if dvd_size else "DVD"
                return f"{dvd_part} REMUX"
            return f"{src} REMUX" if src else "REMUX"

        if media_type in {"WEBDL", "WEBRIP"}:
            return "WEB-DL"

        if is_disc:
            if is_disc == "BDMV":
                return "BluRay"
            if is_disc == "DVD":
                return dvd_size.strip() if dvd_size else "DVD"

        return src

    async def upload(self, meta: dict[str, Any]) -> bool | str:
        descgen = DescGenerator(config=self.config, base_dir=meta["base_dir"])
        await descgen.edit_desc(meta, self.tracker)
        cat_id = await self.get_cat_id(meta)
        type_id = await self.get_type_id(meta)

        tmp_dir = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
        bd_path = tmp_dir / "BD_SUMMARY_00.txt"
        mi_path = tmp_dir / "MEDIAINFO.txt"
        desc_path = tmp_dir / f"[{self.tracker}]DESCRIPTION.txt"
        torrent_path = tmp_dir / "BASE.torrent"

        bd_dump = (
            bd_path.read_text(encoding="utf-8")
            if meta.get("bdinfo") is not None and bd_path.exists()
            else None
        )
        mi_dump = mi_path.read_text(encoding="utf-8") if mi_path.exists() else ""
        desc = desc_path.read_text(encoding="utf-8") if desc_path.exists() else ""
        # name = ( # Automatically done by them
        #     meta.get("manual_name")
        #     or (await self.edit_name(meta) if hasattr(self, "edit_name") else None)
        #     or meta["name"]
        # )

        xanon = (
            1
            if (
                self.config["TRACKERS"][self.tracker].get("xanon", False)
                or self.config.get("ADULT", {}).get("always_anon", False)
            )
            and meta.get("xxx")
            else 0
        )
        anon = (
            1
            if (meta.get("anon", 0) or self.config["TRACKERS"][self.tracker].get("anon", False))
            or self.config["DEFAULT"].get("always_anon", False)
            or xanon
            else 0
        )
        modq = 1 if meta.get("modq", False) or self.config["TRACKERS"][self.tracker].get("modq", False) else 0

        is_disc = meta.get("is_disc", "")

        data: dict[str, Any] = {
            "category_id": cat_id,
            "type_id": type_id,
            "tmdb": meta.get("tmdb", 0),
            "anonymous": anon,
            "mod_queue_opt_in": modq,
        }
        data.update(await self.get_auto_overrides(meta))

        if meta["category"] == "TV":
            data["season_number"] = int(meta.get("season_int", 0))
            data["episode_number"] = int(meta.get("episode_int", 0))

        if meta.get("imdb"):
            data["imdb"] = meta["imdb"]
        if meta.get("tvdb"):
            data["tvdb"] = meta["tvdb"]
        if meta.get("mal"):
            data["mal"] = meta["mal"]

        if meta.get("internal"):
            data["internal"] = 1
        if meta.get("freeleech"):
            data["free"] = 1

        files: dict[str, tuple[str, Any, str]] = {}

        # Debug output before any file handles open
        if meta.get("debug", False):
            log.debug("[bright_magenta]DATA 2 SEND[/bright_magenta]:")
            console.print(Pretty(data))
            log.debug("[bright_magenta]FILES 2 SEND[/bright_magenta]:")
            files_info: dict[str, str] = {"torrent": torrent_path.name}
            if is_disc and bd_dump is not None:
                files_info["bdinfo"] = "BD_SUMMARY_00.txt"
            if mi_dump:
                files_info["mediainfo"] = "MEDIAINFO.txt"
            files_info["description"] = desc_path.name
            console.print(Pretty(files_info))
            if not meta.get("nohash", False):
                await edit_torrent(
                    meta,
                    None,
                    self.tracker,
                    "DEBUG",
                    "https://UNIT3D.CHANGES/INFOHASH/DEBUG-ONLY",
                    "Uploadrr DEBUG-MODE",
                )
            else:
                log.debug(
                    "[bright_magenta]SKIPPING torrent edit — nohash mode, no BASE.torrent[/bright_magenta]"
                )
            return "DEBUG"

        with contextlib.ExitStack() as stack:
            torrent_file = stack.enter_context(open(torrent_path, "rb"))
            files["torrent"] = ("torrent.torrent", torrent_file, "application/x-bittorrent")

            if mi_dump:
                files["mediainfo"] = ("MEDIAINFO.txt", mi_dump, "text/plain")

            if is_disc and bd_dump is not None:
                files["bdinfo"] = ("BD_SUMMARY_00.txt", bd_dump, "text/plain")

            files["description"] = (f"[{self.tracker}]DESCRIPTION.txt", desc, "text/plain")

            return await self._execute_upload(meta, data, files, self._parse_huno_response, torrent_path)
