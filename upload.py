#!/usr/bin/env python
from __future__ import annotations

import asyncio
import sys
import time
import warnings
from pathlib import Path
from typing import Any

# qbittorent pulls in requests which can balk, in practice its noise
warnings.filterwarnings(
    "ignore",
    message=r"urllib3 .* or chardet .* doesn't match a supported version",
)

import src.utils.imports  # noqa: F401
from src.args import (
    apply_special_logic,
    build_parser,
    enforce_global_only,
    group_args_by_path,
    normalize_args,
)
from src.cleanrr import CleanupHandler
from src.clients import Clients
from src.exceptions import BDInfoExtractionError
from src.prep import Prep
from src.progress import ProgressManager
from src.screenshots.manager import ScreenshotManager
from src.utils.checks import pre_upload_checks
from src.utils.config import append_trackers_to_config, check_python_version, load_yaml_config
from src.utils.console import console, log, redactor, set_log_level
from src.utils.loads import load_trackers
from src.utils.meta import edit_meta, load_existing_meta, save_meta
from src.utils.queue import apply_delay, expand_auto_queue, normalize_trackers
from src.utils.torrentman import handle_torrent_creation
from src.utils.ui import print_banner, print_summary
from src.utils.upload import get_confirmation, upload_to_trackers
from src.version import version

# Globals
python_cmd = sys.executable
base_dir = Path(__file__).resolve().parent
data_dir = base_dir / "data"
config_path = data_dir / "config.yaml"
legacy_config = data_dir / "config.py"
old_config_path = data_dir / "backup" / "old_config.py"


