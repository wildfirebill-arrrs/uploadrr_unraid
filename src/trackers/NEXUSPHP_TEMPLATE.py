from __future__ import annotations

from typing import Any, ClassVar

from src.trackers.NEXUSPHP import NexusPHP


class SpecificTracker(NexusPHP):
    tracker = "Abbreviated"
    source_flag = "Source_Flag_AKA_Stamp"
    domain = "https://domain.tld"
    tracker_url = None  # default: tracker.domain.tld if diffrent specify here
    simple_name: bool = False
    # fmt:off
    allowed_imghosts: ClassVar[list[str]] = []  # ["ibb.co", "ptscreens.com"] leave empty if no restrictions
    banned_groups: ClassVar[dict[str, Any]] = {"ALL_TYPES": [""]}  # Supports Dict with types (See: Wiki or BLU.py for examples)
    # fmt: on

    MEDIUM_MAP: ClassVar[dict[str, str]] = {}  # medium_sel values
    CODEC_MAP: ClassVar[dict[str, str]] = {}  # codec_sel values (video)
    TEAM_MAP: ClassVar[dict[str, str]] = {}  # team_sel values

    # AUDIOCODEC_MAP and ZONE_MAP/ZONE_DEFAULT are inherited from NexusPHP but
    # should be overridden if your tracker uses different IDs.

    async def get_cat_id(self, meta: dict[str, Any]) -> str:
        """Return category ID for upload form. Must be implemented."""
        raise NotImplementedError

    async def get_type_id(self, meta: dict[str, Any]) -> str:
        """Return source/medium type ID for upload form. Must be implemented."""
        raise NotImplementedError

    async def get_medium(self, meta: dict[str, Any]) -> str:
        """Return medium_sel ID. Default is empty; override with MEDIUM_MAP lookups."""
        return ""

    async def get_video_id(self, meta: dict[str, Any]) -> str:
        """Return codec_sel ID for video. Default is empty; override with CODEC_MAP lookups."""
        return ""

    async def get_team(self, meta: dict[str, Any]) -> str:
        """Return team_sel ID. Default is empty; override with TEAM_MAP lookups."""
        return ""

    async def get_tags(self, meta: dict[str, Any]) -> list[str]:
        """Return list of tag values for tags. Override per tracker."""
        return []

    # Example override: If this tracker needs custom upload logic
    # async def upload(self, meta: dict[str, Any]) -> bool | str:
    #     # Custom pre-upload step
    #     print(f"Custom logic for {self.tracker}")
    #     return await super().upload(meta)  # Reuse base, or replace entirely
