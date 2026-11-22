import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from src.trackers.AZNETWORK import AZNetwork
from src.utils.console import console


class PHD(AZNetwork):
    tracker = "PHD"
    source_flag = "PrivateHD"
    domain = "https://privatehd.to"
    banned_groups: ClassVar[
        dict[str, list[str]]
    ] = {  # Checked 26-05-23 == https://privatehd.to/rules/upload-rules
        "ALL_TYPES": ["RARBG", "FGT", "Grym", "TBS"],
        "EVO": ["ENCODE", "WEBRIP"],
    }
    personal_release_tag = 1448
    internal_tag = 415

    _ALLOWED_CONTAINERS: ClassVar[set[str]] = {".MKV", ".MP4"}
    _ALLOWED_CODECS_REMUX: ClassVar[set[str]] = {"MPEG-2", "VC-1", "H.264", "H.265", "AVC", "HEVC"}
    _ALLOWED_CODECS_BLURAY_ENCODE: ClassVar[set[str]] = {"H.264", "H.265", "X264", "X265"}
    _ALLOWED_CODECS_WEB_UNTOUCHED: ClassVar[set[str]] = {"H.264", "H.265", "VP9"}
    _ALLOWED_CODECS_WEB_ENCODE: ClassVar[set[str]] = {"H.264", "H.265", "X264", "X265"}
    _ALL_ALLOWED_VIDEO_CODECS: ClassVar[set[str]] = {"AVC", "MPEG-2", "VC-1", "H.264", "VP9", "H.265", "X264", "X265", "HEVC"}  # fmt:off
    _ALLOWED_AUDIO: ClassVar[set[str]] = {
        "AC3",
        "DOLBY DIGITAL",
        "DOLBY TRUEHD",
        "DTS",
        "DTS-HD",
        "FLAC",
        "AAC",
    }
    _FORBIDDEN_AUDIO: ClassVar[set[str]] = {"LPCM", "PCM", "LINEAR PCM"}
    _WEB_SOURCES: ClassVar[set[str]] = {"HDTV", "WEB", "HDRIP"}
    _BITRATE_RULES: ClassVar[dict[tuple[str, str, int], int]] = {
        ("X265", "WEB", 720): 1_500_000,
        ("X265", "WEB", 1080): 2_500_000,
        ("X265", "BLURAY", 720): 2_000_000,
        ("X265", "BLURAY", 1080): 3_500_000,
        ("X264", "WEB", 720): 2_500_000,
        ("X264", "WEB", 1080): 4_500_000,
        ("X264", "BLURAY", 720): 3_500_000,
        ("X264", "BLURAY", 1080): 6_000_000,
    }

    def language_map(self) -> dict[str, str]:
        base = super().language_map()
        base.update({"Portuguese": "187", "Filipino": "188", "Mor\u00e9": "189"})
        return base

    async def edit_name(self, meta: dict[str, Any]) -> str:
        name = meta["name"]
        if meta.get("tv_pack", 0) != 0 and meta["category"] == "TV":
            year = str(meta.get("year", ""))
            if year and year in name:
                name = name.replace(f" {year}", "")
        if meta.get("is_disc") == "DVD" or meta.get("type") == "DVD":
            name = name.replace("DVD", meta.get("resolution", "DVD"))
        name = re.sub(r"\bLIMITED\b", "", name, flags=re.IGNORECASE)
        name = re.sub(r"\b\d+th\s+Anniversary\b", "", name, flags=re.IGNORECASE)
        name = name.replace("Director's Cut", "DC").replace("Directors Cut", "DC")
        name = " ".join(name.split())
        meta[f"{self.tracker}_name"] = name
        return name

    @staticmethod
    def _resolution_value(meta: dict[str, Any]) -> int:
        text = str(meta.get("resolution", "")).lower().replace("p", "").replace("i", "")
        return int(text) if text.isdigit() else 0

    async def banned_content(self, meta: dict[str, Any]) -> tuple[bool, bool]:
        type_ = str(meta.get("type", "")).strip().upper()
        source = str(meta.get("source", "")).strip().upper()
        video_encode = str(meta.get("video_encode", "")).strip().upper()
        video_codec = str(meta.get("video_codec", "")).strip().upper()
        resolution = self._resolution_value(meta)
        violations: list[str] = []

        if meta["category"] not in ("MOVIE", "TV"):
            violations.append("PHD only allows Movies and TV Shows.")

        if meta.get("anime", False):
            violations.append("Upload Anime to AnimeTorrents.me instead.")

        year = meta.get("year")
        if year and str(year).isdigit() and datetime.now(timezone.utc).year - int(year) >= 50:
            violations.append("Upload 50+ year old content to CinemaZ.to instead.")

        origin_codes = meta.get("origin_country_code", [])
        if origin_codes:
            codes = {c.upper() for c in origin_codes if isinstance(c, str)}
            if codes & self._AZ_ALLOWED:
                violations.append("Upload Asian content to AvistaZ.to instead.")
            elif not (codes & self._PHD_ALLOWED):
                violations.append(
                    "PHD only allows content from major English-speaking countries. "
                    "European / South American / African content belongs on CinemaZ.to instead."
                )

        if meta.get("sd", 0) == 1:
            violations.append("SD content is forbidden on PHD.")

        if resolution < 720:
            violations.append("Video must be at least 720p on PHD.")

        is_bd_disc = meta.get("is_disc") == "BDMV"
        if not is_bd_disc:
            video = meta.get("video", "")
            if video and Path(video).suffix.upper() not in self._ALLOWED_CONTAINERS:
                ext = Path(video).suffix.upper() or "UNKNOWN"
                violations.append(f"PHD only allows MKV and MP4 containers (got {ext}).")

        tag = str(meta.get("tag", "")).strip().upper().lstrip("-")
        if tag == "EVO" and source != "WEB":
            violations.append("Non-web EVO releases are not allowed on PHD.")

        if type_ == "REMUX" and video_codec not in self._ALLOWED_CODECS_REMUX:
            violations.append("PHD remux allowed codecs: MPEG-2, VC-1, H.264, H.265.")

        if (
            type_ == "ENCODE"
            and source == "BLURAY"
            and video_encode not in self._ALLOWED_CODECS_BLURAY_ENCODE
        ):
            violations.append("PHD BluRay encode allowed codecs: H.264/x264, H.265/x265.")

        if (
            type_ in ("WEBDL", "WEB-DL")
            and source == "WEB"
            and video_encode not in self._ALLOWED_CODECS_WEB_UNTOUCHED
        ):
            violations.append("PHD WEB-DL allowed codecs: H.264, H.265, VP9.")

        if type_ == "ENCODE" and source == "WEB" and video_encode not in self._ALLOWED_CODECS_WEB_ENCODE:
            violations.append("PHD WEB encode allowed codecs: H.264/x264, H.265/x265.")

        if type_ == "ENCODE" and video_encode == "X265" and str(meta.get("bit_depth", "")) != "10":
            violations.append("x265 encodes must be 10-bit on PHD.")

        if resolution > 1080 and video_encode in ("H.264", "X264"):
            violations.append("H.264/x264 only allowed for 1080p and below on PHD.")

        if video_codec and video_codec not in self._ALL_ALLOWED_VIDEO_CODECS:
            violations.append(f"Video codec {video_codec} is not allowed on PHD.")

        if not is_bd_disc:
            mi = self._azn_load_mediainfo(meta)
            tracks = mi.get("media", {}).get("track", []) if mi else []
            audio_tracks = [t for t in tracks if t.get("@type") == "Audio"]

            if audio_tracks:
                original_language = str(meta.get("original_language", "")).lower()

                if original_language:
                    orig_tracks = [
                        t for t in audio_tracks if str(t.get("Language", "")).lower() == original_language
                    ]
                    if orig_tracks:
                        has_truehd_atmos = any(
                            "truehd" in str(t.get("Format_Commercial_IfAny", "")).lower()
                            and "atmos" in str(t.get("Format_Commercial_IfAny", "")).lower()
                            for t in orig_tracks
                        )
                        has_ac3 = any(
                            "ac-3" in str(t.get("Format_Commercial_IfAny", "")).lower()
                            or "dolby digital" in str(t.get("Format_Commercial_IfAny", "")).lower()
                            for t in orig_tracks
                        )
                        if has_truehd_atmos and not has_ac3:
                            violations.append("TrueHD/Atmos requires an AC-3 compatibility track on PHD.")

                invalid: list[str] = []
                for t in audio_tracks:
                    codec = str(t.get("Format_Commercial_IfAny", "") or t.get("Format", "")).upper()
                    if not codec:
                        continue
                    if any(f in codec for f in self._FORBIDDEN_AUDIO) or not any(
                        a in codec for a in self._ALLOWED_AUDIO
                    ):
                        invalid.append(codec)

                if invalid:
                    violations.append(f"Unallowed audio codec(s) on PHD: {', '.join(sorted(set(invalid)))}.")

            if type_ == "ENCODE":
                bitrate = 0
                for t in tracks:
                    if t.get("@type") == "Video":
                        br = t.get("BitRate")
                        if br and str(br).isdigit():
                            bitrate = int(br)
                        break

                source_type = (
                    "WEB" if source in self._WEB_SOURCES else ("BLURAY" if source == "BLURAY" else None)
                )
                if source_type and video_encode in ("X264", "X265"):
                    key = (video_encode, source_type, resolution)
                    min_br = self._BITRATE_RULES.get(key)
                    if min_br and bitrate < min_br:
                        violations.append(
                            f"Bitrate too low for {resolution}p {source_type} "
                            f"{video_encode} on PHD (min {min_br / 1000:.0f} Kbps)."
                        )

        if violations:
            console.print("\n".join(f"[bold red]{v}[/bold red]" for v in violations))
            return True, True

        return False, False
