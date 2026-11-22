import platform
import re
from pathlib import Path
from typing import Final

_PYPROJECT: Final[Path] = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _read_version() -> str:
    try:
        text = _PYPROJECT.read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        return match.group(1) if match else "3.0.0"
    except Exception:
        return "3.0.0"


version: Final[str] = _read_version()


def user_agent() -> str:
    return f"Uploadrr / v{version} ({platform.system()} {platform.release()})"
