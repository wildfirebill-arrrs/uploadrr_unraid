from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from rich.progress import Progress, TimeRemainingColumn

from src.utils.console import console


def normalize_trackers(meta: dict) -> list[str]:
    """Normalize tracker list from meta or config."""
    trackers = meta["trackers"]
    if isinstance(trackers, list):
        trackers = ",".join(trackers)
    trackers = [t.strip().upper() for t in trackers.split(",")]
    if meta.get("manual", False):
        trackers.insert(0, "MANUAL")
    return trackers


def expand_auto_queue(argv: list[str]) -> list[str]:
    """Expand --auto-queue DIR → list of paths in DIR (non-hidden)"""
    expanded: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-aq", "--auto-queue"):
            if i + 1 >= len(argv):
                console.print("[red]--auto-queue requires a directory[/red]")
                sys.exit(1)
            dir_path = Path(argv[i + 1])
            if not dir_path.is_dir():
                console.print(f"[red]--auto-queue directory not found: {dir_path}[/red]")
                sys.exit(1)
            for entry in sorted(dir_path.iterdir(), key=lambda e: str(e).lower()):
                if not entry.name.startswith(".") and entry.exists():
                    expanded.append(str(entry.resolve()))
            i += 2
        else:
            expanded.append(arg)
            i += 1
    return expanded


def list_directory(directory: str) -> list[str]:
    """List non-hidden files in directory."""
    return [
        str(file.resolve())
        for file in sorted(Path(directory).iterdir(), key=lambda e: str(e).lower())
        if not file.name.startswith(".")
    ]


async def apply_delay(meta: dict, config: dict) -> None:
    """Apply delay between uploads if specified."""
    delay = meta.get("delay") or config.get("AUTO", {}).get("delay", 0)
    if delay > 0:
        with Progress(
            "[progress.description]{task.description}", TimeRemainingColumn(), transient=True
        ) as progress:
            task = progress.add_task("[cyan]Auto delay...", total=delay)
            for _ in range(delay):
                await asyncio.sleep(1)
                progress.update(task, advance=1)
