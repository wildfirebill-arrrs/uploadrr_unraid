from __future__ import annotations

from typing import Any, ClassVar

from src.mediainfo import MediaInfoHandler
from src.trackers.NEXUSPHP import NexusPHP
from src.utils.console import log


class PTCAFE(NexusPHP):
    tracker = "PTCAFE"
    source_flag = "[ptcafe.club] 咖啡"
    domain = "https://ptcafe.club"
    allowed_imghosts: ClassVar[list[str]] = []
    banned_groups: ClassVar[dict[str, Any]] = []
    tracker_url = domain

    # fmt: off
    MEDIUM_MAP: ClassVar[dict[str, str]] = {
        "UHD Disc": "1",          # UHD Blu-ray 原盘
        "BDMV": "1",
        "Blu-ray Disc": "2",      # UHD Blu-ray DIY
        "Blu-ray": "2",
        "UHD Remux": "3",         # UHD Remux
        "REMUX": "6",             # Remux (non-UHD)
        "ENCODE": "7",            # Encode
        "WEBDL": "8",             # WEB-DL
        "WEBRIP": "8",            # WEBRIP maps to WEB-DL
        "HDTV": "9",              # TV
        "DVD": "10",              # DVD
        "CD": "11",               # CD
        "Track": "12",            # Track
    }

    CODEC_MAP: ClassVar[dict[str, str]] = {
        "H.265": "1",
        "HEVC": "1",
        "H.264": "2",
        "AVC": "2",
        "x265": "3",
        "x264": "4",
        "VC-1": "5",
        "MPEG-2": "6",
        "MPEG-4": "7",
        "xvid": "8",
        "VP9": "9",
        "DIVX": "10",
    }

    AUDIOCODEC_MAP: ClassVar[dict[str, str]] = {
        "DTS-HDMA:X": "1",
        "DTS-HD MA": "2",
        "DTS-HDHR": "3",
        "DTS-HD HRA": "3",
        "DTS-HD": "4",
        "DTS": "11",
        "DTS:X": "5",
        "LPCM": "6",
        "DD": "7",
        "Atmos": "8",
        "AAC": "9",
        "TrueHD": "10",
        "FLAC": "12",
        "APE": "13",
        "MP3": "14",
        "WAV": "15",
        "Opus": "16",
        "OGG": "17",
    }

    ZONE_MAP: ClassVar[dict[str, str]] = {
        "CN": "1",   # 大陆
        "HK": "2",   # 港台
        "TW": "2",
        "JP": "4",   # 日本
        "KR": "5",   # 韩国
        "IN": "6",   # 印度
        # 欧美 — Europe + Americas (North, South, Central, Caribbean)
        "EUROPE": "3",
        "NORTH_AMERICA": "3",
        "SOUTH_AMERICA": "3",
        "CENTRAL_AMERICA": "3",
        "CARIBBEAN": "3",
    }
    ZONE_DEFAULT = "7"  # 其他 / Other

    TEAM_MAP: ClassVar[dict[str, str]] = {
        "ADE": "1",
        "ADWeb": "2",
        "Audies": "3",
        "beAst": "4",
        "BeiTai": "5",
        "BeyondHD": "6",
        "BtsTV": "7",
        "CafeTV": "8",
        "CafeWEB": "9",
        "CHDBits": "10",
        "CHDWEB": "11",
        "CMCT": "12",
        "DJWEB": "13",
        "FRDS": "14",
        "HDCTV": "15",
        "HDH": "16",
        "HDHome": "17",
        "HDSky": "18",
        "HDSWEB": "19",
        "HHWEB": "20",
        "MTeam": "21",
        "MWeb": "22",
        "OurBits": "23",
        "OurTV": "24",
        "PTCafe": "25",
        "PTerWEB": "26",
        "QHstudIo": "27",
        "TTG": "28",
        "WiKi": "29",
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
        uhd = meta.get("uhd", False)

        if is_disc in ("BDMV", "HD DVD"):
            if uhd:
                return "1"
            return "2"

        if is_disc == "DVD":
            return "10"

        type_map = {
            "HDTV": "9",
            "WEBDL": "8",
            "WEB-DL": "8",
            "WEBRIP": "8",
            "ENCODE": "7",
            "REMUX": "6",
            "FLAC": "7",
        }
        return type_map.get(media_type, "7")

    async def get_medium(self, meta: dict[str, Any]) -> str:
        media_type = meta.get("type", "")
        is_disc = meta.get("is_disc", "")

        if is_disc in ("BDMV", "HD DVD"):
            if meta.get("uhd"):
                return "1"
            return "4"
        if is_disc == "DVD":
            return "10"

        return self.MEDIUM_MAP.get(media_type, "13")

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

        return "2"  # Default to H.264/AVC

    async def get_res_id(self, meta: dict[str, Any]) -> str:
        resolution = meta.get("resolution", "")
        resolution_id = {
            "4320p": "1",
            "2160p": "2",
            "1080p": "3",
            "1080i": "3",
            "720p": "4",
            "720i": "4",
            "360p": "5",
            "360i": "5",
        }.get(resolution, "6")
        return resolution_id

    async def get_team(self, meta: dict[str, Any]) -> str:
        tag = meta.get("tag", "")
        group = tag.strip("[]") if tag else ""

        if group:
            mapped = self.TEAM_MAP.get(group)
            if mapped:
                return mapped

        return "30"  # Other

    async def get_tags(self, meta: dict[str, Any]) -> list[str]:
        """PTCAFE TAGLIST:
        1 = 官方 / Official
        2 = 首发 / First release (personal_release)
        3 = 完结 / Complete series
        4 = 原创 / Original
        5 = 禁转 / No redistribution
        7 = 国语 / Mandarin audio
        8 = 粤语 / Cantonese audio
        9 = 中字 / Chinese subtitles
        10 = 备胎 / Backup
        11 = 杜比视界 / Dolby Vision
        12 = HDR
        13 = DIY
        14 = 应求 / On request
        15 = 高码高帧 / High bitrate / high frame rate
        16 = 月月 / Monthly
        """
        tags: list[str] = []
        hdr = meta.get("hdr", "")
        uuid = meta.get("uuid", "")
        title = meta.get("title", "")

        if "DV" in str(hdr).upper():
            tags.append("11")
        if "HDR" in str(hdr).upper():
            tags.append("12")
        if meta.get("personal_release", False):
            tags.append("2")
        if "DIY" in uuid and "DIY" not in title:
            tags.append("13")

        # Mandarin / Cantonese / ZH Subs
        if meta.get("is_mandarin"):
            tags.append("7")  # 国语 / Mandarin
        if meta.get("is_cantonese"):
            tags.append("8")  # 粤语 / Cantonese
        if meta.get("zh_subs"):
            tags.append("9")  # 中字 / Chinese subs
        return tags

    async def banned_content(self, meta: dict[str, Any]) -> tuple[bool, bool]:
        """PTCAFE bans porn, content without Chinese audio/subs, music, and low-quality sources.

        Must have at least one Chinese audio track OR Chinese subtitles.
        Banned sources: CAM, TC, TS
        Banned: MUSIC
        Returns (True, True) for immediate ban, (True, False) for warning, (False, False) for no ban.
        """
        # --- Porn / Adult ---
        adult = meta.get("adult", False)
        if meta.get("xxx") or adult:
            log.warning("PTCAFE: Adult/XXX content is banned.")
            return True, True

        # --- Music banned ---
        if meta.get("category") == "MUSIC":
            log.warning("PTCAFE: Music uploads are banned.")
            return True, True

        # --- Low-quality sources: CAM, TC, TS ---
        source = str(meta.get("source", "")).upper()
        media_type = str(meta.get("type", "")).upper()
        banned_sources = {"CAM", "TC", "TS", "TELESYNC", "TELECINE"}
        if source in banned_sources or media_type in banned_sources:
            log.warning(f"PTCAFE: Source/type '{source or media_type}' (CAM/TC/TS) is banned.")
            return True, True

        # --- Must have Chinese audio OR Chinese subtitles ---
        has_chinese = meta.get("is_mandarin") or meta.get("is_cantonese") or meta.get("zh_subs")

        if not has_chinese:
            log.warning("PTCAFE: Content must have Chinese audio or Chinese subtitles.")
            return True, True

        return False, False
