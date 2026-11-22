from pathlib import Path
from typing import Any, ClassVar

from src.trackers.AZNETWORK import AZNetwork
from src.utils.console import console


class AZ(AZNetwork):
    tracker = "AZ"
    source_flag = "AvistaZ"
    domain = "https://avistaz.to"
    banned_groups: ClassVar[list[str]] = []
    personal_release_tag = 3773
    internal_tag = 943

    _MIN_HORIZONTAL_RES: ClassVar[int] = 600
    _MIN_AUDIO_BITRATE: ClassVar[int] = 128_000
    _ALLOWED_CONTAINERS: ClassVar[set[str]] = {".MKV", ".MP4", ".AVI"}
    _ALLOWED_VIDEO_CODECS: ClassVar[set[str]] = {
        "AVC",
        "H.264",
        "H.265",
        "X264",
        "X265",
        "HEVC",
        "DIVX",
        "XVID",
    }
    _ALLOWED_AUDIO: ClassVar[set[str]] = {
        "AC3",
        "AUDIO LAYER III",
        "MP3",
        "DOLBY DIGITAL",
        "DOLBY TRUEHD",
        "DTS",
        "DTS-HD",
        "FLAC",
        "AAC",
        "DOLBY",
    }

    def language_map(self) -> dict[str, str]:
        base = super().language_map()
        base.update({"Portuguese": "189", "Filipino": "188", "Mor\u00e9": "187"})
        return base

    async def edit_name(self, meta: dict[str, Any]) -> str:
        name = meta["name"]
        if meta.get("tv_pack", 0) != 0 and meta["category"] == "TV":
            year = str(meta.get("year", ""))
            season = meta.get("season", "")
            if year and season and year in name:
                name = name.replace(f"{season} {year}", f"{year} {season}")
        if meta.get("is_disc") == "DVD" or meta.get("type") == "DVD":
            name = name.replace("DVD", meta.get("resolution", "DVD"))
        meta[f"{self.tracker}_name"] = name
        return name

    async def banned_content(self, meta: dict[str, Any]) -> tuple[bool, bool]:
        type_ = str(meta.get("type", "")).strip().upper()
        video_codec = str(meta.get("video_codec", "")).strip().upper()
        violations: list[str] = []

        if meta["category"] not in ("MOVIE", "TV"):
            violations.append("AZ only allows Movies and TV Shows.")

        if meta.get("anime", False):
            violations.append("Upload Anime to AnimeTorrents.me instead.")

        origin_codes = meta.get("origin_country_code", [])
        if origin_codes:
            codes = {c.upper() for c in origin_codes if isinstance(c, str)}
            if codes & self._PHD_ALLOWED:
                violations.append(
                    "AZ does not allow content from major English-speaking countries. "
                    "Upload this to PrivateHD.to instead."
                )
            elif not (codes & self._AZ_ALLOWED):
                violations.append(
                    "AZ only allows Asian content. Upload non-Asian content to CinemaZ.to instead."
                )

        is_disc = bool(meta.get("is_disc"))
        if not is_disc:
            video = meta.get("video", "")
            if video and Path(video).suffix.upper() not in self._ALLOWED_CONTAINERS:
                ext = Path(video).suffix.upper() or "UNKNOWN"
                violations.append(f"AZ only allows MKV, MP4 and AVI containers (got {ext}).")

            if video_codec and video_codec not in self._ALLOWED_VIDEO_CODECS:
                violations.append(
                    f"Video codec {video_codec} is not allowed on AZ. "
                    "Allowed: H.264/AVC/x264, H.265/HEVC/x265, DivX, Xvid."
                )

        if not is_disc:
            mi = self._azn_load_mediainfo(meta)
            tracks = mi.get("media", {}).get("track", []) if mi else []

            video_width = 0
            for t in tracks:
                if t.get("@type") == "Video":
                    width = t.get("Width")
                    if width and str(width).isdigit():
                        video_width = int(width)
                    break
            if video_width and video_width < self._MIN_HORIZONTAL_RES:
                violations.append(
                    f"AZ requires a minimum horizontal resolution of {self._MIN_HORIZONTAL_RES} pixels."
                )

            audio_tracks = [t for t in tracks if t.get("@type") == "Audio"]

            invalid: list[str] = []
            for t in audio_tracks:
                codec = str(t.get("Format_Commercial_IfAny", "") or t.get("Format", "")).upper()
                if not codec:
                    continue
                # untouched source opus is permitted
                if "OPUS" in codec and type_ not in ("ENCODE", "WEBRIP"):
                    continue
                if not any(a in codec for a in self._ALLOWED_AUDIO):
                    invalid.append(codec)

            if invalid:
                violations.append(f"Unallowed audio codec(s) on AZ: {', '.join(sorted(set(invalid)))}.")

            is_webdl = type_ == "WEBDL"
            low_audio: list[str] = []
            for t in audio_tracks:
                br = t.get("BitRate")
                if br and str(br).isdigit():
                    min_br = 0 if is_webdl else self._MIN_AUDIO_BITRATE
                    if int(br) < min_br:
                        fmt = str(t.get("Format", "Audio"))
                        low_audio.append(fmt)
            if low_audio:
                msg = (
                    f"AZ requires a minimum audio bitrate of {self._MIN_AUDIO_BITRATE // 1000} kbit. "
                    f"Low bitrate track(s): {', '.join(sorted(set(low_audio)))}."
                )
                violations.append(msg)

        if violations:
            console.print("\n".join(f"[bold red]{v}[/bold red]" for v in violations))
            return True, True

        return False, False
