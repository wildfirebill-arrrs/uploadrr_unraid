from __future__ import annotations

import asyncio
from pathlib import Path

from rich.prompt import Confirm

from src.clients import Clients
from src.manual import ManualPackager
from src.prep import Prep
from src.progress import ProgressManager
from src.utils.console import console, log
from src.utils.descgen import DescGenerator

from .meta import get_missing
from .ui import build_db_info


async def upload_to_trackers(
    meta: dict,
    trackers: list[str],
    tracker_registry: dict,
    skipped_details: list[tuple],
    path: str,
    pm: ProgressManager,
    client: Clients,
    config: dict,
) -> tuple[int, int]:
    """Upload to trackers using finalized meta."""
    debug = meta.get("debug", False)
    log.debug("inside upload_to_trackers")
    successful_count = 0
    skipped_count = 0
    for tracker in trackers:
        if tracker == "MANUAL":
            tracker_instance = None
            tracker_type = "MANUAL"
        else:
            tracker_instance = tracker_registry[tracker]["class"](config=config)
            tracker_type = tracker_registry[tracker].get("type")

        if tracker == "MANUAL":
            if await handle_manual_upload(meta, trackers, tracker_registry, config):
                successful_count += 1
            else:
                skipped_count += 1
                skipped_details.append((path, "Manual upload skipped"))
        elif tracker_type in ["api", "azn", "gazzelle", "luminance", "http", "nexus", "unit3d"]:
            log.debug("about to upload_to_tracker")
            tracker_success, _skipped = await upload_to_tracker(
                tracker_instance, tracker, meta, config, client, 0, skipped_details, path, debug
            )
            if tracker_success:
                successful_count += 1
            else:
                skipped_count += 1
        else:
            tracker_success = await handle_special_tracker(
                tracker, tracker_instance, meta, path, skipped_details, config
            )
            if tracker_success:
                successful_count += 1
            else:
                skipped_count += 1

    return successful_count, skipped_count


async def handle_manual_upload(meta: dict, trackers: list[str], tracker_registry: dict, config: dict) -> bool:
    """Handle manual upload packaging."""
    if not (meta.get("unattended", False) or Confirm.ask("Get files for manual upload?", default=True)):
        return False
    packager = ManualPackager(
        config, Prep(config=config, base_dir=Path(meta["base_dir"])).img_host, Path(meta["base_dir"])
    )
    descgen = DescGenerator(config=config, base_dir=meta["base_dir"])
    for tracker in trackers:
        if tracker != "MANUAL" and tracker in tracker_registry:
            instance = tracker_registry[tracker]["class"](config=config)
            if tracker_registry[tracker]["type"] in ["api", "unit3d"]:
                await descgen.edit_desc(meta, instance.tracker, instance.signature)
            elif hasattr(instance, "edit_desc"):
                await instance.edit_desc(meta)
    url = await packager.package(meta)
    uuid = f"tmp/{meta['uuid']}"
    console.print(f"[green]{meta['name']} Files at: [yellow]{url or uuid}[/yellow]")
    return bool(url)


async def handle_special_tracker(
    tracker: str,
    tracker_instance,
    meta: dict,
    path: str,
    skipped_details: list[tuple],
    config: dict,
) -> bool:
    """Handle special tracker uploads."""
    pm = ProgressManager.get_instance()
    pm.update(f"Uploading to {tracker}")
    if tracker == "AR":
        return await upload_to_ar(tracker_instance, meta, path, skipped_details, config)
    elif tracker in ["BHD", "DC", "TL"]:
        success, _ = await upload_to_tracker(
            tracker_instance,
            tracker,
            meta,
            config,
            Clients(config=config),
            0,
            skipped_details,
            path,
            meta.get("debug", False),
        )
        return success

    elif tracker in ["THR", "PTP"]:
        console.print(f"Apologies, {tracker} hasn't been implemented yet.")
        skipped_details.append((path, f"{tracker} not implemented"))
        return False

    skipped_details.append((path, f"{tracker} unsupported tracker type"))
    return False


