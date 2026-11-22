from __future__ import annotations

import re

from guessit import guessit

from src.utils.constants import (
    EDITION_CODES,
    FORMAT_CODES,
    LANG_CODES,
    NOISE_WORDS,
    RGT_NOISE,
    SOURCE_TYPES,
    STOP_TAG_SET,
    TYPE_CODES,
    YEAR_RE,
    YEAR_RE_FULL,
)


def _split_multi_artists(text: str) -> tuple[str, list[str]]:
    """Split multi-artist string and return (primary_artist, all_artists_list).

    Handles 'Artist A, Artist B', 'Artist A & Artist B', 'Artist A + Artist B', 'Artist A x Artist B'.
    """
    if not text:
        return "", []

    # First try delimiters with surrounding spaces (most reliable)
    for delim in [", ", " & ", " + ", " x "]:
        if delim in text:
            parts = [p.strip() for p in text.split(delim) if p.strip()]
            if len(parts) > 1:
                return parts[0], parts

    # Check for comma without space (e.g., "Artist A,Artist B")
    if "," in text:
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if len(parts) > 1:
            return parts[0], parts

    return text.strip(), [text.strip()]


def _empty_result() -> dict[str, str]:
    return {
        "artist": "",
        "album": "",
        "year": "",
        "release_group": "",
        "source": "",
        "type": "",
        "format": "",
        "bit_depth": "",
        "bitrate": "",
        "sampling_rate": "",
        "catalog": "",
        "catalog_number": "",
        "barcode": "",
        "edition": "",
        "language": "",
    }


