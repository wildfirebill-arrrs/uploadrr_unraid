from __future__ import annotations

import importlib
import json
from pathlib import Path

from src.utils.coffee_filter import tracker_filter
from src.utils.console import log


def load_json(filepath: Path) -> dict:
    """Reusable JSON loader with error handling. Efficient: Called in properties."""
    try:
        with filepath.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as err:
        raise FileNotFoundError(f"Could not find JSON file: {filepath}") from err
    except json.JSONDecodeError as err:
        raise ValueError(f"Invalid JSON format in file: {filepath}") from err


def load_trackers(tracker_names: list[str], config: dict) -> dict:
    """Dynamically load tracker modules."""
    src_dir = Path(__file__).resolve().parent.parent
    tracker_dir = src_dir / "trackers"
    tracker_registry = {}
    for tracker in {t.strip().upper() for t in tracker_names if t.strip() and t != "MANUAL"}:
        module_name = f"src.trackers.{tracker}"
        tracker_file = tracker_dir / f"{tracker}.py"
        if not tracker_file.exists():
            log.error(f"[bold]Tracker not found: {tracker}[/bold]")
            continue
        try:
            module = importlib.import_module(module_name)
            tracker_class = getattr(module, tracker)
            instance = tracker_class(config=config)
            tracker_registry[instance.tracker] = {
                "class": tracker_class,
                "type": getattr(instance, "type", "other"),
            }
            if getattr(instance, "simple_name", False):
                tracker_filter(instance.tracker, skips=["ResolutionRule", "HDRRule"])
        except Exception as e:
            log.error(f"Failed to load {tracker}: {e}")
    return tracker_registry


# === DISTRIBUTION MAPPER ===

_distributors: dict[str, int] = None


def _load_distributors() -> dict:
    """Load distributors with UNIt3D ID mappings from JSON and caches them."""
    global _distributors
    if _distributors is None:
        json_path = Path(__file__).resolve().parent.parent.parent / "data" / "distributors.json"
        try:
            with json_path.open("r", encoding="utf-8") as f:
                _distributors = json.load(f)
        except FileNotFoundError:
            log.error(f"[bold red]Distributors file not found:[/bold red] {json_path}")
            _distributors = {}
        except json.JSONDecodeError as e:
            log.error(f"[bold red]Invalid JSON in distributors.json:[/bold red] {e}")
            _distributors = {}
    return _distributors


def get_distributor(distributor: str | None) -> tuple[str, int]:
    """Validates distributor name, returning its name and ID."""
    if not distributor or distributor.strip() in ["", "None", "none"]:
        return ("", 0)
    distributor_upper = distributor.strip().upper()
    distributors = _load_distributors()
    distributor_id = distributors.get(distributor_upper, 0)
    return (distributor_upper, distributor_id)


# === REGION MAPPER ===

_regions: dict[str, int] = None


def _load_regions() -> dict:
    """Load regions with UNIt3D ID mappings from JSON and caches them."""
    global _regions
    if _regions is None:
        json_path = Path(__file__).resolve().parent.parent.parent / "data" / "regions.json"
        try:
            with json_path.open("r", encoding="utf-8") as f:
                _regions = json.load(f)
        except FileNotFoundError:
            log.error(f"[bold red]Distributors file not found:[/bold red] {json_path}")
            _regions = {}
        except json.JSONDecodeError as e:
            log.error(f"[bold red]Invalid JSON in distributors.json:[/bold red] {e}")
            _regions = {}
    return _regions


def get_region(region: str | None) -> tuple[str, int]:
    """Validates region name, returning its name and ID."""
    if not region or region.strip() in ["", "None", "none"]:
        return ("", 0)
    region_upper = region.strip().upper()
    regions = _load_regions()
    region_id = regions.get(region_upper, 0)
    return (region_upper, region_id)
