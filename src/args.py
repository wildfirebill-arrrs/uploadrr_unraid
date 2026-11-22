from __future__ import annotations

import argparse
import datetime
import re
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

from src.utils.console import console

# =============================================================================
# 🔑 GLOBAL-ONLY ARGUMENTS
# These *cannot* be specified per-item — only once, globally.
# Keep this list minimal and intentional.
# =============================================================================
GLOBAL_ARGS: set[str] = {
    # Core control flow
    "full_dir",
    "debug",
    "deep_debug",
    "unattended",
    "quiet",
    "hide_queue",
    "auto_queue",
    "list_only",
    "batch",
    "delay",
    "full_auto",
    "random",
    "skip_dupe_check",
    "auto_desc",
    "clean_meta",
    # Client/queue control
    "no_seed",
    "client",  # override client — likely global policy
    "qbit_tag",
    "qbit_cat",
    "rtorrent_label",
    # Structural
    "trackers",  # default global; per-item would require complex tracker override logic
    "no_nfo",
    "add_missing_tracker",
}


# =============================================================================
# 🧰 Helper Validators (keep pure & testable)
# =============================================================================
def _validate_batch(value: str) -> tuple[int, int]:
    """Parse batch slice: '10' → (0, 10), '20:10' → (19, 29), '30-50' → (29, 50)."""
    if "-" in value:
        parts = value.split("-", 1)
        if not all(p.isdigit() for p in parts):
            raise argparse.ArgumentTypeError(
                f"Invalid batch format: '{value}'. Expected 'N', 'START:CNT', or 'START-END' with positive integers."
            )
        start, end = int(parts[0]), int(parts[1])
        if start < 1 or end < 1:
            raise argparse.ArgumentTypeError("Batch start and end must be positive integers.")
        if end < start:
            raise argparse.ArgumentTypeError("Batch end must be greater than or equal to start.")
        return (start - 1, end)
    if ":" in value:
        parts = value.split(":", 1)
        if not all(p.isdigit() for p in parts):
            raise argparse.ArgumentTypeError(
                f"Invalid batch format: '{value}'. Expected 'N', 'START:CNT', or 'START-END' with positive integers."
            )
        start, count = int(parts[0]), int(parts[1])
        if start < 1 or count < 1:
            raise argparse.ArgumentTypeError("Batch start and count must be positive integers.")
        return (start - 1, start - 1 + count)
    if not value.isdigit():
        raise argparse.ArgumentTypeError(
            f"Invalid batch format: '{value}'. Expected 'N', 'START:CNT', or 'START-END' with positive integers."
        )
    count = int(value)
    if count < 1:
        raise argparse.ArgumentTypeError("Batch count must be a positive integer.")
    return (0, count)


def _validate_mbid(mbid: str) -> str:
    uuid_pattern = r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$"
    if not re.match(uuid_pattern, mbid):
        raise argparse.ArgumentTypeError("Invalid MBID format. Must be a valid UUID (e.g., 83d9...)")
    return mbid


def _validate_porndb_id(value: str) -> str:
    if value.isdigit():
        return value
    try:
        uuid.UUID(value)
        return value
    except ValueError as err:
        raise argparse.ArgumentTypeError(f"Invalid PornDB ID: '{value}'. Must be digits or UUID.") from err


def _type_choice(value: str) -> str:
    normalized = value.strip().lower().replace("-", "")
    valid = {"disc", "remux", "encode", "webdl", "webrip", "hdtv"}
    if normalized not in valid:
        raise argparse.ArgumentTypeError(
            f"invalid choice: '{value}' (choose from 'disc', 'remux', 'encode', 'webdl', 'web-dl', 'webrip', 'hdtv')"
        )
    return normalized.upper()


