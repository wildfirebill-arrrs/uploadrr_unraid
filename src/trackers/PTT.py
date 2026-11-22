from __future__ import annotations

from typing import Any, ClassVar

from src.trackers.UNIT3D import Unit3d


class PTT(Unit3d):
    tracker = "PTT"
    source_flag = "PTT"
    domain = "https://polishtorrent.top"
    allowed_imghosts: ClassVar[list[str]] = []
    banned_groups: ClassVar[dict[str, Any]] = [
        "ViP",
        "BiRD",
        "M@RTiNU$",
        "inTGrity",
        "PSiG",
        "CiNEMAET",
        "MusicET",
        "TeamET",
        "R2D2",
    ]
