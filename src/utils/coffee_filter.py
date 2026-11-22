"""
The Great Coffee Filter - keeping it fresh.
================================

Duplicate detection and filtering across different tracker types (Unit3D, Gazelle, etc.).

- DupeNormalizer: Handles data structure conversion
- DupeFilter: Main filtering engine with rule plugins
- Matcher: Size and name similarity matching
- RuleRegistry: Manages filter rules by tracker and type
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from rich.prompt import Confirm

from src.utils.console import log

# ===============================
# Data Models
# ===============================


class DupeEntry:
    """
    Unified dupe entry structure that all processing uses internally.
    """

    def __init__(
        self,
        name: str,
        size: int | None = None,
        files: list[str] | None = None,
        file_count: int | None = None,
        trumpable: bool = False,
        link: str | None = None,
        raw_data: dict[str, Any] | None = None,
    ):
        self.name = name
        self.size = size
        self.files = files or []
        self.file_count = file_count or len(files) if files else 0
        self.trumpable = trumpable
        self.link = link
        self.raw_data = raw_data or {}

    def __repr__(self) -> str:
        return f"DupeEntry(name='{self.name}', size={self.size}, file_count={self.file_count})"


class MetaContext:
    """
    Context object containing all metadata for filtering decisions.
    """

    def __init__(self, meta: dict[str, Any], config: dict[str, Any], tracker_name: str):
        self.meta = meta
        self.config = config
        self.tracker_name = tracker_name
        self.debug = meta.get("debug", False)

        # Extract commonly used values
        self.is_disc = meta.get("is_disc", False)
        self.category = meta.get("category", "")
        self.resolution = meta.get("resolution", "")
        self.hdr = meta.get("hdr", "")
        self.type = meta.get("type", "")
        self.season = meta.get("season", "")
        self.season_int = meta.get("season_int", "")
        self.episode = meta.get("episode", "")
        self.sd = meta.get("sd", 0)
        self.tv_pack = meta.get("tv_pack", 0)
        self.tag = meta.get("tag", "").lower().replace("-", " ")
        self.clean_name = meta.get("clean_name", "")
        self.xxx = meta.get("xxx", False)
        self.video_encode = meta.get("video_encode")
        self.content_size = meta.get("content_size", 0)
        self.unattended = meta.get("unattended", False)

        # Extract filename list for file matching
        self.filenames = []
        if not self.is_disc and meta.get("filelist"):
            self.filenames = [Path(f).name for f in meta.get("filelist", [])]


# ===============================
# Rule System
# ===============================


class FilterRule(ABC):
    """Base class for all filtering rules."""

    @abstractmethod
    async def should_exclude(self, entry: DupeEntry, context: MetaContext) -> tuple[bool, str | None]:
        """
        Determine if an entry should be excluded.

        Returns:
            Tuple of (should_exclude: bool, reason: Optional[str])
        """
        pass


class CompositeRule(FilterRule):
    """Composite rule that combines multiple rules with AND/OR logic."""

    def __init__(self, rules: list[FilterRule], operator: str = "AND"):
        self.rules = rules
        self.operator = operator.upper()

    async def should_exclude(self, entry: DupeEntry, context: MetaContext) -> tuple[bool, str | None]:
        if self.operator == "AND":
            for rule in self.rules:
                exclude, reason = await rule.should_exclude(entry, context)
                if exclude:
                    return exclude, reason
            return False, None
        else:  # OR
            reasons = []
            for rule in self.rules:
                exclude, reason = await rule.should_exclude(entry, context)
                if exclude:
                    reasons.append(reason or "Rule matched")
            return bool(reasons), "; ".join(reasons) if reasons else None


class RegexRule(FilterRule):
    """Rule based on regex pattern matching."""

    def __init__(self, pattern: str, exclude_on_match: bool = True, case_sensitive: bool = False):
        self.pattern = pattern
        self.exclude_on_match = exclude_on_match
        self.case_sensitive = case_sensitive
        self.compiled_pattern = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)

    async def should_exclude(self, entry: DupeEntry, context: MetaContext) -> tuple[bool, str | None]:
        normalized_name = entry.name.lower().replace("-", "").replace(" ", "").replace(".", "")
        match = self.compiled_pattern.search(normalized_name)

        should_exclude = bool(match) if self.exclude_on_match else not bool(match)
        reason = f"Regex {'match' if match else 'no match'}: {self.pattern}" if should_exclude else None

        return should_exclude, reason


class SizeRule(FilterRule):
    """Rule for size-based filtering."""

    def __init__(self, tolerance: float = 0.3):
        self.tolerance = tolerance

    async def should_exclude(self, entry: DupeEntry, context: MetaContext) -> tuple[bool, str | None]:
        if not entry.size or not context.content_size:
            return False, None

        size_diff = abs(context.content_size - entry.size) / context.content_size
        should_exclude = size_diff > self.tolerance

        return (
            should_exclude,
            f"Size difference {size_diff:.2%} exceeds tolerance {self.tolerance:.2%}"
            if should_exclude
            else None,
        )


class ResolutionRule(FilterRule):
    """Rule for resolution matching."""

    async def should_exclude(self, entry: DupeEntry, context: MetaContext) -> tuple[bool, str | None]:
        if not context.resolution:
            return False, None

        is_dvd = context.is_disc == "DVD" or "DVD" in context.meta.get("source", "")
        is_dvdrip = context.type == "DVDRIP"

        if is_dvd or is_dvdrip:
            return False, None

        if context.resolution not in entry.name:
            return True, f"Resolution '{context.resolution}' not found in '{entry.name}'"

        return False, None


class HDRRule(FilterRule):
    """Rule for HDR matching with term normalization."""

    @staticmethod
    def _normalize_hdr_terms(hdr_text: str) -> set[str]:
        if not hdr_text:
            return set()

        hdr_text = hdr_text.upper()
        terms = set()

        if "DV" in hdr_text or "DOVI" in hdr_text:
            terms.add("DV")
        if any(h in hdr_text for h in ["HDR", "HDR10", "HDR10+", "PQ10"]):
            terms.add("HDR")

        return terms

    async def should_exclude(self, entry: DupeEntry, context: MetaContext) -> tuple[bool, str | None]:
        target_hdr = self._normalize_hdr_terms(context.hdr)
        if not target_hdr:
            return False, None

        file_hdr = self._normalize_hdr_terms(entry.name)
        if not file_hdr:
            return False, None

        # Exact match required
        if target_hdr != file_hdr:
            return True, f"HDR mismatch: expected {target_hdr}, got {file_hdr}"

        return False, None


class SeasonEpisodeRule(FilterRule):
    """Rule for TV season/episode matching."""

    async def should_exclude(self, entry: DupeEntry, context: MetaContext) -> tuple[bool, str | None]:
        if context.category != "TV":
            return False, None

        normalized_entry = entry.name.lower()
        season_int = int(context.season_int) if context.season_int else 0
        season_match = re.search(rf"[sS]{season_int:02}", normalized_entry)

        if not season_match:
            return True, f"Season {context.season} not found in '{entry.name}'"

        # If episode specified, check for episode match
        if context.episode:
            episode_matches = re.findall(r"\d+", str(context.episode))
            target_episodes = [int(ep) for ep in episode_matches]

            # Check if this is a season pack (no episode patterns)
            is_season_pack = not re.search(r"[eE]\d{2}", normalized_entry)

            if is_season_pack:
                # For season packs, just checking season is enough
                return False, None

            episode_patterns = [rf"[eE]{ep:02}" for ep in target_episodes]
            episode_match = any(re.search(ep, normalized_entry) for ep in episode_patterns)

            if not episode_match:
                return True, f"Episodes {target_episodes} not found in '{entry.name}'"

        return False, None


class FileMatchRule(FilterRule):
    """Rule for exact file matching (used by specific trackers)."""

    async def should_exclude(self, entry: DupeEntry, context: MetaContext) -> tuple[bool, str | None]:
        if context.is_disc or not entry.files:
            return False, None

        # TODO MOVE TO TRACKER MODULE or if gazelle and torznab true condition?
        if context.tracker_name in ["MTV", "AR", "RTF"]:
            # Check if any dupe file is a substring of our files
            for dupe_file in entry.files:
                if any(dupe_file in file for file in context.filenames):
                    context.meta["filename_match"] = f"{entry.name} = {entry.link or 'No link'}"
                    if entry.file_count and entry.file_count == len(context.filenames):
                        context.meta["file_count_match"] = entry.file_count
                        return True, "Exact file match found"
        else:
            # Standard file matching
            for file in context.filenames:
                if file in entry.files:
                    context.meta["filename_match"] = f"{entry.name} = {entry.link or 'No link'}"
                    if entry.file_count and entry.file_count == len(context.filenames):
                        context.meta["file_count_match"] = entry.file_count
                        return True, "Exact file match found"

        return False, None


# ===============================
# Rule Registration System
# ===============================


class RuleRegistry:
    """Registry for managing filter rules across all modules."""

    def __init__(self):
        self._global_rules: list[FilterRule] = []
        self._tracker_rules: dict[str, list[FilterRule]] = {}  # Tracker -> custom rules to add
        self._tracker_skips: dict[str, set[str]] = {}  # Tracker -> set of rule class names to skip
        self._cached_rule_sets: dict[
            str, list[FilterRule]
        ] = {}  # Tracker -> precomputed rules (global + custom - skips)

    def register_rule(self, rule: FilterRule, tracker_name: str | None = None):
        if tracker_name:
            self._tracker_rules.setdefault(tracker_name, []).append(rule)
        else:
            self._global_rules.append(rule)
        self._invalidate_cache(tracker_name)  # Invalidate only affected cache

    def register_skips(self, tracker_name: str, skip_rule_names: list[str]):
        self._tracker_skips[tracker_name] = set(skip_rule_names)
        self._invalidate_cache(tracker_name)

    def _invalidate_cache(self, tracker_name: str | None):
        if tracker_name:
            self._cached_rule_sets.pop(tracker_name, None)
        else:
            self._cached_rule_sets.clear()  # Rare global invalidation

    def get_rules_for_context(self, context: MetaContext) -> list[FilterRule]:
        tracker = context.tracker_name
        if tracker in self._cached_rule_sets:
            return self._cached_rule_sets[tracker]

        # Compute: global + custom - skips
        rules = self._global_rules.copy()
        rules.extend(self._tracker_rules.get(tracker, []))

        skips = self._tracker_skips.get(tracker, set())
        rules = [r for r in rules if r.__class__.__name__ not in skips]

        self._cached_rule_sets[tracker] = rules
        return rules


# Global registry instance
_rule_registry = RuleRegistry()


def get_rule_registry() -> RuleRegistry:
    """Get the global rule registry instance."""
    return _rule_registry


# ===============================
# Core Processing Classes
# ===============================


class DupeNormalizer:
    """Handles conversion of various dupe data structures to unified format."""

    @staticmethod
    def normalize(dupes: dict | list[dict[str, Any]] | list[str] | str) -> list[DupeEntry]:
        """
        Normalize various dupe data formats to list of DupeEntry objects.

        Supported formats:
        - List[Dict]: [{"name": str, "size": int|None, "files": List[str]|None, ...}]
        - List[str]: ["name1", "name2"]
        - str: "single_name"
        """
        entries = []

        if isinstance(dupes, list):
            for item in dupes:
                if isinstance(item, dict):
                    name = item.get("name", "")
                    size = item.get("size")
                    files = item.get("files") or []
                    file_count = item.get("file_count")
                    trumpable = item.get("trumpable", False)
                    link = item.get("link")
                    raw_data = item

                    entries.append(
                        DupeEntry(
                            name=name,
                            size=size,
                            files=files,
                            file_count=file_count,
                            trumpable=trumpable,
                            link=link,
                            raw_data=raw_data,
                        )
                    )

                elif isinstance(item, str):
                    entries.append(DupeEntry(name=item, raw_data={"name": item}))

        elif isinstance(dupes, dict):
            for name, size in dupes.items():
                entries.append(
                    DupeEntry(
                        name=name,
                        size=size if isinstance(size, (int, float)) else None,
                        raw_data={"name": name, "size": size},
                    )
                )

        elif isinstance(dupes, str):
            entries.append(DupeEntry(name=dupes, raw_data={"name": dupes}))

        return entries


class DupeFilter:
    """Main filtering engine that delegates to the global rule registry."""

    def __init__(self):
        pass

    def add_rule(self, rule: FilterRule, scope: str = "default"):
        """Add a rule to the specified scope via the global registry."""
        registry = get_rule_registry()
        if scope == "default":
            registry.register_rule(rule)
        elif scope.startswith("tracker:"):
            tracker_name = scope.split(":", 1)[1]
            registry.register_rule(rule, tracker_name=tracker_name)
        else:
            # For backward compatibility, treat as default
            registry.register_rule(rule)

    def get_rules_for_context(self, context: MetaContext) -> list[FilterRule]:
        """Get all applicable rules for a given context."""
        registry = get_rule_registry()
        return registry.get_rules_for_context(context)

    async def filter_dupes(
        self,
        dupes: dict | list[dict[str, Any]] | list[str] | str,
        meta: dict[str, Any],
        config: dict[str, Any],
        tracker_name: str,
    ) -> list[DupeEntry]:
        """
        Filter duplicates using all applicable rules.
        """

        log.debug(f"[cyan]Pre-filtered dupes from {tracker_name}")
        log.debug(dupes)

        # Normalize dupe data
        entries = DupeNormalizer.normalize(dupes)

        if not entries:
            return entries

        # Create context
        context = MetaContext(meta, config, tracker_name)

        # Get applicable rules
        rules = self.get_rules_for_context(context)
        composite_rule = CompositeRule(rules) if rules else None

        # Filter entries
        filtered_entries = []
        for entry in entries:
            if composite_rule:
                should_exclude, reason = await composite_rule.should_exclude(entry, context)
                if should_exclude:
                    log.debug(f"[yellow]Excluding result due to {reason}: {entry.name}")
                else:
                    filtered_entries.append(entry)
            else:
                filtered_entries.append(entry)

        if filtered_entries and not meta.get("unattended", False):
            log.debug(f"[yellow]Filtered dupes on {tracker_name}: {[e.name for e in filtered_entries]}")

        return filtered_entries


class DupeMatcher:
    """Handles matching logic for duplicates."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.similarity_threshold = max(config.get("AUTO", {}).get("dupe_similarity", 90.00) / 100, 0.70)
        self.size_tolerance = max(min(config.get("AUTO", {}).get("size_tolerance", 30), 100), 1) / 100

    def calculate_similarity(self, name1: str, name2: str) -> float:
        """Calculate similarity between two names."""
        cleaned1 = re.sub(r"[^\w\s]", "", name1.lower())
        cleaned2 = re.sub(r"[^\w\s]", "", name2.lower())
        return SequenceMatcher(None, cleaned1, cleaned2).ratio()

    async def check_exact_match(self, meta: dict[str, Any], entry: DupeEntry, meta_size: int) -> bool:
        """Check for exact size match within a tiny tolerance (torrent padding / metadata overhead)."""
        if not entry.size or not meta_size:
            return False

        # 0.005% relative capped at 1 MiB (~1 MB max difference on big files)
        tolerance = min(int(meta_size * 0.00005), 1_048_576)
        size_diff = abs(entry.size - meta_size)
        if size_diff <= tolerance:
            log.warning(f"[red]Exact size match found: {entry.name}. Aborting..")
            return True
        return False

    async def check_similarity_match(
        self, meta: dict[str, Any], entry: DupeEntry, meta_size: int, cleaned_meta_name: str
    ) -> bool:
        """Check for similarity-based match."""
        if not entry.size:
            log.debug(f"[dim]similarity_match skipped (no entry.size): {entry.name!r}")
            return False

        if not meta_size:
            log.debug(
                f"[dim]similarity_match skipped (meta_size=0): entry={entry.name!r}, meta_size={meta_size}"
            )
            return False

        size_diff = abs(meta_size - entry.size)
        size_tolerance_bytes = self.size_tolerance * meta_size

        similarity = self.calculate_similarity(cleaned_meta_name, entry.name)

        # Check conditions for similarity match
        xxx_condition = meta.get("xxx", False) and size_diff <= size_tolerance_bytes
        similarity_condition = similarity >= self.similarity_threshold and size_diff <= size_tolerance_bytes

        if xxx_condition or similarity_condition:
            log.debug(
                f"[yellow]Similarity match: {entry.name} "
                f"(similarity: {similarity:.3f}, size_diff: {size_diff})"
            )
            return True

        return False

    async def find_matches(
        self, dupes: list[DupeEntry], meta: dict[str, Any], config: dict[str, Any], simple_name: bool = False
    ) -> list[DupeEntry]:
        """Find matching duplicates using all available criteria."""
        matches = []

        # Get meta size
        raw_content_size = meta.get("content_size", 0)
        meta_size = raw_content_size
        torrent_size = 0
        if not meta.get("rehash"):
            from src.utils.torrentman import extract_size_from_torrent

            torrent_size = extract_size_from_torrent(meta["base_dir"], meta["uuid"])
            if torrent_size > 0:
                meta_size = torrent_size
                meta["content_size"] = torrent_size

        tracker_name = meta.get("tracker_name", "Unknown")
        if simple_name:
            tracker_specific_name = meta.get(f"{tracker_name.lower()}_name")
        else:
            tracker_specific_name = meta.get(f"{tracker_name}_name")
        name_for_matching = tracker_specific_name or meta.get("clean_name", "")
        cleaned_meta_name = re.sub(r"[^\w\s]", "", name_for_matching.lower())

        log.debug(
            f"[cyan]find_matches: tracker={tracker_name} "
            f"content_size={raw_content_size} torrent_size={torrent_size} "
            f"meta_size={meta_size} clean_name={cleaned_meta_name!r} "
            f"dupe_count={len(dupes)}"
        )

        for entry in dupes:
            if await self.check_exact_match(meta, entry, meta_size) or await self.check_similarity_match(
                meta, entry, meta_size, cleaned_meta_name
            ):
                matches.append(entry)

        log.debug(f"[cyan]find_matches result: {len(matches)} match(es) from {len(dupes)} dupes")
        return matches