def _season_or_episode(value: str) -> dict[str, str]:
    """
    Parse Sxx, Exx, SxxExx → {"manual_season": "S02", "manual_episode": "E05"}
    """
    value = value.strip().lower()
    # Match optional S## and optional E##
    match = re.match(r"s?(\d+)?\s*e?(\d+)?", value)
    if not match:
        return {"manual_season": value}

    season_num, episode_num = match.groups()
    result = {}
    if season_num:
        result["manual_season"] = f"S{int(season_num):02d}"
    if episode_num:
        result["manual_episode"] = f"E{int(episode_num):02d}"
    return result


# =============================================================================
# 🧹 Normalization & Post-Processing
# (Pure functions — no side effects)
# =============================================================================
def normalize_args(ns: argparse.Namespace) -> dict[str, Any]:
    """
    Convert parsed args → clean meta dict.
    Handles type coercion, casing, simple defaults.
    """
    meta = vars(ns).copy()
    # Remove dummy placeholder
    meta.pop("dummy_path", None)

    # --- Simple type coercions & defaults ---
    if meta.get("screens") is not None:
        meta["screens"] = int(meta["screens"])

    if meta.get("category"):
        meta["category"] = meta["category"].upper()

    if meta.get("manual_type"):
        meta["type"] = meta["manual_type"].upper().replace("-", "")

    if meta.get("manual_source"):
        src = meta["manual_source"].lower()
        if src == "bluray":
            meta["manual_source"] = "BluRay"
        elif src == "blu-ray":
            meta["manual_source"] = "Blu-ray"
        else:
            meta["manual_source"] = src.upper()

    if meta.get("resolution"):
        meta["resolution"] = meta["resolution"].lower()

    if meta.get("imghost"):
        meta["imghost"] = meta["imghost"].lower()

    if meta.get("torrent_creation"):
        meta["torrent_creation"] = meta["torrent_creation"].lower()

    if meta.get("tag") and isinstance(meta["tag"], str):
        meta["tag"] = f"-{meta['tag']}"

    # --- ID parsing (non-failing, just extract best effort) ---
    meta.setdefault("manual_ids", {})
    if meta.get("tmdb"):
        val = meta["tmdb"][0] if meta["tmdb"] else ""
        meta["manual_ids"]["tmdb"] = parse_tmdb_id(val, meta.get("category"))

    if meta.get("imdb"):
        raw = meta["imdb"][0] if isinstance(meta["imdb"], list) else meta["imdb"]
        meta["manual_ids"]["imdb"] = raw

    if meta.get("mal"):
        raw = meta["mal"][0] if isinstance(meta["mal"], list) else meta["mal"]
        meta["manual_ids"]["mal"] = raw

    if meta.get("mbid_manual"):
        meta["manual_ids"]["mbid"] = meta["mbid_manual"]

    if meta.get("discogs_id"):
        raw = meta["discogs_id"][0] if isinstance(meta["discogs_id"], list) else meta["discogs_id"]
        meta["manual_ids"]["discogs"] = raw

    if meta.get("deezer_manual"):
        meta["manual_ids"]["deezer"] = meta["deezer_manual"]

    if meta.get("pdb_manual"):
        meta["manual_ids"]["tpdb"] = meta["pdb_manual"]

    if meta.get("ptp"):
        meta["ptp"] = _parse_tracker_id(meta["ptp"][0], "ptp", "torrentid") if meta["ptp"] else ""
    if meta.get("blu"):
        meta["blu"] = _parse_blu_id(meta["blu"][0]) if meta["blu"] else ""
    if meta.get("hdb"):
        meta["hdb"] = _parse_tracker_id(meta["hdb"][0], "hdb", "id") if meta["hdb"] else ""

    if meta.get("trackers"):
        # trackers: str or list[str] → always list
        t = meta["trackers"]
        meta["trackers"] = t if isinstance(t, list) else [t]

    # --- Freeleech: default 100 if flag used without value ---
    if meta.get("freeleech") == []:
        meta["freeleech"] = 100
    elif meta.get("freeleech"):
        meta["freeleech"] = (
            int(meta["freeleech"][0]) if isinstance(meta["freeleech"], list) else int(meta["freeleech"])
        )

    # --- Edition: list → space-joined string ---
    if isinstance(meta.get("manual_edition"), list):
        meta["manual_edition"] = " ".join(meta["manual_edition"]) or ""

    # --- Keywords: list → comma-separated string (as expected by uploaders) ---
    if isinstance(meta.get("keywords"), list):
        meta["keywords"] = ",".join(meta["keywords"])

    # --- Service: list → comma-separated string ---
    if isinstance(meta.get("service"), list):
        meta["service"] = ",".join(meta["service"])

    # --- Distributor: list → space-joined string ---
    if isinstance(meta.get("distributor"), list):
        meta["distributor"] = " ".join(meta["distributor"])

    return meta


