from __future__ import annotations

import json
from pathlib import Path

_groups: dict[str, frozenset[str]] | None = None


def _load() -> dict[str, frozenset[str]]:
    global _groups
    if _groups is None:
        path = Path(__file__).resolve().parent.parent.parent / "data" / "country_groups.json"
        try:
            with path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            _groups = {k: frozenset(v) for k, v in raw.items()}
        except FileNotFoundError:
            _groups = {}
        except json.JSONDecodeError:
            _groups = {}
    return _groups


def get_group(name: str) -> frozenset[str]:
    return _load().get(name.upper(), frozenset())
