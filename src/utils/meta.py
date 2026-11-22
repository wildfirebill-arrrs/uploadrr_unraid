from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

from rich.prompt import Prompt
from rich.text import Text

from src.args import apply_special_logic, build_parser, enforce_global_only, normalize_args
from src.name import NameGenerator
from src.utils.console import console, log


async def save_meta(meta: dict, base_dir: Path, uuid: str) -> None:
    meta_dir = base_dir / "tmp" / uuid
    meta_dir.mkdir(parents=True, exist_ok=True)
    meta_path = meta_dir / "meta.json"
    to_save = dict(meta)
    image_list = to_save.pop("image_list", ...)
    rehost_list = to_save.pop("rehost_list", ...)
    try:
        with meta_path.open("w", encoding="utf-8") as f:
            json.dump(to_save, f, ensure_ascii=False, indent=4)
        log.debug(f"Saved meta.json to {meta_path}")
    except Exception as e:
        log.error(f"Failed to save meta.json: {e}")
    if image_list is not ...:
        if rehost_list is not ...:
            image_list["rehost_list"] = rehost_list
        screens_path = meta_dir / "screens.json"
        try:
            with screens_path.open("w", encoding="utf-8") as f:
                json.dump(image_list, f, ensure_ascii=False, indent=4)
            log.debug(f"Saved screens.json to {screens_path}")
        except Exception as e:
            log.error(f"Failed to save screens.json: {e}")


async def load_existing_meta(base_dir: Path, path: str, new_meta: dict, clean_meta: bool = False) -> dict:
    meta_file = base_dir / "tmp" / Path(path).name / "meta.json"
    screens_file = base_dir / "tmp" / Path(path).name / "screens.json"
    if clean_meta and meta_file.exists():
        try:
            meta_file.unlink()
            log.debug(f"Purged existing meta.json at {meta_file}")
        except Exception as e:
            log.warning(f"Failed to purge meta.json at {meta_file}: {e}")
    # fmt: off
    overwrite_keys = [
        "anon", "base_dir", "blu", "category", "client", "debug", "desc",
        "descfile", "desclink", "draft", "dupe", "episode", "ffdebug", "freeleech", "full_dir",
        "hardcoded_subs", "hdb", "imdb", "imghost", "mal", "manual", "manual_edition", "manual_ids", "manual_name",
        "manual_source", "manual_type", "mbid", "no_aka", "no_dub", "no_nfo", "no_seed", "no_season", "no_tag",  "no_year",
        "nohash", "path", "personalrelease", "ptp", "qbit_cat", "qbit_tag", "randomized", "rehash",
        "region", "screens", "season", "service", "skip_dupe_check", "skip_imghost_upload", "stream", "tag", "torrent_creation",
        "trackers", "unattended", "vapoursynth", "webdv",
        # XXX keys
         "jav", "xpack", "user_cover", "user_banner", "user_trailer", "xmovie", "xscene", "xxx"
    ]
    # fmt: on
    my_meta = {
        "base_dir": str(new_meta.get("base_dir", base_dir)),
        "path": new_meta.get("path", path),
        "uuid": new_meta.get("uuid", Path(path).name),
    }

    saved_meta: dict = {}
    try:
        with meta_file.open("r", encoding="utf-8") as f:
            saved_meta = json.load(f)
            for key, value in saved_meta.items():
                if key not in my_meta:
                    my_meta[key] = value
            for key in overwrite_keys:
                if key in new_meta and key not in ["base_dir", "path", "uuid"]:
                    my_meta[key] = new_meta[key]
            for key, value in new_meta.items():
                if key not in my_meta:
                    my_meta[key] = value
    except FileNotFoundError:
        log.debug(f"No existing meta.json at {meta_file}")
        for key, value in new_meta.items():
            if key not in my_meta:
                my_meta[key] = value
    except Exception as e:
        log.error(f"Error loading meta.json: {e}")
        for key, value in new_meta.items():
            if key not in my_meta:
                my_meta[key] = value

    if screens_file.exists():
        try:
            with screens_file.open("r", encoding="utf-8") as f:
                screens_data = json.load(f)
            my_meta["image_list"] = screens_data
            if "rehost_list" in screens_data:
                my_meta["rehost_list"] = screens_data.pop("rehost_list")
            log.debug(f"Loaded screens.json from {screens_file}")
        except Exception as e:
            log.warning(f"Failed to load screens.json at {screens_file}: {e}")
    elif "image_list" in saved_meta:
        image_list = saved_meta["image_list"]
        if isinstance(image_list, list):
            image_list = {"main": image_list, "episodes": {}, "contact_sheets": []}
        my_meta["image_list"] = image_list
        log.info(f"Migrated image_list from meta.json to screens.json for {path}")

    log.ddebug(f"meta keys after load_existing_meta: {list(my_meta.keys())}")
    return my_meta


