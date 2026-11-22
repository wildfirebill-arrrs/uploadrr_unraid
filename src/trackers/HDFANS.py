from __future__ import annotations

from typing import Any, ClassVar

from src.mediainfo import MediaInfoHandler
from src.trackers.NEXUSPHP import NexusPHP


class HDFANS(NexusPHP):
    tracker = "HDFANS"
    source_flag = "[hdfans.org] HDFans"
    domain = "https://hdfans.org"
    allowed_imghosts: ClassVar[list[str]] = []
    banned_groups: ClassVar[dict[str, Any]] = []
    tracker_url = domain

    # fmt: off
    MEDIUM_MAP: ClassVar[dict[str, str]] = {
        "BDMV": "1",
        "UHD Disc": "1",
        "Blu-ray Disc": "2",
        "REMUX": "3",
        "HDTV": "4",
        "WEBDL": "5",
        "WEBRIP": "6",
        "ENCODE": "6",
        "FLAC": "6",
        "DVD": "7",
        "HD DVD": "8",
    }

    CODEC_MAP: ClassVar[dict[str, str]] = {
        "H.264": "1",
        "x264": "1",
        "AVC": "1",
        "H.265": "2",
        "x265": "2",
        "HEVC": "2",
        "AV1": "3",
        "VP9": "4",
        "MPEG-2": "5",
        "VC-1": "6",
    }

    AUDIOCODEC_MAP: ClassVar[dict[str, str]] = {
        "AAC": "1",
        "DTS": "2",
        "DTS-HD MA": "3",
        "DTS-HD HRA": "4",
        "DTS:X": "5",
        "TrueHD": "6",
        "TrueHD Atmos": "7",
        "DD": "8",
        "DDP": "9",
        "DDP Atmos": "10",
        "FLAC": "11",
        "LPCM": "12",
        "Opus": "13",
        "Vorbis": "15",
    }

    ZONE_MAP: ClassVar[dict[str, str]] = {
        "CN": "1",  # CN/中国大陆
        "HK": "4",  # HK/香港
        "TW": "5",  # TW/台湾
        "US": "2",  # US/美国
        "GB": "3",  # UK/英国
        "JP": "6",  # JP/日本
        "KR": "7",  # KR/韩国
        "IN": "10",  # IN/印度
        "SG": "11",  # SG/新加坡
        "MY": "12",  # MY/马来西亚
        # Continent fallbacks
        "EUROPE": "8",
        "NORTH_AMERICA": "2",
    }
    ZONE_DEFAULT = "9"  # Other/其他

    TEAM_MAP: ClassVar[dict[str, str]] = {
        "CMCT": "1",
        "WiKi": "2",
        "beAst": "3",
        "MTeam": "4",
        "OurBits": "5",
        "Geek": "6",
        "iLoveHD": "7",
        "HDSky": "8",
        "HDFans": "9",
        "CHD": "10",
        "PTer": "11",
        "TJUPT": "12",
        "TTG": "13",
        "HDChina": "14",
        "HDHome": "15",
        "PuTao": "16",
        "OneHD": "17",
        "HDS": "18",
        "Dream": "19",
        "LHD": "20",
        "NanBei": "21",
        "CCF": "22",
        "MiniHD": "23",
        "U2": "24",
        "HDStreet": "25",
        "NanDu": "26",
        "BTSchool": "28",
        "PHD": "29",
        "SoulVoice": "30",
        "GHT": "31",
        "BTN/NTb": "32",
        "FD": "33",
        "BeiTai": "34",
        "ARiN": "35",
        "FreeFarm": "36",
        "HITV": "37",
        "NoGroup": "38",
        "DBTV": "39",
        "BGT": "40",
        "BGM": "41",
        "ABT": "42",
        "NOGROUP": "27",
    }
    # fmt: on

    async def get_cat_id(self, meta: dict[str, Any]) -> str:
        category = meta.get("category", "")
        is_anime = meta.get("anime", False)
        is_sports = meta.get("is_sports", False)
        genres = {g.strip().lower() for g in (meta.get("genres") or "").split(",") if g.strip()}
        keywords = {k.strip().lower() for k in (meta.get("keywords") or "").split(",") if k.strip()}

        if is_anime or "anime" in keywords:
            return "417"
        if "documentary" in genres:
            return "403"
        if is_sports:
            return "418"
        if category == "MOVIE":
            return "401"
        if category == "TV":
            return "402"
        if category == "MUSIC":
            return "406"
        return "401"

    async def get_type_id(self, meta: dict[str, Any]) -> str:
        media_type = meta.get("type", "")
        is_disc = meta.get("is_disc", "")

        if is_disc in ("BDMV", "HD DVD"):
            if meta.get("uhd"):
                return "1"
            return "2"

        if is_disc == "DVD":
            return "7"

        type_map = {"HDTV": "4", "WEBDL": "5", "WEBRIP": "6", "ENCODE": "6", "REMUX": "3", "FLAC": "6"}
        return type_map.get(media_type, "6")

    async def get_medium(self, meta: dict[str, Any]) -> str:
        media_type = meta.get("type", "")
        is_disc = meta.get("is_disc", "")

        if is_disc in ("BDMV", "HD DVD"):
            if meta.get("uhd"):
                return "1"
            return "2"
        if is_disc == "DVD":
            return "7"

        return self.MEDIUM_MAP.get(media_type, "6")

    async def get_video_id(self, meta: dict[str, Any]) -> str:
        video_codec = meta.get("video_codec", "")
        video_encode = meta.get("video_encode", "")

        for key in (video_codec, video_encode):
            key_stripped = str(key).strip()
            if key_stripped:
                mapped = self.CODEC_MAP.get(key_stripped)
                if mapped:
                    return mapped
                # Try partial match
                for codec_id, val in self.CODEC_MAP.items():
                    if codec_id in key_stripped:
                        return val

        # Fall back to mediainfo
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

        return "1"  # Default to H.264

    async def get_team(self, meta: dict[str, Any]) -> str:
        tag = meta.get("tag", "")
        group = tag.strip("[]") if tag else ""

        if group:
            mapped = self.TEAM_MAP.get(group)
            if mapped:
                return mapped

        return "27"  # Others

    async def get_tags(self, meta: dict[str, Any]) -> list[str]:
        """HDFANS TAGLIST:
        2 = 微光星辰 / Glittering Stars
        14 = 官字组 / Official subtitle group
        16 = 甄选 / Curated
        24 = 驻站 / Stationed
        15 = 首发 / First release (personal_release)
        13 = 原创 / Original
        3 = 禁转 / No redistribution
        8 = 限转 / Limited redistribution
        29 = 源站转发 / Source site forwarding
        4 = DIY
        21 = 原生 / Native
        5 = 国语 / Mandarin audio
        10 = 粤语 / Cantonese audio
        6 = 中字 / Chinese subtitles
        17 = 中英双语 / Chinese + English bilingual
        18 = 特效 / Special effects subs
        7 = HDR
        9 = Dolby Vision
        12 = Atmos
        19 = 4K
        20 = 8K
        22 = CC收藏 / CC Collection
        30 = Hi-Res
        23 = 完结 / Complete series
        28 = 刮削 / Scraped metadata
        27 = AI修复 / AI restored
        26 = 保种 / Seeding commitment
        """
        tags: list[str] = []
        hdr = meta.get("hdr", "")
        uuid = meta.get("uuid", "")
        title = meta.get("title", "")
        resolution = meta.get("resolution", "")

        if resolution == "2160p":
            tags.append("19")
        elif resolution == "4320p":
            tags.append("20")

        if "DV" in str(hdr).upper():
            tags.append("9")
        if "HDR" in str(hdr).upper():
            tags.append("7")
        if meta.get("personal_release", False):
            tags.append("15")
        if "DIY" in uuid and "DIY" not in title:
            tags.append("4")

        # Mandarin / Cantonese / ZH Subs
        if uuid:
            mi = MediaInfoHandler()._cache.get(uuid)
            if mi:
                try:
                    tracks = mi["media"]["track"]
                    for t in tracks:
                        if t.get("@type") == "Audio":
                            commercial = str(t.get("Format_Commercial", ""))
                            additional = str(t.get("Format_AdditionalFeatures", ""))
                            if "Atmos" in commercial or "Atmos" in additional:
                                tags.append("12")
                                break
                except (KeyError, IndexError):
                    pass
            if meta.get("is_mandarin"):
                tags.append("5")  # 国语 / Mandarin
            if meta.get("is_cantonese"):
                tags.append("10")  # 粤语 / Cantonese
            if meta.get("zh_subs"):
                tags.append("6")  # 中字 / Chinese subs

        return tags