def apply_special_logic(meta: dict[str, Any]) -> dict[str, Any]:
    """
    Apply higher-level logic that depends on multiple fields or side effects.
    """
    meta = meta.copy()

    # --- XXX detection ---
    xxx_flags = ("xxx", "xscene", "xmovie", "jav", "xpack")
    if any(meta.get(flag) for flag in xxx_flags):
        meta["xxx"] = True
        for flag in xxx_flags:
            if meta.get(flag):
                meta[flag] = True

    # --- Full-auto mode ---
    if meta.get("full_auto"):
        meta["unattended"] = True
        meta["auto_desc"] = True
        meta["auto_queue"] = meta["full_auto"]  # could be str (dir) or True

    # --- Draft settings (can be overridden by debug) ---
    if meta.get("draft"):
        meta["qbit_cat"] = "DRAFT"
        meta["qbit_tag"] = "DRAFT"
        meta["rtorrent_label"] = "DRAFT"

    # --- Debug mode settings ---
    if meta.get("debug") or meta.get("deep_debug"):
        meta["qbit_cat"] = "DEBUG"
        meta["qbit_tag"] = "DEBUG"
        meta["rtorrent_label"] = "DEBUG"

    # --- Manual IDs: resolve tuples and copy to canonical keys ---
    manual_ids = meta.get("manual_ids", {})
    if isinstance(manual_ids.get("tmdb"), tuple):
        cat, tid = manual_ids["tmdb"]
        meta["category"] = cat or meta.get("category")
        meta["manual_ids"]["tmdb"] = tid

    if manual_ids.get("tmdb"):
        meta["tmdb"] = meta["manual_ids"]["tmdb"]
    if manual_ids.get("imdb"):
        meta["imdb"] = meta["manual_ids"]["imdb"]
        meta["imdb_id"] = str(meta["imdb"]).replace("tt", "")
    if manual_ids.get("mal"):
        meta["mal_id"] = meta["manual_ids"]["mal"]
    if manual_ids.get("mbid"):
        meta["mbid"] = meta["manual_ids"]["mbid"]
    if manual_ids.get("discogs"):
        meta["discogs_id"] = meta["manual_ids"]["discogs"]
    if manual_ids.get("deezer"):
        meta["deezer"] = meta["manual_ids"]["deezer"]
    if manual_ids.get("tpdb"):
        meta["tpdb"] = meta["manual_ids"]["tpdb"]
        meta["xxx"] = True

    # --- Season/Episode parsing from --season/--episode CLI args ---
    for key in ("season", "episode"):
        if meta.get(key):
            extra = _season_or_episode(meta[key])
            # e.g., {"manual_season": "S02", "manual_episode": "E05"}
            meta.update(extra)
            meta.pop(key, None)  # remove raw 'season'/'episode'

    # --- Manual date: ensure it's a date obj (already done by argparse, but confirm) ---
    if meta.get("manual_date") and not isinstance(meta["manual_date"], datetime.date):
        try:
            meta["manual_date"] = datetime.date.fromisoformat(meta["manual_date"])
        except Exception:
            meta.pop("manual_date", None)

    return meta