class Uploadrr:
    def __init__(self, config: dict):
        self.config = config
        self.handler = CleanupHandler(name="Uploadrr")
        self.client = Clients(config=self.config)

    async def do_the_thing(self, base_dir: Path) -> None:
        base_meta: dict[str, Any] = {"base_dir": str(base_dir)}
        raw_argv = sys.argv[1:]

        # =====================================================================
        # 1. EARLY EXPANSION: --auto-queue → inject paths
        # =====================================================================
        expanded_argv = expand_auto_queue(raw_argv)

        # =====================================================================
        # 2. GROUP CLI: per-item blocks + global trailing args
        # =====================================================================
        items, global_argv = group_args_by_path(expanded_argv)

        # clean exit on help
        if any(a in ("-h", "--help") for a in sys.argv[1:]):
            parser = build_parser(self.config)
            parser.print_help()
            sys.exit(0)

        if not items:
            console.print("[red]❌ No valid paths to process.[/red]")
            sys.exit(1)

        # =====================================================================
        # 3. BUILD PARSER
        # =====================================================================
        parser = build_parser(self.config)
        print_banner()

        # =====================================================================
        # 4. PARSE GLOBAL ARGS
        # =====================================================================
        try:
            global_ns = parser.parse_args(global_argv)
            enforce_global_only(global_ns, is_global=True, parser=parser)
        except SystemExit as exc:
            # argparse calls sys.exit(0) on --help and sys.exit(2) on error — re-raise on 0
            if exc.code == 0:
                raise
            console.print("[red]❌ Error parsing global arguments.[/red]")
            sys.exit(1)
        except Exception as e:
            console.print(f"[red]❌ Global arg parsing failed: {e}[/red]")
            sys.exit(1)

        global_meta = normalize_args(global_ns)
        global_meta = apply_special_logic({**base_meta, **global_meta})

        # =====================================================================
        # 5. PARSE & MERGE PER-ITEM ARGS
        # =====================================================================
        queue: list[dict[str, Any]] = []
        for i, (arg_block, path) in enumerate(items, 1):
            item_updates: dict[str, Any] = {}
            if arg_block:
                try:
                    ns = parser.parse_args(arg_block)
                    enforce_global_only(ns, is_global=False, parser=parser)
                    raw = normalize_args(ns)
                    item_updates = {k: v for k, v in raw.items() if v is not None and v != [] and v != ""}
                except Exception as e:
                    console.print(f"[red]❌ Item {i} ({path}): {e}[/red]")
                    sys.exit(1)

            item_meta = {**global_meta, **item_updates}
            item_meta = apply_special_logic(item_meta)
            item_meta["path"] = path
            queue.append(item_meta)

        # =====================================================================
        # 6. SET LOG LEVEL (from final global meta)
        # =====================================================================
        set_log_level(
            debug=global_meta.get("debug", False),
            ddebug=global_meta.get("deep_debug", False),
            quiet=global_meta.get("quiet", False),
        )

        # =====================================================================
        # 7. APPLY BATCH SLICE
        # =====================================================================
        batch = global_meta.get("batch")
        if batch:
            start, end = batch
            total_before = len(queue)
            queue = queue[start:end]
            log.info(f"Batch slice: {start + 1}-{end} (total {total_before} → {len(queue)})")

        # =====================================================================
        # 8. SHOW QUEUE (if requested)
        # =====================================================================
        def _print_queue(queue: list[dict]) -> None:
            for i, item in enumerate(queue, 1):
                console.print(f"[turquoise2]{i}.[/turquoise2] [light_cyan3]{Path(item['path']).name}")
            console.print(f"\nTotal: [bold turquoise2]{len(queue)}[/bold turquoise2]\n")

        if global_meta.get("list_only"):
            console.print(f"\n[bold green]Queue list ({len(queue)} items):[/bold green]\n")
            _print_queue(queue)
            sys.exit(0)

        if not global_meta.get("hide_queue"):
            console.print("\n[bold green]Queuing these files:[/bold green]\n")
            _print_queue(queue)

        # =====================================================================
        # 9. VALIDATE TRACKERS
        # =====================================================================
        trackers = normalize_trackers(global_meta)
        tracker_registry = load_trackers(trackers, self.config)
        if not any(t in tracker_registry or t == "MANUAL" for t in trackers):
            console.print(f"[bold red]❌ No valid trackers: {', '.join(trackers)}[/bold red]")
            sys.exit(1)

        # =====================================================================
        # 10. RUN QUEUE
        # =====================================================================
        try:
            await self._process_queue(queue, global_meta, trackers, tracker_registry, base_dir)
        except KeyboardInterrupt:
            console.print("\n[yellow]⚠️ Upload interrupted by user.[/yellow]")
            raise

    async def _process_queue(
        self,
        queue: list[dict[str, Any]],
        global_meta: dict[str, Any],
        trackers: list[str],
        tracker_registry: dict[str, Any],
        base_dir: Path,
    ) -> None:
        """Process the upload queue — one item at a time."""

        log.info("[yellow]Starting queue processing[/yellow]")
        total = len(queue)
        progress = ProgressManager(
            console, quiet=global_meta.get("quiet", False), unattended=global_meta.get("unattended", False)
        )
        progress.start(total)

        screenshot_manager = ScreenshotManager(config=self.config, base_dir=base_dir)

        successful = skipped = 0
        skipped_details = []
        skipped_tmdb = []

        try:
            for idx, item_meta in enumerate(queue, 1):
                path = item_meta["path"]
                item_name = Path(path).name
                progress.update(f"Processing {item_name} ({idx}/{total})")

                item_meta = await load_existing_meta(
                    base_dir, path, item_meta, clean_meta=global_meta.get("clean_meta", False)
                )
                await apply_delay(item_meta, self.config)

                if not Path(path).exists():
                    progress.update(f"Processing {item_name}: Skipped (path does not exist)")
                    skipped += 1
                    skipped_details.append((path, "No media found — path does not exist."))
                    skipped_tmdb.append(path)
                    continue

                prep = Prep(config=self.config, base_dir=base_dir)
                try:
                    item_meta = await prep.gather_prep(meta=item_meta)
                except BDInfoExtractionError as e:
                    log.warning(f"[yellow]Skipping due to prep error: {e}[/yellow]")
                    progress.update(f"Processing {item_name}: Skipped (Prep error)")
                    skipped += 1
                    skipped_details.append((path, "Prep error"))
                    continue

                await save_meta(item_meta, base_dir, item_meta["uuid"])

                if item_meta.get("tmdb_not_found") and not item_meta.get("xxx"):
                    progress.update(f"Processing {item_name}: Skipped (TMDB not found)")
                    skipped += 1
                    skipped_details.append((path, "TMDB not found"))
                    skipped_tmdb.append(path)
                    continue

                if item_meta.get("invalid_media", False):
                    progress.update(f"Processing {item_name}: Skipped (NOT A VALID MEDIATYPE)")
                    skipped += 1
                    skipped_details.append((path, "Not a valid media type."))
                    skipped_tmdb.append(path)
                    continue

                screens_handled = False

                async def handle_screens(whitelists: dict):
                    nonlocal item_meta
                    item_meta = await screenshot_manager.handle_screenshots(item_meta, whitelists)
                    await save_meta(item_meta, base_dir, item_meta["uuid"])

                needs_screenshots = int(item_meta.get("screens", 0)) or item_meta.get("user_images")

                # Pre-compute tracker whitelists for early screenshot generation
                if self.config.get("SCREENS", {}).get("generate_b4_confirm", False) and needs_screenshots:
                    early_whitelists = {
                        t: getattr(tracker_registry[t]["class"](config=self.config), "allowed_imghosts", [])
                        for t in trackers
                        if t in tracker_registry
                    }
                    await handle_screens(early_whitelists)
                    screens_handled = True

                tracker_upload_ok = await pre_upload_checks(
                    item_meta, trackers, tracker_registry, skipped_details, path, self.config
                )
                if not any(tracker_upload_ok.values()):
                    progress.update(f"Processing {item_name}: Skipped (pre-upload checks failed)")
                    skipped += 1
                    skipped_details.append((path, "Pre-upload checks failed"))
                    continue

                viable_trackers = [t for t, ok in tracker_upload_ok.items() if ok]
                if not item_meta.get("unattended"):
                    with progress.pause_for_interaction():
                        confirm = await get_confirmation(
                            item_meta, viable_trackers, tracker_registry, self.config
                        )
                        if not confirm:
                            item_meta = await edit_meta(
                                prep, item_meta, viable_trackers, tracker_registry, self.config
                            )
                        if not item_meta:
                            progress.update(f"Processing {item_name}: Skipped (user cancelled)")
                            skipped += 1
                            skipped_details.append((path, "User cancelled"))
                            continue
                else:
                    console.print(f"[yellow]Unattended mode: Skipping confirmation for {path}[/yellow]")

                tracker_whitelists = {
                    t: getattr(tracker_registry[t]["class"](config=self.config), "allowed_imghosts", [])
                    for t in viable_trackers
                    if t in tracker_registry
                }

                if needs_screenshots and not screens_handled:
                    await handle_screens(tracker_whitelists)

                item_meta = await handle_torrent_creation(self.config, item_meta)

                success, skip = await upload_to_trackers(
                    item_meta,
                    viable_trackers,
                    tracker_registry,
                    skipped_details,
                    path,
                    progress,
                    self.client,
                    self.config,
                )
                successful += success
                skipped += skip

                progress.update(f"Processing {item_name}: {'Uploaded' if success else 'Skipped'}")

        except KeyboardInterrupt:
            progress.update("Cancelling...")
            raise
        finally:
            if "item_meta" in locals():
                await save_meta(item_meta, base_dir, item_meta.get("uuid"))
            progress.stop()

        print_summary(total, successful, skipped, skipped_details, skipped_tmdb)

    async def shutdown(self) -> None:
        """Clean up resources on shutdown."""
        await self.handler.shutdown()


