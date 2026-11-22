from __future__ import annotations

from rich.prompt import Confirm

from src.progress import ProgressManager
from src.utils.coffee_filter import dupe_check as coffee_dupe_check
from src.utils.console import console, log


async def pre_upload_checks(
    meta: dict,
    trackers: list[str],
    tracker_registry: dict,
    skipped_details: list[tuple],
    path: str,
    config: dict,
) -> dict[str, bool]:
    """Perform pre-upload checks and return tracker-specific upload decisions."""
    tracker_upload_ok = dict.fromkeys(trackers, True)
    progress = ProgressManager.get_instance()
    progress.update("Pre-Upload Checks..")

    if meta.get("skip_dupe_check", False):
        console.print("[yellow]Skipping dupe check..")

    for tracker in trackers:
        if tracker not in tracker_registry and tracker != "MANUAL":
            skipped_details.append((path, f"No such tracker: {tracker}"))
            tracker_upload_ok[tracker] = False
            continue

        if tracker not in config.get("TRACKERS", {}):
            log.error(f"{tracker} not found in config")
            skipped_details.append((path, f"{tracker} not found in config"))
            tracker_upload_ok[tracker] = False
            continue

        tracker_instance = tracker_registry[tracker]["class"](config=config) if tracker != "MANUAL" else None
        if tracker == "MANUAL":
            continue

        if hasattr(tracker_instance, "has_valid_api_key"):
            if not tracker_instance.has_valid_api_key():
                log.error(f"{tracker}: api_key is missing or not configured")
                skipped_details.append((path, f"{tracker} API key not configured"))
                tracker_upload_ok[tracker] = False
                continue

        banned_groups = (
            await tracker_instance.get_banned_groups()
            if hasattr(tracker_instance, "get_banned_groups")
            else tracker_instance.banned_groups
        )

        if banned_groups and check_banned_group(tracker, banned_groups, meta, skipped_details, path):
            skipped_details.append((path, f"Banned group on {tracker}"))
            tracker_upload_ok[tracker] = False
            continue

        if hasattr(tracker_instance, "banned_content"):
            result = await tracker_instance.banned_content(meta)
            if isinstance(result, bool):
                is_banned, is_warn = result, False
            else:
                is_banned, is_warn = result[0], result[1] if len(result) > 1 else False

            if is_banned:
                if is_warn:
                    log.warning(f"{tracker}: Content warning — proceeding at user discretion.")
                    with progress.pause_for_interaction():
                        if not meta.get("unattended", False):
                            if not Confirm.ask(
                                f"[bold yellow]{tracker} warning: content flagged by tracker rules. "
                                "Proceed with upload anyway?[/bold yellow]",
                                default=True,
                            ):
                                skipped_details.append((path, f"Skipped after warning on {tracker}"))
                                tracker_upload_ok[tracker] = False
                                continue
                else:
                    skipped_details.append((path, f"Banned content on {tracker}"))
                    tracker_upload_ok[tracker] = False
                    continue

        if not meta.get("skip_dupe_check", False):
            tracker_type = tracker_registry[tracker].get("type", "other")
            if tracker_type == "luminance":
                if not await tracker_instance.ensure_authenticated():
                    skipped_details.append((path, f"Authentication failed on {tracker}"))
                    tracker_upload_ok[tracker] = False
                    continue
            dupes = await tracker_instance.search_existing(meta)
            skip_rules = bool(meta.get("is_music", False) or meta.get("xxx", False))
            meta, skipped = await coffee_dupe_check(
                dupes,
                meta,
                config,
                skipped_details,
                path,
                tracker,
                skip_rule_filter=skip_rules,
                tracker_simple_name=getattr(tracker_instance, "simple_name", False),
            )
            if skipped:
                skipped_details.append((path, f"Potential duplicate on {tracker}"))
                tracker_upload_ok[tracker] = False
                continue

        if hasattr(tracker_instance, "has_valid_cookies"):
            if not await tracker_instance.has_valid_cookies():
                skipped_details.append((path, f"No Valid Cookies for {tracker}"))
                tracker_upload_ok[tracker] = False
                continue

    return tracker_upload_ok


def check_banned_group(
    tracker: str, banned_group_list: dict, meta: dict, skipped_details: list[tuple], path: str
) -> bool:
    """Check if release group is banned on tracker."""
    progress = ProgressManager.get_instance()
    group_tag = meta["tag"][1:] if meta.get("tag") else ""
    if not group_tag:
        return False

    release_type = meta.get("type", "")

    if isinstance(banned_group_list, list):
        banned_group_list = {group.lower(): ["ALL"] for group in banned_group_list if group}

    all_banned = [g.lower() for g in banned_group_list.get("ALL_TYPES", [])]
    if group_tag.lower() in all_banned:
        console.print(
            f"[bold yellow]{group_tag}[/bold yellow][bold red] was found on [bold yellow]{tracker}[/bold yellow] list of banned groups."
        )
        with progress.pause_for_interaction():
            if meta.get("unattended", False) or not Confirm.ask("[bold red] Upload Anyways?"):
                return True
        return False

    banned_types = banned_group_list.get(group_tag)
    if banned_types is not None:
        if not banned_types or "ALL" in banned_types:
            console.print(
                f"[bold yellow]{group_tag}[/bold yellow][bold red] was found on [bold yellow]{tracker}[/bold yellow] list of banned groups."
            )
            with progress.pause_for_interaction():
                return bool(meta.get("unattended", False) or not Confirm.ask("[bold red] Upload Anyways?"))
        elif release_type and release_type in banned_types:
            console.print(
                f"[bold yellow]{group_tag}[/bold yellow][bold red] was found on [bold yellow]{tracker}[/bold yellow] list of banned groups for type [bold yellow]{release_type}[/bold yellow]."
            )
            with progress.pause_for_interaction():
                return bool(meta.get("unattended", False) or not Confirm.ask("[bold red] Upload Anyways?"))

    return False