# =============================================================================
# 🌐 ID Parsing Helpers (URL → ID extraction)
# =============================================================================
def parse_tmdb_id(id_str: str, fallback_category: str | None = None) -> tuple[str, str]:
    """Returns (detected_category, id)"""
    try:
        id_str = id_str.strip().lower()
        if not id_str:
            return fallback_category or "", "0"
        if "/" in id_str:
            parts = id_str.split("/", 2)
            if len(parts) >= 2:
                type_part, id_part = parts[0], parts[1]
                if type_part == "tv":
                    return "TV", id_part
                elif type_part == "movie":
                    return "MOVIE", id_part
        return fallback_category or "", id_str
    except Exception as e:
        console.print(f"[red]⚠️ Failed to parse TMDb ID '{id_str}': {e}")
        return fallback_category or "", "0"


def _parse_tracker_id(url_or_id: str, tracker: str, query_param: str) -> str:
    if url_or_id.startswith("http"):
        try:
            parsed = urllib.parse.urlparse(url_or_id)
            qs = urllib.parse.parse_qs(parsed.query)
            if query_param in qs:
                return qs[query_param][0]
        except Exception as e:
            console.print(f"[red]⚠️ Failed to parse {tracker} ID from URL: {url_or_id} → {e}")
    return url_or_id


def _parse_blu_id(url_or_id: str) -> str:
    if url_or_id.startswith("http"):
        try:
            path = urllib.parse.urlparse(url_or_id).path.rstrip("/")
            return path.split("/")[-1]
        except Exception as e:
            console.print(f"[red]⚠️ Failed to parse BLU ID from URL: {url_or_id} → {e}")
    return url_or_id