def _clean_name(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"[\[\]{}]", " ", text)
    text = re.sub(r"'", "", text)
    text = re.sub(r"_", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    words = text.split()
    cleaned = []
    for w in words:
        if not w:
            continue
        if (w.isupper() and len(w) > 1) or w.isdigit() or re.match(r"^\d+(?:st|nd|rd|th)?$", w, re.I):
            cleaned.append(w)
        else:
            cleaned.append(w.capitalize())
    return " ".join(cleaned)


def _is_scene(raw: str) -> bool:
    return " - " not in raw and ("-" in raw or "_" in raw)


def _extract_barcode(text: str, result: dict[str, str]) -> None:
    if result["barcode"]:
        return
    m = re.search(r"\{barcode-([0-9]+)\}", text, re.I)
    if m:
        result["barcode"] = m.group(1)


# fmt: off
def _extract_release_group(text: str, result: dict[str, str]) -> None:
    if result["release_group"]:
        return
    m = re.search(r"YERAYCITO\s+MASTER\s+SERIES(?:\s+[IVX]+)?$", text, re.I)
    if m:
        result["release_group"] = "YERAYCITO"
        return
    m = re.search(r"-\s*\[([A-Za-z0-9_]+)\]\s*$", text)
    if m:
        cand = m.group(1).strip()
        if cand.lower() not in RGT_NOISE and not YEAR_RE_FULL.fullmatch(cand):
            result["release_group"] = cand
            return
    m = re.search(r"[-\[\s_]\[([A-Z][A-Z0-9_-]{1,11})\]\s*$", text)
    if m:
        cand = m.group(1).strip()
        if cand.lower() not in RGT_NOISE and not YEAR_RE_FULL.fullmatch(cand):
            result["release_group"] = cand
            return
    m = re.search(r"[-_]([A-Z][A-Z0-9_-]{1,11})$", text)
    if m:
        cand = m.group(1).strip()
        if cand.lower() not in RGT_NOISE and not YEAR_RE_FULL.fullmatch(cand):
            result["release_group"] = cand
            return
    m = re.search(r"\s+([a-z][a-z0-9_]{1,14})$", text)
    if m:
        cand = m.group(1)
        if cand.lower() not in RGT_NOISE and not YEAR_RE_FULL.fullmatch(cand):
            result["release_group"] = cand


def _extract_source_info(text: str, result: dict[str, str]) -> None:
    if result["source"]:
        return
    m = re.search(r"\[\s*FLAC\s+CD\b", text, re.I)
    if m:
        result["source"] = "CD"
        return
    m = re.search(r"\((?:\d+CD|CD)\b", text, re.I)
    if m:
        result["source"] = "CD"
        return
    m = re.search(r"\bMCD\b", text, re.I)
    if m:
        result["source"] = "CD"
        return
    m = re.search(r"\d+CD\b", text, re.I)
    if m:
        result["source"] = "CD"
        return
    m = re.search(r"\[\s*(FLAC|MP3|WAV|AAC|ALAC|DSF)\s+(CD|WEB|VINYL|STREAM|RADIO)\b", text, re.I)
    if m:
        result["source"] = m.group(2).upper()


def _extract_bitrate_info(text: str, result: dict[str, str]) -> None:
    if not result["bit_depth"]:
        m = re.search(r"(\d+)\s*bit", text, re.I)
        if m:
            result["bit_depth"] = f"{m.group(1)}bit"
    if not result["sampling_rate"]:
        m = re.search(r"(\d+\.?\d*)\s*kHz", text, re.I)
        if m:
            result["sampling_rate"] = f"{m.group(1)}khz"


def _clean_album_text(text: str, result: dict[str, str]) -> str:
    if not text:
        return ""
    # Remove year ranges (e.g., "1961-1971")
    text = re.sub(r"\b\d{4}-\d{4}\b", "", text)
    # Remove orphan year numbers that were split from ranges (e.g., "1961" from "1961-1971")
    text = re.sub(r"\s+\d{4}\s*$", "", text)
    text = re.sub(r"\s*\([^)]*\d{4}[^)]*\)", "", text)
    text = re.sub(r"\s*\([^)]*\)", "", text)
    words = text.split()
    clean_words = [w for w in words if w.lower() not in NOISE_WORDS]
    text = " ".join(clean_words)
    text = re.sub(r"\b\d+CD\b", "", text, flags=re.I)
    text = re.sub(r"\bMCD(?:-[A-Z]+)?\b", "", text, flags=re.I)
    text = re.sub(r"\bMCD\b", "", text, flags=re.I)
    text = re.sub(r"\b(?:YERAYCITO\s+MASTER\s+SERIES(?:\s+[IVX]+)?|YERAYCITO\s+MASTER\s+[IVX]+|YERAYCITO\s+MASTER\s+[A-Z0-9]+)\b", "", text, flags=re.I)
    text = re.sub(r"\bOST\b", "", text, flags=re.I)
    text = re.sub(r"\w+_EDITION\b", "", text, flags=re.I)
    text = re.sub(r"\bOST\s+SPECIAL\b", "", text, flags=re.I)
    text = re.sub(r"\bSPECIAL\s+EDITION\b", "", text, flags=re.I)
    rg = result.get("release_group") or ""
    if rg:
        text = re.sub(re.escape(rg), "", text, flags=re.I).strip()
        text = re.sub(re.escape(rg.upper()), "", text).strip()
    text = re.sub(r"\(\s*[A-Za-z]*\s*\)", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    year = result.get("year") or ""
    if year:
        text = re.sub(rf"\b{re.escape(year)}\b", "", text).strip()
    return text


def _extract_type_edition(part: str, upper: str, result: dict[str, str]) -> tuple[str, bool]:
    for ed_tag in [
        "OST SPECIAL EDITION",
        "OST DELUXE EDITION",
        "SPECIAL EDITION",
        "DELUXE EDITION",
        "OST DELUXE",
        "DELUXE",
        "ANNIVERSARY",
        "BOX SET",
    ]:
        ed_spaced = ed_tag.upper()
        ed_underscored = ed_spaced.replace(" ", "_")
        if ed_underscored in upper:
            if not result["edition"]:
                result["edition"] = ed_tag.replace("OST ", "")
            # Also set type if OST present
            if ed_tag.startswith("OST") and not result["type"]:
                result["type"] = "OST"
            # Return empty string - nothing to add to album_parts
            return "", True
    for type_tag in ["OST", "SINGLE", "EP", "SAT"]:
        type_upper = type_tag.upper()
        if re.search(r"_" + type_upper + r"$", upper):
            if not result["type"]:
                result["type"] = type_upper
            m = re.search(r"_" + type_upper + r"$", upper)
            return part[: m.start()], True
    return part, False


def _parse_scene(raw: str, result: dict[str, str]) -> None:
    paren_contents = re.findall(r"\(([^)]+)\)", raw)
    for content in paren_contents:
        clean = re.sub(r"[^A-Z0-9]", "", content).upper()
        words = content.split("_")
        if (
            clean
            and len(content) > 10
            and any(w.lower() in {"remix", "deluxe", "extended", "edit"} for w in words)
        ):
            edition_text = re.sub(r"_", " ", content).strip()
            if edition_text:
                result["edition"] = edition_text
        elif clean and len(clean) >= 3 and clean not in FORMAT_CODES:
            if not YEAR_RE_FULL.fullmatch(clean):
                result["catalog"] = clean
                break

    text = re.sub(r"\([^)]+\)", "", raw)
    text = re.sub(r"\{[^}]+}", "", text)

    parts = [p.strip() for p in text.split("-") if p.strip()]
    if not parts:
        return
    result["artist"] = _clean_name(parts[0])

    album_parts = []
    for i in range(1, len(parts)):
        part = parts[i]
        upper = part.upper()
        lower = part.lower()

        m = re.search(r"(19|20)\d{2}", part)
        if m and not result["year"]:
            result["year"] = m.group(0)
            continue

        if re.match(r"^\d{2}$", part):
            continue

        is_technical = False

        if upper in SOURCE_TYPES:
            if not result["source"]:
                result["source"] = upper
            is_technical = True
        elif upper == "MCD":
            if not result["source"]:
                result["source"] = "CD"
            is_technical = True
        elif upper in FORMAT_CODES:
            if not result["format"]:
                result["format"] = upper
            is_technical = True
        elif upper in TYPE_CODES:
            if not result["type"]:
                result["type"] = upper
            is_technical = True
        elif upper in EDITION_CODES:
            if not result["edition"]:
                result["edition"] = upper
            is_technical = True
        elif "_" in upper and upper not in SOURCE_TYPES and upper not in FORMAT_CODES:
            part, handled = _extract_type_edition(part, upper, result)
            if handled:
                album_parts.append(part)
                is_technical = True

        if is_technical:
            continue

        if re.match(r"^\d+\s*BIT$", part, re.I) and not result["bit_depth"]:
            result["bit_depth"] = re.sub(r"\s+", "", part).upper()
            continue

        if len(part) == 2 and part.upper() in LANG_CODES and not result["language"]:
            result["language"] = part.upper()
            continue

        if len(part) <= 12 and re.match(r"^[A-Z][A-Z0-9_-]*$", part, re.I):
            if i == len(parts) - 1 and not result["release_group"]:
                result["release_group"] = part
                continue

        if lower in STOP_TAG_SET:
            break

        album_parts.append(part)

    if album_parts:
        result["album"] = _clean_name(" ".join(album_parts))
    result["album"] = _clean_album_text(result["album"], result)


def _parse_non_scene(raw: str, result: dict[str, str]) -> None:
    text = raw

    m = re.search(r"^\(([A-Z]+)\)", text)
    if m:
        fmt = m.group(1).upper()
        if fmt in FORMAT_CODES:
            result["format"] = fmt
            text = text[m.end() :]

    m = re.search(r"\[\s*(FLAC|MP3|WAV|AAC|ALAC|DSF)\s+(CD|WEB|VINYL|STREAM|RADIO)\b", text, re.I)
    if m:
        if not result["source"]:
            result["source"] = m.group(2).upper()
        if not result["format"]:
            result["format"] = m.group(1).upper()

    paren = re.compile(r"\(([^)]+)\)", re.I)
    for m in paren.finditer(raw):
        content = m.group(1)
        clean = re.sub(r"[^A-Z0-9]", "", content).upper()
        lower_content = content.lower()
        if "box set" in lower_content:
            if not result["edition"]:
                result["edition"] = "BOX SET"
        elif any(w in lower_content for w in {"deluxe", "special edition", "anniversary edition"}):
            if not result["edition"]:
                ed_text = re.sub(r"\s*\d+CD\s*", "", content, flags=re.I).strip()
                result["edition"] = ed_text.upper()
        if clean and len(clean) >= 3 and clean not in FORMAT_CODES and not result["catalog"]:
            if clean not in FORMAT_CODES and not re.search(r"\d{4}", content):
                result["catalog"] = clean
        if re.search(r"\d+CD", content, re.I) and not result["source"]:
            result["source"] = "CD"

    for m in re.finditer(r"\{([^}]+)\}", raw):
        content = m.group(1)
        catalog_match = re.search(r"([A-Z]+)[^A-Z0-9]*([0-9]+)", content, re.I)
        if catalog_match and not result["catalog"]:
            result["catalog"] = (catalog_match.group(1) + catalog_match.group(2)).upper()

    _extract_release_group(raw, result)

    text = re.sub(r"\{[^}]+}", "", text)
    text = re.sub(r"\[[^\]]+\]", "", text)
    text = re.sub(r"\b(FLAC|MP3|WAV|AAC|ALAC|DSF)\b", "", text, flags=re.I)

    if " - " in text:
        # Extract year from prefix like "2011. Artist - Album"
        prefix_year_match = re.search(r"^(\d{4})\.\s+", text)
        if prefix_year_match and not result["year"]:
            result["year"] = prefix_year_match.group(1)

        dash_parts = re.split(r"\s*-\s*", text)
        album_start_idx = 1

        for idx, p in enumerate(dash_parts[1:], 1):
            if re.match(r"^\d{4}$", p.strip()):
                if not result["year"]:
                    result["year"] = p.strip()
                album_start_idx = idx + 1
            else:
                break

        parts = [p.strip() for p in dash_parts if p.strip()]
        raw_artist = re.sub(r"^\d{4}\.\s*", "", parts[0])
        result["artist"] = _clean_name(
            re.sub(r"\s*\([^)]*\d{4}[^)]*\)$", "", re.sub(r"\s*\([^)]+\)$", "", raw_artist))
        )

        album_parts = []
        for p in parts[album_start_idx:]:
            if YEAR_RE.fullmatch(p):
                continue
            album_parts.append(p)
        album_text = " ".join(album_parts)
        album_text = re.sub(r"\s*\(\d{4}\)\s*$", "", album_text)

        m = re.search(r"_([0-9]+(?:st|nd|rd|th)\s+Anniversary\s+Edition)", album_text, re.I)
        if m:
            if not result["edition"]:
                result["edition"] = m.group(1)
            album_text = re.sub(r"_" + re.escape(m.group(1)), "", album_text, flags=re.I)
        else:
            album_text, _ = _extract_type_edition(album_text, album_text.upper(), result)

        result["album"] = _clean_name(album_text) if album_text.strip() else ""
        result["album"] = _clean_album_text(result["album"], result)
    elif text:
        result["artist"] = _clean_name(text)


def parse_music_folder(raw: str) -> dict[str, str]:
    if not raw or not raw.strip():
        return _empty_result()

    result = _empty_result()
    text = raw.strip()

    _extract_barcode(text, result)

    if _is_scene(raw):
        _parse_scene(raw, result)
    else:
        _parse_non_scene(raw, result)

    _extract_bitrate_info(text, result)
    _extract_source_info(text, result)

    guess = guessit(text)
    if guess.get("year") and not result["year"]:
        y = guess["year"]
        result["year"] = str(y) if isinstance(y, int) else y

    if guess.get("source") and not result["source"]:
        result["source"] = str(guess["source"]).upper()

    if guess.get("audio_codec") and not result["format"]:
        fmt = guess["audio_codec"]
        result["format"] = fmt.upper() if isinstance(fmt, str) else str(fmt).upper()

    if guess.get("edition") and not result["edition"]:
        ed = guess["edition"]
        result["edition"] = ed.upper() if isinstance(ed, str) else str(ed).upper()

    if guess.get("language") and not result["language"]:
        lang = guess["language"]
        result["language"] = lang.upper() if isinstance(lang, str) else str(lang).upper()

    rg = guess.get("release_group")
    if rg and not result["release_group"]:
        rg_str = str(rg)
        if (
            not YEAR_RE_FULL.fullmatch(rg_str)
            and rg_str.lower() not in RGT_NOISE
            and len(rg_str) <= 20
            and not re.search(r"[][{}}]", rg_str)
        ):
            result["release_group"] = rg_str

    return result
