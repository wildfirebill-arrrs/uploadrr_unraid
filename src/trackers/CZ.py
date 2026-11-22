from datetime import datetime, timezone
from typing import Any, ClassVar

from src.trackers.AZNETWORK import AZNetwork
from src.utils.console import console
from src.utils.country_groups import get_group


class CZ(AZNetwork):
    tracker = "CZ"
    source_flag = "CinemaZ"
    domain = "https://cinemaz.to"
    banned_groups: ClassVar[list[str]] = []
    personal_release_tag = 1594
    internal_tag = 938

    _EUROPE = get_group("EUROPE") - {"GB", "IE"}
    _SOUTH_AND_CENTRAL_AMERICA = get_group("SOUTH_AMERICA") | get_group("CENTRAL_AMERICA")
    _AFRICA = get_group("AFRICA")
    _MIDDLE_EAST = get_group("MIDDLE_EAST")
    _CZ_ALLOWED: ClassVar[frozenset[str]] = _EUROPE | _SOUTH_AND_CENTRAL_AMERICA | _AFRICA | _MIDDLE_EAST

    def language_map(self) -> dict[str, str]:
        base = super().language_map()
        base.update(
            {"Portuguese": "187", "Mor\u00e9": "188", "Filipino": "189", "Bissa": "190", "Rom": "191"}
        )
        return base

    _MIN_HORIZONTAL_RES: ClassVar[int] = 600
    _MIN_AUDIO_BITRATE: ClassVar[int] = 128_000
    _BITRATE_RULES: ClassVar[dict[tuple[str, int], int]] = {
        ("X264", 480): 1_000_000,
        ("X264", 720): 1_500_000,
        ("X264", 1080): 3_000_000,
        ("X264", 2160): 12_000_000,
        ("X265", 720): 1_000_000,
        ("X265", 1080): 2_000_000,
        ("X265", 2160): 8_000_000,
    }

    async def edit_name(self, meta: dict[str, Any]) -> str:
        name = meta["name"]
        if meta.get("is_disc") == "DVD" or meta.get("type") == "DVD":
            name = name.replace("DVD", meta.get("resolution", "DVD"))
        meta[f"{self.tracker}_name"] = name
        return name

    @staticmethod
    def _resolution_value(meta: dict[str, Any]) -> int:
        text = str(meta.get("resolution", "")).lower().replace("p", "").replace("i", "")
        return int(text) if text.isdigit() else 0

    @staticmethod
    def _is_older_than_50(y: Any) -> bool:
        if y is None or not str(y).isdigit():
            return False
        return datetime.now(timezone.utc).year - int(y) >= 50

    async def banned_content(self, meta: dict[str, Any]) -> tuple[bool, bool]:
        type_ = str(meta.get("type", "")).strip().upper()
        video_encode = str(meta.get("video_encode", "")).strip().upper()
        resolution = self._resolution_value(meta)
        violations: list[str] = []

        if meta["category"] not in ("MOVIE", "TV"):
            violations.append("CinemaZ only allows Movies and TV Shows.")

        if meta.get("anime", False):
            violations.append("Upload Anime to AnimeZ instead.")

        origin_codes = meta.get("origin_country_code", [])
        if origin_codes:
            codes = {c.upper() for c in origin_codes if isinstance(c, str)}

            if codes & self._PHD_ALLOWED:
                if not self._is_older_than_50(meta.get("year")):
                    violations.append(
                        "CinemaZ does not allow recent mainstream English content. "
                        "Upload this to PrivateHD instead."
                    )
            elif codes & self._AZ_ALLOWED:
                violations.append("CZ does not allow Asian content. Upload this to AvistaZ.to instead.")
            elif not (codes & self._CZ_ALLOWED):
                violations.append(
                    "CZ accepts content from Europe (excluding GB/IE), "
                    "Africa, the Middle East, Russia, and the Americas "
                    "(excluding recent mainstream English content)."
                )

        if type_ == "ENCODE" and video_encode in ("X264", "H.264", "H264", "X265", "H.265", "H265"):
            key = ("X264" if video_encode in ("X264", "H.264", "H264") else "X265", resolution)

            if resolution < 720 and video_encode in ("X265", "H.265", "H265"):
                violations.append("x265/H.265 is not allowed for SD content on CinemaZ.")

            min_br = self._BITRATE_RULES.get(key)
            if min_br:
                mi = self._azn_load_mediainfo(meta)
                tracks = mi.get("media", {}).get("track", []) if mi else []
                bitrate = 0
                for t in tracks:
                    if t.get("@type") == "Video":
                        br = t.get("BitRate")
                        if br and str(br).isdigit():
                            bitrate = int(br)
                        break
                if bitrate < min_br:
                    violations.append(
                        f"Bitrate too low for {resolution}p {video_encode} on CinemaZ "
                        f"(min {min_br / 1000:.0f} Kbps)."
                    )

        if resolution >= 720 and video_encode in ("DIVX", "XVID"):
            violations.append("DivX/Xvid is not allowed for HD video and up on CinemaZ.")

        is_bd_disc = meta.get("is_disc") == "BDMV"
        if not is_bd_disc:
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
                    f"CinemaZ requires a minimum horizontal resolution of {self._MIN_HORIZONTAL_RES} pixels."
                )

            audio_tracks = [t for t in tracks if t.get("@type") == "Audio"]
            if audio_tracks:
                low_audio: list[str] = []
                for t in audio_tracks:
                    br = t.get("BitRate")
                    if br and str(br).isdigit():
                        if int(br) < self._MIN_AUDIO_BITRATE:
                            fmt = str(t.get("Format", "Audio"))
                            low_audio.append(fmt)
                if low_audio:
                    violations.append(
                        f"CinemaZ requires a minimum audio bitrate of {self._MIN_AUDIO_BITRATE // 1000} kbit. "
                        f"Low bitrate track(s): {', '.join(sorted(set(low_audio)))}."
                    )

        if violations:
            console.print("\n".join(f"[bold red]{v}[/bold red]" for v in violations))
            return True, True

        return False, False