# =============================================================================
# 🛠️ Parser Construction
# =============================================================================
def build_parser(config: dict[str, Any]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Uploadrr — Upload media with rich metadata", add_help=True, allow_abbrev=False
    )
    # Dummy to absorb positional args (will be stripped later)
    parser.add_argument("dummy_path", nargs="*", help=argparse.SUPPRESS)

    # ========================
    # 🌍 GLOBAL-ONLY Arguments
    # (Enforced after parsing via `enforce_global_only`)
    # ========================
    parser.add_argument("-fd", "--full-dir", action="store_true", help="Upload folder and all content within")
    parser.add_argument("-D", "--debug", action="store_true", default=False, help="Enable debug logging")
    parser.add_argument(
        "-DD", "--deep-debug", dest="deep_debug", action="store_true", help="Enable verbose debug logging"
    )
    parser.add_argument("-ua", "--unattended", action="store_true", help="Run without interactive prompts")
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        default=config.get("AUTO", {}).get("keep_quiet", False),
        help="Quiet mode: only show summary and errors",
    )
    parser.add_argument(
        "-hq",
        "--hide-queue",
        action="store_true",
        default=config.get("AUTO", {}).get("hide_queue", False),
        help="Suppress queued file listing",
    )
    parser.add_argument("-aq", "--auto-queue", metavar="DIR", help="Automatically queue all items in DIR")
    parser.add_argument(
        "-l", "--list-only", action="store_true", help="List queued files and exit without processing"
    )
    parser.add_argument(
        "--batch",
        type=_validate_batch,
        metavar="N|START:CNT|START-END",
        help="Process only a batch of N items (START:CNT for offset, START-END for inclusive range)",
    )
    parser.add_argument("-delay", "--delay", type=int, help="Delay between uploads (seconds)")
    parser.add_argument(
        "-fa",
        "--full-auto",
        nargs="?",
        const=True,
        default=False,
        help="Full auto: queue, grab desc, unattended (optionally specify dir)",
    )
    parser.add_argument("-random", "--random", action="store_true", help="Randomize upload order")
    parser.add_argument("-sdc", "--skip-dupe-check", action="store_true", help="Skip duplicate checking")
    parser.add_argument("-ns", "--no-seed", action="store_true", help="Do not add torrent to client")
    parser.add_argument("-client", "--client", help="Override default torrent client")
    parser.add_argument("-qbt", "--qbit-tag", dest="qbit_tag", help="qBittorrent tag")
    parser.add_argument("-qbc", "--qbit-cat", dest="qbit_cat", help="qBittorrent category")
    parser.add_argument("-rtl", "--rtorrent-label", dest="rtorrent_label", help="rTorrent label")
    parser.add_argument(
        "-tk",
        "--trackers",
        nargs="*",
        default=config.get("TRACKERS", {}).get("default_trackers", ["MANUAL"]),
        help="Trackers to upload to (e.g., blu bhd)",
    )
    parser.add_argument(
        "-amt",
        "--add-missing-tracker",
        nargs="*",
        metavar="TRACKER",
        help="Add missing tracker(s) from example config to active config. Omit names to add all.",
    )
    parser.add_argument(
        "-aid",
        "--auto-insert-desc",
        dest="auto_desc",
        action="store_true",
        default=config.get("AUTO", {}).get("always_aid", False),
        help="Use existing .txt file for description",
    )
    parser.add_argument(
        "-cm",
        "--clean-meta",
        dest="clean_meta",
        action="store_true",
        help="Purge existing meta.json and rebuild from scratch",
    )

    # ========================
    # 🎯 Per-Item or Global Arguments
    # ========================
    parser.add_argument(
        "-s",
        "--screens",
        type=int,
        default=int(config.get("SCREENS", {}).get("screen_count", 3)),
        help="Number of screenshots",
    )
    parser.add_argument("-ih", "--imghost", type=str, default=None, help="Image hosting service")

    # --- Choices ---
    parser.add_argument(
        "-c", "--category", choices=["movie", "tv", "fanres", "sports", "music"], help="Content category"
    )
    parser.add_argument("-t", "--type", dest="manual_type", type=_type_choice, help="Release type")
    parser.add_argument(
        "--source",
        dest="manual_source",
        choices=["Blu-ray", "BluRay", "DVD", "HDDVD", "WEB", "HDTV", "UHDTV"],
        help="Source media",
    )
    parser.add_argument(
        "-res",
        "--resolution",
        choices=[
            "2160p",
            "1080p",
            "1080i",
            "720p",
            "576p",
            "576i",
            "480p",
            "480i",
            "8640p",
            "4320p",
            "other",
        ],
        help="Video resolution",
    )
    parser.add_argument(
        "-ps", "--piece-size-max", type=int, choices=[1, 2, 4, 8, 16], help="Max piece size (MiB)"
    )
    parser.add_argument(
        "-tc",
        "--torrent-creation",
        choices=["torf", "torrenttools", "mktorrent", "mkbrr"],
        default=config.get("DEFAULT", {}).get("torrent_creation", "torf"),
        help="Torrent creation tool",
    )

    # --- IDs ---
    parser.add_argument("-tmdb", "--tmdb", nargs="*", help="TMDb ID (e.g., 123 or movie/123)")
    parser.add_argument("-imdb", "--imdb", nargs="*", help="IMDb ID")
    parser.add_argument("-mal", "--mal", nargs="*", help="MyAnimeList ID")
    parser.add_argument(
        "-mbid", "--mbid", type=_validate_mbid, dest="mbid_manual", help="MusicBrainz Release ID (UUID)"
    )
    parser.add_argument("-discogs", "--discogs", nargs="*", dest="discogs_id", help="Discogs Release ID")
    parser.add_argument("-deezer", "--deezer", type=int, dest="deezer_manual", help="Deezer Album ID")
    parser.add_argument(
        "-tpdb", "--tpdb", type=_validate_porndb_id, dest="pdb_manual", help="ThePornDB ID or UUID"
    )

    # --- Metadata ---
    parser.add_argument("-g", "--tag", help="Group tag (e.g., RARBG)")
    parser.add_argument("-serv", "--service", nargs="*", help="Streaming service (e.g., Netflix, Hulu)")
    parser.add_argument("-art", "--artist", help="Artist name")
    parser.add_argument("-dist", "--distributor", nargs="*", help="Disc distributor (e.g., Criterion)")
    parser.add_argument(
        "-edition",
        "--edition",
        "--repack",
        nargs="*",
        dest="manual_edition",
        help="Edition string (e.g., Director's Cut, REPACK)",
    )
    parser.add_argument("-season", "--season", help="Season (e.g., 2 or S02)")
    parser.add_argument("-episode", "--episode", help="Episode (e.g., 5 or E05)")
    parser.add_argument(
        "-daily",
        "--daily",
        dest="manual_date",
        type=datetime.date.fromisoformat,
        help="Air date (YYYY-MM-DD)",
    )
    parser.add_argument("-year", "--year", dest="manual_year", type=int, default=0, help="Release year")

    # --- NFO mutex group ---
    nfo_mutex = parser.add_mutually_exclusive_group()
    nfo_mutex.add_argument(
        "-nfo", "--nfo", dest="nfo", action="store_true", default=None, help="Use .nfo file for description"
    )
    nfo_mutex.add_argument(
        "--no-nfo-global",
        dest="no_nfo_global",
        action="store_true",
        default=None,
        help="Suppress NFO file/description upload globally",
    )

    # --- Boolean flags (per-item) ---
    bool_flags = [
        ("-a", "--anon", "anon", "Upload anonymously"),
        ("-dr", "--draft", "draft", "Save as draft instead of publishing"),
        ("-ffdebug", "--ffdebug", "ffdebug", "Show ffmpeg debug during screenshots"),
        ("-hc", "--hardcoded-subs", "hardcoded_subs", "Contains hardcoded subtitles"),
        ("-m", "--manual", "manual", "Manual mode (returns DDL links)"),
        ("-modq", "--mod-queue", "modq", "Opt into Mod Queue"),
        ("-nh", "--nohash", "nohash", "Skip hashing"),
        ("-noaka", "--no-aka", "no_aka", "Remove AKA from title"),
        ("-nodub", "--no-dub", "no_dub", "Remove 'Dubbed' from title"),
        ("-noszn", "--no-season", "no_season", "Remove season from title"),
        ("-nzc", "--no-zh-check", "no_zh_check", "Skip Chinese language/subtitle warning check"),
        ("-g0", "--no-tag", "no_tag", "Remove group tag from title"),
        ("-ny", "--no-year", "no_year", "Remove year from title"),
        ("-pr", "--personal-release", "personalrelease", "Mark as personal release"),
        ("-rh", "--rehash", "rehash", "Force rehash"),
        ("-siu", "--skip-imagehost-upload", "skip_imghost_upload", "Skip image upload"),
        ("-st", "--stream", "stream", "Stream-optimized encoding"),
        ("-vs", "--vapoursynth", "vapoursynth", "Use VapourSynth for screenshots"),
        ("-webdv", "--webdv", "webdv", "Contains Dolby Vision layer"),
        ("-x", "--xxx", "xxx", "XXX content"),
        ("-xs", "--xscene", "xscene", "XXX Scene mode"),
        ("-xm", "--xmovie", "xmovie", "XXX Movie mode"),
        ("-jav", "--jav", "jav", "JAV content"),
        ("-xp", "--pack", "xpack", "XXX Pack (mini/mega)"),
    ]
    for short, long, dest, help_text in bool_flags:
        parser.add_argument(short, long, dest=dest, action="store_true", help=help_text)

    # --- Paths & Media ---
    parser.add_argument("-cv", "--cover", dest="user_cover", help="Cover image (path/URL)")
    parser.add_argument("-b", "--banner", dest="user_banner", help="Banner image (path/URL)")
    parser.add_argument("-trailer", "--trailer", dest="user_trailer", help="Trailer file/url")

    # --- Descriptions ---
    parser.add_argument("-d", "--desc", nargs="*", help='Custom description (e.g., "[b]Hi![/b]")')
    parser.add_argument("-pb", "--desclink", help="Pastebin URL")
    parser.add_argument("-df", "--descfile", help="Local description file")

    # --- Other ---
    parser.add_argument("-ptp", "--ptp", nargs="*", help="PTP torrent ID or URL")
    parser.add_argument("-blu", "--blu", nargs="*", help="BLU torrent ID or URL")
    parser.add_argument("-hdb", "--hdb", nargs="*", help="HDB torrent ID or URL")
    parser.add_argument("-th", "--torrenthash", nargs="*", help="Reuse existing torrent hash")
    parser.add_argument("-k", "--keywords", nargs="*", help="Comma-separated keywords")
    parser.add_argument("-reg", "--region", nargs="*", help="Region (for discs)")
    parser.add_argument(
        "-mt", "--manual-title", dest="manual_name", nargs="*", help="Override generated name"
    )
    parser.add_argument(
        "-rt", "--randomized", nargs="*", default=0, help="Number of extra randomized torrents"
    )
    parser.add_argument(
        "-fl", "--freeleech", nargs="*", help="Freeleech percentage (default 100 if flag used alone)"
    )

    return parser


