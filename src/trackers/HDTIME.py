from __future__ import annotations

from typing import Any, ClassVar

from src.mediainfo import MediaInfoHandler
from src.trackers.NEXUSPHP import NexusPHP


class HDTIME(NexusPHP):
    tracker = "HDTIME"
    source_flag = "[hdtime.org] HDTIME"
    domain = "http://hdtime.org"
    allowed_imghosts: ClassVar[list[str]] = []
    banned_groups: ClassVar[dict[str, Any]] = []

    MEDIUM_MAP: ClassVar[dict[str, str]] = {
        "Blu-ray": "1",
        "UHD Disc": "1",
        "BDMV": "1",
        "HD DVD": "2",
        "REMUX": "3",
        "MiniBD": "4",
        "HDTV": "5",
        "DVDR": "6",
        "DVD": "6",
        "ENCODE": "7",
        "CD": "8",
        "Track": "9",
        "WEBDL": "10",
    }

    CODEC_MAP: ClassVar[dict[str, str]] = {
        "H.264": "1",
        "x264": "1",
        "AVC": "1",
        "H.265": "12",
        "x265": "12",
        "HEVC": "12",
        "x264-10bit": "11",
        "AV1": "15",
        "VP8": "14",
        "VP9": "14",
        "AVS3": "17",
        "xvid": "3",
        "MPEG-2": "4",
        "VC-1": "2",
        "VVC": "16",
        "H.266": "16",
        "x266": "16",
    }

    AUDIOCODEC_MAP: ClassVar[dict[str, str]] = {
        "TrueHD Atmos": "8",
        "TrueHD": "9",
        "DTS-HD": "10",
        "DTS": "3",
        "DD": "11",
        "DDP": "12",
        "LPCM": "13",
        "PCM": "13",
        "AAC": "6",
        "Audio Vivid": "14",
        "AV3A": "14",
        "Opus": "15",
        "FLAC": "1",
        "APE": "2",
        "MP3": "4",
        "OGG": "5",
    }

    TEAM_MAP: ClassVar[dict[str, str]] = {
        "CHD": "2",
        "beAst": "3",
        "WiKi": "4",
        "PADTime": "7",
        "CMCT": "8",
        "Other": "5",
        "个人原创": "9",
        "VTime": "15",
        "HDT": "12",
        "HDTime": "6",
        "QHstudIo": "16",
        "AilMWeb": "17",
        "HHWEB": "18",
    }

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
            if meta.get("is_disc") in ("BDMV", "HD DVD"):
                return "424"
            return "401"
        if category == "TV":
            if any(k in keywords for k in ("unscripted", "variety show", "game show", "reality tv")):
                return "403"
            return "402"
        if category == "MUSIC":
            if len(meta.get("video_files", [])) > 0:
                return "406"
            return "408"
        # if category == "PC":
        #     if meta.get("pc_type") == "Games":
        #         return "410"
        #     return "414"
        # if category == "BOOKS":
        #     return "411"
        return "409"

    async def get_type_id(self, meta: dict[str, Any]) -> str:
        media_type = meta.get("type", "")
        is_disc = meta.get("is_disc", "")

        if is_disc in ("BDMV", "HD DVD"):
            if meta.get("uhd"):
                return "1"
            return "2"

        if is_disc == "DVD":
            return "7"

        type_map = {
            "HDTV": "5",
            "WEBDL": "10",
            "WEB-DL": "10",
            "WEBRIP": "10",
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
                return "1"
            return "2"
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

    async def get_region_id(self, meta: dict[str, Any]) -> str:
        return ""

    async def get_team(self, meta: dict[str, Any]) -> str:
        tag = meta.get("tag", "")
        group = tag.strip("[]") if tag else ""

        if group:
            mapped = self.TEAM_MAP.get(group)
            if mapped:
                return mapped

        return "5"

    async def get_tags(self, meta: dict[str, Any]) -> list[str]:
        """HDTIME TAGLIST:
        8 = 杜比视界 / Dolby Vision
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

        if "DV" in str(hdr).upper():
            tags.append("8")
        if "HDR" in str(hdr).upper():
            tags.append("7")
        if meta.get("personal_release", False):
            tags.append("2")
        if "DIY" in uuid and "DIY" not in title:
            tags.append("4")

        # Mandarin audio / Chinese subs
        if meta.get("is_mandarin"):
            tags.append("5")  # 国语 / Mandarin
        if meta.get("zh_subs"):
            tags.append("6")  # 中字 / Chinese subs
        return tags
