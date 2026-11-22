from __future__ import annotations

from typing import Any, ClassVar

from src.trackers.UNIT3D import Unit3d


class OINK(Unit3d):
    tracker = "OINK"
    source_flag = "YOiNKED"
    domain = "https://yoinked.org"
    allowed_imghosts: ClassVar[list[str]] = []
    banned_groups: ClassVar[dict[str, Any]] = ["GalaxyRG", "LAMA", "MeGUSTA", "NAHOM", "RARBG", "YTS", "YiFY"]
