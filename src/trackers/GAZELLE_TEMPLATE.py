from __future__ import annotations

from typing import Any, ClassVar

from src.trackers.GAZELLE import Gazelle


class SpecificTracker(Gazelle):
    tracker = "Abbreviated"
    type = "gazelle"  # or "luminance"
    source_flag = "Source_Flag_AKA_Stamp"
    domain = "https://domain.tld"
    allowed_imghosts: ClassVar[
        list[str]
    ] = []  # ex: ["ibb.co", "ptscreens.com"] leave empty if no restrictions
    banned_groups: ClassVar[dict[str, Any]] = [
        ""
    ]  # Supports Dict with types (See: Wiki or BLU.py for examples)

    async def get_cat_id(self, meta: dict[str, Any]) -> str:
        """
        Subclasses must implement this method to return the category ID for the tracker.
        """
        raise NotImplementedError("Subclasses must implement get_cat_id")

    async def banned_content(self, meta: dict[str, Any]) -> tuple[bool, bool]:
        """
        Check if content is banned.
        Returns (True, True) for immediate ban, (True, False) for warning, (False, False) for no ban.
        """
        # TODO: Implement specific banned content checks based on tracker rules
        return False, False

    # Example override: If this tracker needs custom upload logic
    # async def upload(self, meta: Dict[str, Any]) -> bool:
    #     # Custom pre-upload step
    #     print(f"Custom logic for {self.tracker}")
    #     return await super().upload(meta)  # Reuse base, or replace entirely