# =============================================================================
# 🔒 Scoping Enforcement
# =============================================================================
def enforce_global_only(ns: argparse.Namespace, is_global: bool, parser: argparse.ArgumentParser) -> None:
    """
    Raises ValueError if a global-only arg is used in a per-item context.
    Ignores default values from config/parser.
    """
    if is_global:
        return

    for arg in GLOBAL_ARGS:
        if hasattr(ns, arg):
            value = getattr(ns, arg)
            default = parser.get_default(arg)
            if value != default:
                flag = arg.replace("_", "-")
                raise ValueError(
                    f"Argument --{flag} is global-only and cannot be used per-item. Move it to the end of the command."
                )


def group_args_by_path(argv: list[str]) -> tuple[list[tuple[list[str], str]], list[str]]:
    """
    Grouping:
    - Only treat a token as a path if:
        a) it doesn't start with '-', AND
        b) it exists as a file/directory, AND
        c) it's NOT consumed by a preceding flag

    Strategy: collect args until we hit a *non-flag, non-consumed* token that is a path.
    We assume most paths come *first* or after `--`.
    """
    items: list[tuple[list[str], str]] = []
    current_block: list[str] = []
    i = 0
    n = len(argv)

    # Common flags that take path-like args (so their values shouldn't be items)
    PATH_FLAGS = {"--cover", "-cv", "--banner", "-bn", "--trailer", "--descfile", "-df", "--desclink"}

    while i < n:
        arg = argv[i]

        if arg == "--":
            # Explicit separator: everything after is path
            i += 1
            while i < n:
                p = Path(argv[i])
                if p.exists():
                    items.append((current_block.copy(), str(p.resolve())))
                    current_block.clear()
                else:
                    console.print(f"[yellow]⚠️ Non-existent path after '--': {argv[i]}[/yellow]")
                i += 1
            break

        if arg.startswith("-"):
            current_block.append(arg)
            i += 1
            # Look ahead: if next token exists and is not a flag,
            # and this flag is in PATH_FLAGS or likely takes a value, consume it
            if i < n and not argv[i].startswith("-"):
                # Heuristic: if flag is known to take a path, or value looks like a path
                next_val = argv[i]
                next_path = Path(next_val)
                if (
                    arg in PATH_FLAGS
                    or next_path.exists()
                    or next_val.lower().endswith((".png", ".jpg", ".mp4", ".txt", ".webp"))
                ):
                    current_block.append(next_val)
                    i += 1
            continue

        # Non-flag: likely a path
        p = Path(arg)
        if p.exists() or (i == 0 and not items):  # first non-flag is likely main path
            items.append((current_block.copy(), str(p.resolve() if p.exists() else p)))
            current_block.clear()
        else:
            # Unknown — keep in block (e.g., bare words like "skip" in edit)
            current_block.append(arg)
        i += 1

    return items, current_block


# =============================================================================
# 📦 Exported Interface
# =============================================================================
__all__ = [
    "GLOBAL_ARGS",
    "apply_special_logic",
    "build_parser",
    "enforce_global_only",
    "group_args_by_path",
    "normalize_args",
]