async def upload_to_tracker(
    tracker_instance,
    tracker_name: str,
    meta: dict,
    config: dict,
    client: Clients,
    skipped_files: int,
    skipped_details: list[tuple],
    path: str,
    debug: bool,
) -> tuple[bool, int]:
    """Generic tracker upload method after pre-checks."""
    pm = ProgressManager.get_instance()
    pm.update(f"Uploading to {tracker_name}")
    console.print(f"Uploading to {tracker_name} {'[dim red](DEBUG)' if debug else ''}")

    result = await tracker_instance.upload(meta)
    if result is True:
        if client:
            injected = await client.add_to_client(meta, tracker_name)
            if not injected:
                skipped_files += 1
                skipped_details.append((path, "[red]Uploaded but not injected to client"))
        return True, skipped_files
    else:
        if client and result in ("DEBUG", "DRAFT"):
            injected = await client.add_to_client(meta, tracker_name)
            if not injected:
                skipped_files += 1
                skipped_details.append((path, "[red]Failed to injected to client"))
        skipped_files += 1
        skipped_details.append(
            (path, f"{tracker_name} {result if isinstance(result, str) else 'Rejected Upload'}")
        )
        return False, skipped_files


async def upload_to_ar(ar, meta: dict, path: str, skipped_details: list[tuple], config: dict) -> bool:
    """Specific upload logic for AlphaRatio."""

    from .checks import check_banned_group

    if check_banned_group("AR", ar.banned_groups, meta, skipped_details, path):
        return False
    if not await ar.ensure_authenticated():
        skipped_details.append((path, "AR authentication failed"))
        return False
    # Dupe check already performed in pre_upload_checks; proceed if allowed
    if not meta.get("upload", False):
        skipped_details.append((path, "AR duplicate or upload blocked"))
        return False
    try:
        await ar.upload(meta)
        await asyncio.sleep(5)
        client = Clients(config=config)
        await client.add_to_client(meta, "AR")
    finally:
        if ar.session:
            await ar.close_session()
    return True


async def get_confirmation(
    meta: dict, trackers: list[str] | None, tracker_registry: dict | None, config: dict
) -> bool:
    """Confirm upload details with the user."""
    console.print(f"Prep material saved to Uploadrr/tmp/{meta['uuid']}")

    db_info = build_db_info(meta)
    from rich.panel import Panel

    console.print(Panel("\n".join(db_info), title="Database Info", border_style="bold yellow"))

    tag = f" / {meta['tag'][1:]}" if meta.get("tag", "") else ""
    res = meta["source"] if meta.get("is_disc") == "DVD" else meta["resolution"]
    from rich.text import Text

    console.print(Text(f" {res} / {meta['type']}{tag}", style="bold"))
    if meta.get("personal_release", False):
        console.print("[bright_magenta]Personal Release!")

    display_names = []
    if trackers and tracker_registry:
        for tracker in trackers:
            tracker_class = tracker_registry.get(tracker, {}).get("class")
            if not tracker_class:
                continue
            tracker_obj = tracker_class(config=config)
            if hasattr(tracker_obj, "edit_name"):
                name = meta.get(f"{tracker}_name") or await tracker_obj.edit_name(meta)
                if name:
                    display_names.append(f"[bold green1]{tracker}[/bold green1]: {name}")
                else:
                    display_names.append(f"[bold green1]{tracker}[/bold green1]: {meta['name']}")
            else:
                display_names.append(f"[bold green1]{tracker}[/bold green1]: {meta['name']}")

    if display_names:
        if len(display_names) == 1:
            console.print(f"{display_names[0]}", force=True)
        else:
            console.print("[bold]NAME[/bold]:", force=True)
            for name in display_names:
                console.print(f" • {name}", force=True)
    else:
        console.print(f"[bold]Name[/bold]: {meta['name']}", force=True)

    if not meta.get("unattended", False):
        if not meta.get("is_sports"):
            get_missing(meta)
        bell = "\a" if config.get("DEFAULT", {}).get("sfx_on_prompt", True) else ""
        debug = "([bright_red]DEBUG[/bright_red])" if meta["debug"] else ""
        console.print(f"[bold yellow]Is this correct? {debug}{bell}", force=True)
        from rich.prompt import Confirm

        return Confirm.ask(" Correct?", console=console)
    return True
