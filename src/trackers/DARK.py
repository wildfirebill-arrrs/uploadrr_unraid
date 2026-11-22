from __future__ import annotations

from typing import Any, ClassVar

import langcodes

from src.mediainfo import MediaInfoHandler
from src.trackers.UNIT3D import Unit3d


class DARK(Unit3d):
    tracker = "DARK"
    source_flag = "DarkPeers"
    domain = "https://darkpeers.org"
    allowed_imghosts: ClassVar[list[str]] = []
    banned_groups: ClassVar[dict[str, Any]] = {
        "ALL_TYPES": [
            "aXXo",
            "BANDOLEROS",
            "BONE",
            "BRrip",
            "CM8",
            "CrEwSaDe",
            "CTFOH",
            "dAV1nci",
            "DNL",
            "FaNGDiNG0",
            "FiSTER",
            "GalaxyTV",
            "HD2DVD",
            "HDTime",
            "iHYTECH",
            "ION10",
            "iPlanet",
            "KiNGDOM",
            "LAMA",
            "MeGusta",
            "mHD",
            "mSD",
            "NaNi",
            "NhaNc3",
            "nHD",
            "nikt0",
            "nSD",
            "OFT",
            "PiTBULL",
            "PRODJi",
            "RARBG",
            "ROCKETRACCOON",
            "Rifftrax",
            "SANTi",
            "SasukeducK",
            "SEEDSTER",
            "ShAaNiG",
            "Sicario",
            "STUTTERSHIT",
            "TAoE",
            "TGALAXY",
            "TGx",
            "TORRENTGALAXY",
            "ToVaR",
            "TSP",
            "TSPxL",
            "ViSION",
            "VXT",
            "WAF",
            "WKS",
            "X0r",
            "YIFY",
            "YTS",
        ],
        "EVO": ["DISC", "REMUX", "WEBRIP", "HDTV", "ENCODE"],
    }

    _NORDIC_CODES = frozenset(["sv", "swe", "da", "dan", "no", "nor", "fi", "fin", "is", "ice", "isl"])

    async def edit_name(self, meta: dict[str, Any]) -> str:
        name = meta["name"]

        if meta.get("is_disc") not in ("BDMV", "DVD", "HDDVD"):
            audio_langs = self._get_audio_languages(meta)
            original_lang = (meta.get("original_language") or "").lower()
            dub_tag = self._compute_dub_tag(original_lang, audio_langs) if audio_langs else ""

            audio_str = meta.get("audio", "")
            current_lang = meta.get("lang", "")

            if dub_tag:
                if current_lang and current_lang in name:
                    name = name.replace(current_lang, "", 1)
                    name = " ".join(name.split())

                if audio_str and audio_str in name:
                    name = name.replace(audio_str, f"{dub_tag} {audio_str}", 1)
                else:
                    name = f"{name} {dub_tag}"
                    name = " ".join(name.split())
            else:
                if current_lang and current_lang not in ("DUBBED", "DUAL", "MULTI", "DUAL-AUDIO"):
                    if current_lang in name:
                        name = name.replace(current_lang, "", 1)
                        name = " ".join(name.split())

        name = name.replace("DDP", "DD+")
        name = " ".join(name.split())
        meta[f"{self.tracker}_name"] = name
        return name

    @staticmethod
    def _get_audio_languages(meta: dict[str, Any]) -> list[tuple[str, str]]:
        handler = MediaInfoHandler._instance
        if handler is None or meta.get("uuid") not in handler._cache:
            return []

        mi = handler._cache[meta["uuid"]]
        tracks = mi.get("media", {}).get("track", [])
        if not isinstance(tracks, list):
            tracks = []

        result: list[tuple[str, str]] = []
        seen: set = set()

        for t in tracks:
            if t.get("@type") != "Audio":
                continue
            title = str(t.get("Title", "")).lower()
            if "comment" in title or "score" in title or "review" in title:
                continue

            lang_code = (t.get("Language_String2") or t.get("Language", "")).lower()
            if lang_code in ("", "zxx"):
                continue

            lang_name = t.get("Language_String", "").strip()
            if not lang_name:
                try:
                    lang_name = langcodes.Language.get(lang_code).display_name()
                except langcodes.LanguageTagError:
                    lang_name = lang_code.title()

            if lang_code not in seen:
                seen.add(lang_code)
                result.append((lang_code, lang_name))

        return result

    def _compute_dub_tag(self, original_lang: str, audio_langs: list[tuple[str, str]]) -> str:
        if not original_lang or not audio_langs:
            return ""

        codes = [c for c, _ in audio_langs]
        code_set = set(codes)
        is_english_original = original_lang == "en"

        def name_for_code(code: str) -> str:
            for c, n in audio_langs:
                if c == code:
                    return n
            try:
                return langcodes.Language.get(code).display_name()
            except langcodes.LanguageTagError:
                return code.title()

        def is_nordic(code: str) -> bool:
            return code in self._NORDIC_CODES

        def is_nordic_only(s: set) -> bool:
            return bool(s) and s.issubset(self._NORDIC_CODES)

        if is_english_original:
            if len(code_set) == 2 and "en" in code_set:
                other = next(c for c in code_set if c != "en")
                return f"{name_for_code(other)} MULTi"
            if len(code_set) >= 2:
                return "MULTi"
            return ""

        has_original = original_lang in code_set
        has_english = "en" in code_set
        non_original_non_english = code_set - {original_lang, "en"}
        non_original_count = len(non_original_non_english)

        if code_set == {"en"}:
            return "Dubbed"

        if is_nordic_only(code_set) and not is_nordic(original_lang):
            return f"{name_for_code(codes[0])} Dubbed"

        if code_set == {original_lang, "en"}:
            return "Dual-Audio"

        if has_original and has_english and non_original_count >= 1:
            return "MULTi"

        if has_original and non_original_count >= 2:
            return "MULTi"

        if len(code_set) == 2:
            candidates = [c for c in code_set if c not in (original_lang, "en") and not is_nordic(c)]
            if candidates:
                return f"{name_for_code(candidates[0])} MULTi"
            candidates = [c for c in code_set if c != original_lang]
            if candidates:
                return f"{name_for_code(candidates[0])} MULTi"
            return "MULTi"

        if len(code_set) >= 2:
            return "MULTi"

        return ""