async def edit_meta(
    prep,
    meta: dict[str, Any],
    trackers: list[str] | None,
    tracker_registry: dict | None,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    """Handle manual metadata correction with CLI-like editing."""
    parser = build_parser(config)  # reuse standard parser

    while True:
        console.print("Input args to correct (e.g., --tag PMEDIA --tmdb 12345) or 'skip'")
        raw_input = Prompt.ask("").strip()

        if raw_input.lower() == "skip":
            return None

        if not raw_input:
            console.print("⚠️ Invalid input. Try again or 'skip'.", style="dim")
            continue

        try:
            edit_argv = raw_input.split()
            ns = parser.parse_args(edit_argv)
            enforce_global_only(ns, is_global=False, parser=parser)

        except (ValueError, SystemExit, Exception) as e:
            console.print(f"[red]❌ Parse error: {e}[/red]")
            console.print("[dim]Hint: Use --flag value (e.g., --tmdb 12345, --tag GROUP)[/dim]")
            continue

        edit_meta_dict = normalize_args(ns)
        # fmt:off
        if meta.get("xxx"):
            xxx_tokens = {"-x", "--xxx", "-xs", "--xscene", "-xm", "--xmovie", "-jav", "--jav", "-xp", "--pack", "--xpack"}
            user_changed_xxx = any(tok in raw_input for tok in xxx_tokens)
            if not user_changed_xxx:
                for flag in ("xxx", "xscene", "xmovie", "jav", "xpack"):
                    if meta.get(flag):
                        edit_meta_dict[flag] = meta[flag]
        # fmt:on
        # Preserve debug flags during edits
        if meta.get("debug") and not any(tok in raw_input for tok in {"-D", "--debug"}):
            edit_meta_dict["debug"] = True
        if meta.get("deep_debug") and not any(tok in raw_input for tok in {"-DD", "--deep-debug"}):
            edit_meta_dict["deep_debug"] = True

        new_meta = {**meta, **edit_meta_dict}
        new_meta = apply_special_logic(new_meta)
        new_meta["edit"] = True

        base_dir = Path(meta["base_dir"])
        new_meta = await load_existing_meta(base_dir, meta["path"], new_meta)

        reprocess_keys = ["manual_ids"]
        if any(key in edit_meta_dict for key in reprocess_keys):
            new_meta = await prep.gather_prep(meta=new_meta)

        # Regenerate name (if non-sports)
        if new_meta.get("category", "").upper() != "SPORTS":
            name_gen = NameGenerator()
            (
                new_meta["name_notag"],
                new_meta["name"],
                new_meta["clean_name"],
                new_meta["potential_missing"],
            ) = await name_gen.get_name(new_meta)

        # Save
        await save_meta(new_meta, base_dir, new_meta["uuid"])

        # recomputes names against the freshly edited meta.
        keys_to_clear = [
            k for k in new_meta if k.endswith("_name") and k not in ("name", "clean_name", "name_notag")
        ]
        for k in keys_to_clear:
            del new_meta[k]

        # Confirm
        from src.utils.upload import get_confirmation

        if await get_confirmation(new_meta, trackers, tracker_registry, config):
            return new_meta

        meta = new_meta


def get_missing(meta: dict) -> None:
    """Display potentially missing metadata."""
    info_notes = {
        "edition": "Special Edition/Release",
        "description": "Remux/Encode Notes",
        "service": "WEB Service (e.g., AMZN)",
        "region": "Disc Region",
        "imdb": "IMDb ID (tt1234567)",
        "distributor": "Disc Distributor (e.g., Criterion)",
    }
    missing = [
        f"--{k} | {info_notes.get(k)}"
        for k in meta.get("potential_missing", [])
        if not str(meta.get(k, "")).strip()
    ]
    if missing:
        console.print("Potentially missing information", style="bold yellow")
        for item in missing:
            console.print(Text(item, style="bold red" if "imdb" in item else "yellow"))


def get_video_codec(bdinfo: dict) -> str:
    codecs = {
        "MPEG-2 Video": "MPEG-2",
        "MPEG-4 AVC Video": "AVC",
        "MPEG-H HEVC Video": "HEVC",
        "VC-1 Video": "VC-1",
    }
    codec = codecs.get(bdinfo["video"][0]["codec"], "")
    return codec


def find_largest_clpi(bdmv_path: str):
    """Find the largest .clpi file in BDMV/CLIPINF or BDMV/BACKUP/CLIPINF."""
    BDMV = Path(bdmv_path)
    clpi_paths = [BDMV / "CLIPINF", BDMV / "BACKUP" / "CLIPINF"]

    largest_clpi = None
    max_size = 0

    for clpi_dir in clpi_paths:
        if clpi_dir.exists():
            for clpi_file in clpi_dir.glob("*.clpi"):
                file_size = clpi_file.stat().st_size
                if file_size > max_size:
                    max_size = file_size
                    largest_clpi = clpi_file

    return largest_clpi


def get_dvd_size(discs):
    sizes = []
    dvd_sizes = []
    for each in discs:
        sizes.append(each["size"])
    grouped_sizes = [list(i) for j, i in itertools.groupby(sorted(sizes))]
    for each in grouped_sizes:
        if len(each) > 1:
            dvd_sizes.append(f"{len(each)}x{each[0]}")
        else:
            dvd_sizes.append(each[0])
    dvd_sizes.sort()
    compact = " ".join(dvd_sizes)
    return compact