# ===============================
# Main Interface Functions
# ===============================

# Global filter instance
_dupe_filter = DupeFilter()
_dupe_matcher: DupeMatcher | None = None


def get_dupe_filter() -> DupeFilter:
    """Get the global dupe filter instance."""
    return _dupe_filter


def get_dupe_matcher(config: dict[str, Any]) -> DupeMatcher:
    """Get or create a dupe matcher with the given config."""
    global _dupe_matcher
    if _dupe_matcher is None or _dupe_matcher.config != config:
        _dupe_matcher = DupeMatcher(config)
    return _dupe_matcher


async def filter_dupes(
    dupes: dict | list[dict[str, Any]] | list[str] | str,
    meta: dict[str, Any],
    config: dict[str, Any],
    tracker_name: str,
) -> list[DupeEntry]:
    """
    Filter duplicates
    """
    return await get_dupe_filter().filter_dupes(dupes, meta, config, tracker_name)


async def match_dupes(
    dupes: list[DupeEntry], meta: dict[str, Any], config: dict[str, Any], simple_name: bool = False
) -> list[DupeEntry]:
    """
    Find matching duplicates
    """
    matcher = get_dupe_matcher(config)
    return await matcher.find_matches(dupes, meta, config, simple_name=simple_name)


async def dupe_check(
    dupes: dict | list[dict[str, Any]] | list[str],
    meta: dict,
    config: dict,
    skipped_details: list[tuple],
    path: str,
    tracker_name: str,
    skip_rule_filter: bool = False,
    tracker_simple_name: bool = False,
) -> tuple[dict, bool]:
    from rich.table import Table

    from src.progress import ProgressManager
    from src.utils.console import console

    progress = ProgressManager.get_instance()
    progress.update("Dupe Check..")

    if meta.get("skip_dupe_check", False):
        console.print("[yellow]Skipping duplicate check as requested.")
        meta["upload"] = True
        return meta, False

    if not dupes:
        console.print("[green]No dupes found")
        meta["upload"] = True
        return meta, False

    # Normalize dupes for display
    entries = DupeNormalizer.normalize(dupes)

    if not meta.get("unattended", False):
        table = Table(
            title=f"Are these dupes? ({tracker_name})",
            show_header=True,
            header_style="bold underline",
            expand=True,
            box=None,
        )
        table.add_column("Name")
        table.add_column("Size", justify="center")

        for entry in entries:
            size_gb = "N/A"
            if entry.size:
                size_gb = f"{round(entry.size / (1024**3), 2)} GB"
            table.add_row(entry.name, f"[magenta]{size_gb}[/magenta]")

        console.print(table)

    # Filter dupes first (unless skipped for music/xxx)
    if skip_rule_filter:
        filtered_entries = entries
    else:
        filtered_entries = await filter_dupes(dupes, meta, config, tracker_name)

    # If no dupes remain after filtering, allow upload
    if not filtered_entries:
        console.print("[green]No relevant dupes found after filtering")
        meta["upload"] = True
        return meta, False

    # Check for matches
    meta["tracker_name"] = tracker_name
    matches = await match_dupes(filtered_entries, meta, config, simple_name=tracker_simple_name)

    if matches:
        # Exact size matches should abort without prompting
        meta_size = meta.get("content_size", 0)
        if any(m.size and m.size == meta_size for m in matches):
            meta["upload"] = False
            return meta, True

        with progress.pause_for_interaction():
            if meta.get("unattended", False) or not Confirm.ask(" Upload anyways?"):
                meta["upload"] = False
                return meta, True

    meta["upload"] = True
    return meta, False


