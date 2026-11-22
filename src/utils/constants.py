from __future__ import annotations

import re

# fmt: off
VIDEO_EXTS: set[str] = {".mkv", ".mp4", ".ts", ".avi", ".wmv"}

MUSIC_EXTS: set[str] = {".aac", ".ac3", ".alac", ".dsf", ".dff",
    ".eac3", ".flac", ".m4a", ".mp3", ".ogg", ".ogm", ".oga",
    ".ogv", ".ogx", ".opus", ".spx", ".wav", ".wsd",
}

IMAGE_EXTS: set[str] = {".jpg", ".jpeg", ".png", ".svg", ".ico", ".gif"}

LOG_EXT: str = ".log"
TXT_EXT: str = ".txt"
NFO_EXT: str = ".nfo"

SOURCE_TYPES: set[str] = {"WEB", "CD", "VINYL", "STREAM", "RADIO"}

TYPE_CODES: set[str] = {"SINGLE", "EP", "SAT", "OST"}

EDITION_CODES: set[str] = {"DELUXE", "SPECIAL EDITION", "ANNIVERSARY", "BOX SET", "REMASTERED", "REISSUE"}

NOISE_WORDS: set[str] = {
    "web", "vinyl", "cd", "mp3", "flac", "wav", "aac", "alac", "dsf", "khz",
    "lossy", "lossless", "mix", "edit", "radio", "extended", "original",
    "instrumental", "acapella", "version", "various", "va", "limited", "reissue",
    "remastered", "promo", "stream", "broadcast", "tv", "guestmix", "serie",
    "series", "incl",
}

INVALID_TAG_WORDS: set[str] = {
    "web", "vinyl", "cd", "mp3", "flac", "wav", "aac", "alac", "khz",
}

RGT_NOISE: set[str] = {
    "flac", "mp3", "vinyl", "web", "cd", "lossy", "lossless", "remastered",
    "single", "ep", "deluxe", "special edition", "anniversary", "box set",
}

LABEL_WORDS: list[str] = [
    "records", "label", "music", "productions", "studio",
    "group", "band", "sound", "entertainment",
]

LANG_CODES: set[str] = {
    "EN", "FR", "DE", "ES", "IT", "JP", "US", "UK", "NL", "SE", "NO", "FI",
    "DK", "AT", "BE", "CH", "CA", "AU", "IE", "GR", "CZ", "HU", "PL", "RO",
    "RU", "TR", "CN", "KR", "PT", "AR", "HI", "JA", "KO", "ZH",
}

FORMAT_CODES: set[str] = {"DSF", "FLAC", "MP3", "WAV", "AAC", "ALAC"}

STOP_TAG_SET: set[str] = {
    "single", "singles", "ep", "eps", "ost", "osts", "deluxe",
    "special edition", "specialedition", "anniversary", "box set",
    "boxset", "compilation", "compilations", "live", "stream"
}

YEAR_RE: re.Pattern = re.compile(r"\b(19|20)\d{2}\b")
YEAR_RE_FULL: re.Pattern = re.compile(r"^(19|20)\d{2}$")

# fmt: on