def _parse_add_tracker(argv: list[str]) -> list[str] | None:
    names: list[str] = []
    found = False
    for arg in argv:
        if arg in ("--add-missing-tracker", "-amt"):
            found = True
            continue
        if arg.startswith("--add-missing-tracker="):
            found = True
            val = arg.split("=", 1)[1]
            if val:
                names.append(val.upper())
            continue
        if found:
            if arg.startswith("-"):
                break
            names.append(arg.upper())
    return names if found else None


async def main() -> None:
    start_time = time.perf_counter()
    check_python_version()
    if not config_path.exists():
        if legacy_config.exists():
            log.error("Legacy Config detected, please create a new yaml config, thank you!")
            sys.exit(1)
        else:
            log.error("No Config detected, please setup config before starting, thank you!")
            sys.exit(1)
    config = load_yaml_config(config_path)

    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        parser = build_parser(config)
        parser.print_help()
        sys.exit(0)

    add_tracker_names = _parse_add_tracker(sys.argv[1:])
    if add_tracker_names is not None:
        example_path = data_dir / "backup" / "example_config.yaml"
        if not example_path.exists():
            log.error(f"Example config not found at {example_path}")
            sys.exit(1)
        example = load_yaml_config(example_path)
        active_trackers = config.setdefault("TRACKERS", {})
        example_trackers = example.get("TRACKERS", {})

        if not add_tracker_names:
            to_add = [k for k in example_trackers if k != "default_trackers"]
        else:
            to_add = add_tracker_names

        added: list[str] = []
        skipped: list[str] = []
        missing: list[str] = []
        for tracker in to_add:
            if tracker == "default_trackers":
                continue
            if tracker in active_trackers:
                skipped.append(tracker)
                continue
            if tracker not in example_trackers:
                missing.append(tracker)
                continue
            added.append(tracker)

        if added:
            append_trackers_to_config(config_path, {t: example_trackers[t] for t in added})
            console.print(f"[green]Added {len(added)} tracker(s): {', '.join(added)}[/green]")
        if skipped:
            console.print(f"[yellow]Skipped (already in config): {', '.join(skipped)}[/yellow]")
        if missing:
            console.print(f"[red]Not found in example config: {', '.join(missing)}[/red]")
        if not added and not skipped and not missing:
            console.print("[yellow]No trackers to add.[/yellow]")
        sys.exit(0)

    redactor.load_from_config(config)
    redactor.add_rule(str(base_dir), "Uploadrr")
    redactor.add_rule(str(Path.home()), "HOME")
    if sys.platform == "win32":
        winbase = str(base_dir.as_posix())
        winhome = str(Path.home().as_posix())
        redactor.add_rule(winbase, "Uploadrr")
        redactor.add_rule(winhome, "HOME")
    uploadrr = Uploadrr(config)
    try:
        await uploadrr.do_the_thing(base_dir)
    except KeyboardInterrupt:
        console.print("[bold red]Interrupted by user. Cleaning up...[/bold red]")
        raise

    finally:
        end_time = time.perf_counter()
        console.print("[magenta]see you soon ^.<", justify="center")
        log.debug(f"Uploadrr v{version} took {end_time - start_time:.4f} seconds too long.")


def _run_event_loop(coro):
    """Run a coroutine and cleanly shut down the loop."""
    import functools

    def _silencer(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except Exception:
                pass

        return wrapper

    try:
        from asyncio.sslproto import _SSLProtocolTransport

        _SSLProtocolTransport.__del__ = _silencer(_SSLProtocolTransport.__del__)
    except ImportError:
        pass

    if sys.platform == "win32":
        try:
            from asyncio.proactor_events import _ProactorBasePipeTransport

            _ProactorBasePipeTransport.__del__ = _silencer(_ProactorBasePipeTransport.__del__)
        except ImportError:
            pass

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        pending = asyncio.all_tasks(loop)
        if pending:
            for task in pending:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


if __name__ == "__main__":
    cleanrr = CleanupHandler(name="Uploadrr")
    try:
        _run_event_loop(main())
    except KeyboardInterrupt:
        console.print("[bold red]Interrupted by user. Cleaning up...[/bold red]")
        asyncio.run(cleanrr.shutdown())
        sys.exit(1)