# ===============================
# Setup and Initialization
# ===============================


def setup_default_filter_rules():
    """Setup the default filter rules based on tracker patterns."""
    registry = get_rule_registry()

    # Add default rules
    registry.register_rule(ResolutionRule())
    registry.register_rule(HDRRule())
    registry.register_rule(SeasonEpisodeRule())
    registry.register_rule(FileMatchRule())

    # Add SD-specific rules for BHD and AITHER
    class SDResolutionRule(FilterRule):
        async def should_exclude(self, entry: DupeEntry, context: MetaContext) -> tuple[bool, str | None]:
            if context.sd == 1 and context.tracker_name in ["BHD", "AITHER"]:
                if any(res in entry.name for res in ["1080", "720", "2160"]):
                    return True, f"SD content but resolution found in: {entry.name}"
            return False, None

    registry.register_rule(SDResolutionRule(), tracker_name="BHD")
    registry.register_rule(SDResolutionRule(), tracker_name="AITHER")


# Initialize on import
setup_default_filter_rules()


def tracker_filter(
    tracker_name: str, custom_rules: list[FilterRule] | None = None, skips: list[str] | None = None
):
    registry = get_rule_registry()
    if custom_rules:
        for rule in custom_rules:
            registry.register_rule(rule, tracker_name=tracker_name)
    if skips:
        registry.register_skips(tracker_name, skips)
