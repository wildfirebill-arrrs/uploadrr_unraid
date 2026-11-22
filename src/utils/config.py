import os
import platform
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from src.utils.console import console, log


def get_backup_name(path: str, suffix: str = "_bu") -> str:
    """Generate a unique backup filename."""
    base, ext = os.path.splitext(path)
    counter = 1
    new_path = f"{base}{suffix}{counter}{ext}"
    while os.path.exists(new_path):
        counter += 1
        new_path = f"{base}{suffix}{counter}{ext}"
    return new_path


def load_yaml_config(path: Path) -> dict:
    """Load YAML config from file."""
    try:
        with open(path, encoding="utf-8") as f:
            config_data = yaml.safe_load(f) or {}
        return config_data
    except Exception as e:
        log.error(f"[bold red]Error loading config.yaml: {e}[/bold red]")
        sys.exit(1)


def save_yaml_config(path: Path, data: dict) -> None:
    """Save dict to YAML config file, creating backup if exists."""
    try:
        if path.exists():
            backup = get_backup_name(str(path))
            path.rename(backup)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    except Exception as e:
        log.error(f"[bold red]Error saving config.yaml: {e}[/bold red]")
        sys.exit(1)


def append_trackers_to_config(path: Path, trackers: dict[str, Any]) -> None:
    if not trackers:
        return

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    # Skip any that already exist as indented keys to avoid duplication
    present = set()
    for line in lines:
        for name in trackers:
            if re.search(rf"^\s+{re.escape(name)}:", line):
                present.add(name)
                break
    trackers = {k: v for k, v in trackers.items() if k not in present}
    if not trackers:
        return

    backup_dir = path.parent / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    counter = 1
    backup = backup_dir / f"old_config{counter}.yaml"
    while backup.exists():
        counter += 1
        backup = backup_dir / f"old_config{counter}.yaml"
    path.rename(backup)

    # Locate TRACKERS block
    trackers_idx = None
    for i, line in enumerate(lines):
        if line.strip() == "TRACKERS:":
            trackers_idx = i
            break

    def _block_end(start_idx: int) -> int:
        """End of the TRACKERS block: first non-indented, non-blank line."""
        i = start_idx + 1
        while i < len(lines):
            line = lines[i]
            if not line or not line.strip():
                i += 1
                continue
            if not line.startswith(" ") and not line.startswith("\t"):
                return i
            i += 1
        return len(lines)

    # Build indented YAML snippets
    snippets = []
    for idx, (name, data) in enumerate(trackers.items()):
        yaml_text = yaml.dump({name: data}, default_flow_style=False, sort_keys=False, allow_unicode=True)
        if not yaml_text.endswith("\n"):
            yaml_text += "\n"
        snippets.extend(f"  {ln}" for ln in yaml_text.splitlines(keepends=True))
        if idx < len(trackers) - 1:
            snippets.append("\n")

    if trackers_idx is None:
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append("TRACKERS:\n")
        lines.extend(snippets)
        lines.append("\n")
    else:
        insert_idx = _block_end(trackers_idx)
        if lines and insert_idx > 0:
            prev = lines[insert_idx - 1]
            if prev.strip():
                snippets = ["\n", *snippets]
        lines = lines[:insert_idx] + snippets + lines[insert_idx:]

    path.write_text("".join(lines), encoding="utf-8")


def check_python_version() -> None:
    """Ensure Python version compatibility."""
    pyver = platform.python_version_tuple()
    if int(pyver[0]) != 3 or int(pyver[1]) <= 8:
        console.print("[bold red]Legacy Python Detected. Please use Python 3.9+[/bold red]")
        sys.exit(1)
