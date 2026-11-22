from __future__ import annotations

from typing import Any, ClassVar

from src.mediainfo import MediaInfoHandler
from src.trackers.NEXUSPHP import NexusPHP


class RGPT(NexusPHP):
    tracker = "RGPT"
    source_flag = "[bilibili.download] RailgunPT"
    domain = "https://bilibili.download"
    tracker_url = domain
    allowed_imghosts: ClassVar[list[str]] = []
    banned_groups: ClassVar[dict[str, Any]] = []
    tracker_url = domain

    # fmt: off
    MEDIUM_MAP: ClassVar[dict[str, str]] = {
        "Blu-ray": "1",
        "BDMV": "1",
        "UHD Disc": "2",
        "REMUX": "3",
        "WEBDL": "4",
        "HDTV": "5",
        "DVD": "6",
        "CD": "8",
        "Track": "9",
        "ENCODE": "7",
    }

    CODEC_MAP: ClassVar[dict[str, str]] = {
        "H.264": "1",
        "x264": "1",
        "AVC": "1",
        "H.265": "2",
        "x265": "2",
        "HEVC": "2",
        "VC-1": "3",
        "MPEG-2": "4",
        "Xvid": "5",
        "xvid": "5",
        "Other": "6",
    }

    AUDIOCODEC_MAP: ClassVar[dict[str, str]] = {
        "TrueHD Atmos": "1",
        "TrueHD": "1",
        "DTS-HD MA": "2",
        "DTS-HD": "2",
        "DD": "3",
        "LPCM": "4",
        "FLAC": "5",
        "MP3": "6",
        "AAC": "7",
        "APE": "8",
        "WAV": "10",
        "Other": "9",
    }
    # fmt: on

    async def get_cat_id(self, meta: dict[str, Any]) -> str:
        category = meta.get("category", "")
        is_anime = meta.get("anime", False)
        is_sports = meta.get("is_sports", False)
        genres = {g.strip().lower() for g in (meta.get("genres") or "").split(",") if g.strip()}
        keywords = {k.strip().lower() for k in (meta.get("keywords") or "").split(",") if k.strip()}

        if is_anime or "anime" in keywords:
            return "405"
        if "documentary" in genres:
            return "404"
        if is_sports:
            return "407"
        if category == "MOVIE":
            return "401"
        if category == "TV":
            if any(k in keywords for k in ("unscripted", "variety show", "game show", "reality tv")):
                return "403"
            return "402"
        if category == "MUSIC":
            if len(meta.get("video_files", [])) > 0:
                return "406"
            return "408"
        return "409"

    async def get_type_id(self, meta: dict[str, Any]) -> str:
        media_type = meta.get("type", "")
        is_disc = meta.get("is_disc", "")

        if is_disc in ("BDMV", "HD DVD"):
            if meta.get("uhd"):
                return "1"
            return "2"

        if is_disc == "DVD":
            return "6"

        type_map = {
            "HDTV": "5",
            "WEBDL": "4",
            "WEB-DL": "4",
            "WEBRIP": "4",
            "ENCODE": "7",
            "REMUX": "3",
            "FLAC": "7",
        }
        return type_map.get(media_type, "7")

    async def get_medium(self, meta: dict[str, Any]) -> str:
        media_type = meta.get("type", "")
        is_disc = meta.get("is_disc", "")

        if is_disc in ("BDMV", "HD DVD"):
            if meta.get("uhd"):
                return "2"
            return "1"
        if is_disc == "DVD":
            return "6"

        return self.MEDIUM_MAP.get(media_type, "7")

    async def get_video_id(self, meta: dict[str, Any]) -> str:
        video_codec = meta.get("video_codec", "")
        video_encode = meta.get("video_encode", "")

        for key in (video_codec, video_encode):
            key_stripped = str(key).strip()
            if key_stripped:
                mapped = self.CODEC_MAP.get(key_stripped)
                if mapped:
                    return mapped
                for codec_id, val in self.CODEC_MAP.items():
                    if codec_id in key_stripped:
                        return val

        uuid = meta.get("uuid", "")
        mi = MediaInfoHandler()._cache.get(uuid) if uuid else None
        if mi:
            try:
                tracks = mi["media"]["track"]
                video = next((t for t in tracks if t.get("@type") == "Video"), {})
                fmt = video.get("Format", "")
                for codec_id, val in self.CODEC_MAP.items():
                    if codec_id in fmt:
                        return val
            except (KeyError, IndexError, StopIteration):
                pass

        return "1"

    async def get_res_id(self, meta: dict[str, Any]) -> str:
        resolution = meta.get("resolution", "")
        resolution_id = {
            "2160p": "1",
            "1080p": "2",
            "1080i": "2",
            "720p": "3",
            "576p": "4",
            "576i": "4",
            "480p": "4",
            "480i": "4",
            "2K": "6",
        }.get(resolution, "5")
        return resolution_id

    async def get_tags(self, meta: dict[str, Any]) -> list[str]:
        """RGPT TAGLIST:
        1 = 禁转 / No redistribution
        2 = 首发 / First release (personal_release)
        3 = 官方 / Official
        4 = DIY
        5 = 国语 / Mandarin audio
        6 = 中字 / Chinese subtitles
        7 = HDR
        """
        tags: list[str] = []
        hdr = meta.get("hdr", "")
        uuid = meta.get("uuid", "")
        title = meta.get("title", "")

        if "HDR" in str(hdr).upper():
            tags.append("7")
        if meta.get("personal_release", False):
            tags.append("2")
        if "DIY" in uuid and "DIY" not in title:
            tags.append("4")

        if meta.get("is_mandarin"):
            tags.append("5")
        if meta.get("zh_subs"):
            tags.append("6")

        return tags
