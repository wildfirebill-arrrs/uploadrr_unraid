from __future__ import annotations

import re
from typing import Any, ClassVar

from src.utils.console import log


class TemplateContext:
    """Base template context with core functionality."""

    def __init__(self, meta: dict[str, Any]):
        self._meta = meta
        self._overrides = {}

    def set_overrides(self, overrides: dict[str, Any]):
        """Set tracker-specific overrides."""
        self._overrides = overrides

    def get(self, key: str, override: str = "") -> str:
        """Get value with tracker-specific overrides taking precedence."""
        if key in self._overrides and self._overrides[key] is not None:
            return str(self._overrides[key])

        # Fall back to meta
        value = self._meta.get(key, override)
        return str(value) if value is not None else ""

    def __getattr__(self, key: str) -> str:
        """Allow dot notation access with overrides."""
        return self.get(key)

    def to_dict(self) -> dict[str, str]:
        """Convert meta + overrides into a flat dict for template formatting."""
        result = {}
        keys = set(self._meta.keys()) | set(self._overrides.keys())
        for key in keys:
            result[key] = self.get(key)
        return result


class NameGenerator:
    # fmt:off
    NAMING_TEMPLATES: ClassVar[dict[str, Any]] = {
        "MOVIE": {
            "DISC": {
                "BDMV": "{title} {aka} {year} {3D} {cut} {ratio} {edition} {repack} {resolution} {region} {uhd} {source} {hdr} {video_codec} {lang} {audio}",
                "DVD": "{title} {aka} {year} {cut} {ratio} {edition} {repack} {source} {dvd_size} {lang} {audio}",
                "HDDVD": "{title} {aka} {year} {cut} {ratio} {edition} {repack} {resolution} {source} {video_codec} {lang} {audio}",
            },
            "REMUX": {
                "BluRay": "{title} {aka} {year} {3D} {cut} {ratio} {edition} {repack} {resolution} {uhd} {source} REMUX {hdr} {video_codec} {lang} {audio}",
                "HDDVD": "{title} {aka} {year} {3D} {cut} {ratio} {edition} {repack} {resolution} {uhd} {source} REMUX {hdr} {video_codec} {lang} {audio}",
                "DVD": "{title} {aka} {year} {cut} {ratio} {edition} {repack} {source} REMUX {lang} {audio}",
            },
            "ENCODE": "{title} {aka} {year} {cut} {ratio} {edition} {repack} {resolution} {uhd} {source} {lang} {audio} {hdr} {video_encode}",
            "WEBDL": "{title} {aka} {year} {cut} {ratio} {edition} {repack} {resolution} {uhd} {service} WEB-DL {lang} {audio} {hdr} {video_encode}",
            "WEBRIP": "{title} {aka} {year} {cut} {ratio} {edition} {repack} {resolution} {uhd} {service} WEBRip {lang} {audio} {hdr} {video_encode}",
            "HDTV": "{title} {aka} {year} {cut} {ratio} {edition} {repack} {resolution} {source} {lang} {audio} {video_encode}",
        },
        "TV": {
            "DISC": {
                "BDMV": "{title} {year} {aka} {season}{episode} {3D} {cut} {ratio} {edition} {repack} {resolution} {region} {uhd} {source} {hdr} {video_codec} {lang} {audio}",
                "DVD": "{title} {aka} {season}{episode} {3D} {cut} {ratio} {edition} {repack} {source} {dvd_size} {lang} {audio}",
                "HDDVD": "{title} {aka} {year} {cut} {ratio} {edition} {repack} {resolution} {source} {video_codec} {lang} {audio}",
            },
            "REMUX": {
                "BluRay": "{title} {year} {aka} {season}{episode} {episode_title} {part} {3D} {cut} {ratio} {edition} {repack} {resolution} {uhd} {source} REMUX {hdr} {video_codec} {lang} {audio}",
                "DVD": "{title} {year} {aka} {season}{episode} {episode_title} {part} {cut} {ratio} {edition} {repack} {source} REMUX {lang} {audio}",
            },
            "ENCODE": "{title} {year} {aka} {season}{episode} {episode_title} {part} {cut} {ratio} {edition} {repack} {resolution} {uhd} {source} {lang} {audio} {hdr} {video_encode}",
            "WEBDL": "{title} {year} {aka} {season}{episode} {episode_title} {part} {cut} {ratio} {edition} {repack} {resolution} {uhd} {service} WEB-DL {lang} {audio} {hdr} {video_encode}",
            "WEBRIP": "{title} {year} {aka} {season}{episode} {episode_title} {part} {cut} {ratio} {edition} {repack} {resolution} {uhd} {service} WEBRip {lang} {audio} {hdr} {video_encode}",
            "HDTV": "{title} {year} {aka} {season}{episode} {episode_title} {part} {cut} {ratio} {edition} {repack} {resolution} {source} {lang} {audio} {video_encode}",
        },
        "MUSIC": {
            "lossless": "{artist} - {album} ({year}) {edition} [{service} {source} {type} {bit_depth} {sampling_rate}]",
            "lossy": "{artist} - {album} ({year}) {edition} [{service} {source} {type} {bitrate}]",
        },
        "XXX": {
            "default": "{title} ({year}) {resolution} {source} {audio} {video_encode}",
        },
    }
    # fmt: on

    @staticmethod
    def _xxx_network(meta: dict) -> str:
        """Get XXX network from porndb data."""
        porndb = meta.get("porndb", {})
        parent = porndb.get("parent", "")
        network = porndb.get("network", "")
        site = porndb.get("site", "")
        candidates = [(parent, parent.lower()), (network, network.lower()), (site, site.lower())]

        parts = []
        for i, (val, lval) in enumerate(candidates):
            if not val:
                continue
            redundant = False
            for j in range(i + 1, len(candidates)):
                if candidates[j][1] and lval in candidates[j][1]:
                    redundant = True
                    break
            if redundant:
                continue
            if "mindgeek" in lval or "aylo" in lval:
                continue
            if lval not in [p.lower() for p in parts]:
                styled_val = NameGenerator._stylize(val)
                parts.append(styled_val)

        return "[" + "/".join(parts) + "] " if parts else ""

    @staticmethod
    def _stylize(val: str) -> str:
        """Stylize the value with special cases and conditional title casing."""
        if not val:
            return ""
        specials = {
            "clips4sale": "Clips4Sale",
            "faphouse": "FapHouse",
            "manyvids": "ManyVids",
            "onlyfans": "OnlyFans",
        }
        subparts = val.split(":") if ":" in val else [val]
        styled_subparts = []
        for p in subparts:
            if not p:
                continue
            sl = p.lower()
            if sl in specials:
                styled_subparts.append(specials[sl])
            else:
                if any(c.isupper() for c in p[1:]):
                    styled_subparts.append(p)
                else:
                    styled_subparts.append(p.title())
        return ":".join(styled_subparts)

    @staticmethod
    def get_template(
        category: str,
        media_type: str,
        source: str | None = None,
        is_disc: str | None = None,
        custom_templates: dict | None = None,
        tracker: str | None = None,
        is_lossy: bool | None = None,
    ) -> str:
        """Retrieve the appropriate naming template."""
        # Try custom templates first - handle flat structure
        if custom_templates:
            # Unwrap tracker namespacing if present: {"TRACKER": {...}} -> {...}
            if tracker and tracker in custom_templates and isinstance(custom_templates[tracker], dict):
                custom_templates = custom_templates[tracker]

            # Support flat MUSIC templates (no wrapper key): {"lossless": ..., "lossy": ...}
            if category == "MUSIC" and any(k in custom_templates for k in ("lossless", "lossy")):
                key = "lossy" if is_lossy else "lossless"
                template = custom_templates.get(key)
                if template:
                    return template

            if category in custom_templates:
                cat_templates = custom_templates[category]

                # If it's a flat template (set or string), return it directly
                if isinstance(cat_templates, (set, str)):
                    if isinstance(cat_templates, set):
                        # Extract the template string from the set
                        return next(iter(cat_templates)) if cat_templates else ""
                    return cat_templates

                # If it's a dict (hierarchical structure), use the original logic
                if category == "MUSIC":
                    key = "lossy" if is_lossy else "lossless"
                    template = cat_templates.get(key)
                    if template:
                        return template
                elif media_type in cat_templates:
                    template_or_dict = cat_templates[media_type]
                    if isinstance(template_or_dict, dict):
                        if media_type == "DISC" and is_disc:
                            return template_or_dict.get(is_disc, "")
                        elif media_type == "REMUX" and source:
                            source_key = "BluRay"
                            if "DVD" in source.upper():
                                source_key = "DVD"
                            elif "HDDVD" in source.upper():
                                source_key = "HDDVD"
                            return template_or_dict.get(source_key, "")
                        return next(iter(template_or_dict.values())) if template_or_dict else ""
                    return template_or_dict

        # Fall back to built-in templates (original logic)
        templates = NameGenerator.NAMING_TEMPLATES.get(category, {})
        if category == "MUSIC":
            key = "lossy" if is_lossy else "lossless"
            return templates.get(key, "")
        if category == "XXX":
            return templates.get(media_type, templates["default"])
        if media_type == "DISC":
            return templates[media_type].get(is_disc, "")
        if media_type == "REMUX":
            source_key = "BluRay"
            if source:
                if "DVD" in source.upper():
                    source_key = "DVD"
                elif "HDDVD" in source.upper():
                    source_key = "HDDVD"
            return templates[media_type].get(source_key, "")
        return templates.get(media_type, "")

    @staticmethod
    def clean_filename(name: str) -> str:
        """Clean filename of invalid characters."""
        invalid = r'<>:"/\|?*'
        for char in invalid:
            name = name.replace(char, "-")
        return " ".join(name.split())

    @staticmethod
    def _preprocess_meta_for_context(meta: dict) -> dict:
        """Preprocess meta data for context usage."""
        processed = meta.copy()

        # Handle title variations
        if processed.get("use_original_title", False):
            processed["title"] = processed.get("original_title", "")

        # Handle resolution
        if processed.get("resolution") == "OTHER":
            processed["resolution"] = ""

        # Handle language defaults
        if processed.get("lang", "ENGLISH") == "ENGLISH":
            processed["lang"] = ""

        # Handle no_aka and no_dub flags
        if processed.get("no_aka"):
            processed["aka"] = ""
        if processed.get("no_dub"):
            processed["lang"] = processed.get("lang", "").replace("DUBBED", "")

        # Prefer WEB for MUSIC only when source is still empty
        if processed.get("category") == "MUSIC" and processed.get("service") and not processed.get("source"):
            processed["source"] = "WEB"

        # Append "bit" suffix for MUSIC templates that use {bit_depth}bit
        if processed.get("category") == "MUSIC":
            bit_depth = processed.get("bit_depth", "")
            processed["bit_depth"] = f"{bit_depth}bit" if bit_depth else ""

        # Normalize boolean flag fields to their string representation for templates
        if isinstance(processed.get("uhd"), bool):
            processed["uhd"] = "UHD" if processed["uhd"] else ""

        return processed

    @staticmethod
    def _clean_formatted_name(name: str) -> str:
        name = re.sub(r"\[\s+", "[", name)
        name = re.sub(r"\s+\]", "]", name)
        name = re.sub(r"\[\s*\]", "", name)
        name = re.sub(r"\(\s+(\d{4})\)", r"(\1)", name)
        name = re.sub(r"\(\s*\)", "", name)
        return " ".join(name.split())

    async def get_name(self, meta: dict) -> tuple[str, str, str, list[str]]:
        """Generate the main name using context."""
        processed_meta = self._preprocess_meta_for_context(meta)
        context = TemplateContext(processed_meta)

        category, media_type = meta.get("category", ""), meta.get("type", "")
        source, is_disc = meta.get("source", ""), meta.get("is_disc", "")
        is_lossy = meta.get("is_lossy")

        template = self.get_template(category, media_type, source, is_disc, is_lossy=is_lossy)

        if not template:
            name_notag = "NO_TEMPLATE_FOUND"
        else:
            try:
                formatted_dict = context.to_dict()
                name_notag = template.format(**formatted_dict)
                name_notag = self._clean_formatted_name(name_notag)
            except (KeyError, ValueError) as e:
                tmpl_keys = set(re.findall(r"\{(\w+)\}", template))
                missing = sorted(tmpl_keys - set(formatted_dict.keys()))
                log.warning(f"Template formatting error: missing {missing} ({e})")
                name_notag = "TEMPLATE_ERROR"

        if category == "XXX":
            sites = self._xxx_network(processed_meta)
            name_notag = sites + name_notag
            tag = meta.get("tag")
            date = meta.get("porndb", {}).get("date")
            if (
                not isinstance(tag, str)
                or not tag.strip()
                or tag[1:].replace(" ", "").lower() in sites.lower()
            ):
                meta["tag"] = "-NOGRP"
            if meta.get("xscene") and date:
                name_notag = name_notag.replace(f"{meta['year']}", f"{date}")

        tag = "" if meta.get("no_tag") or not meta.get("tag") else meta["tag"]
        name = name_notag + tag if tag and not name_notag.endswith(tag) else name_notag
        clean_name = self.clean_filename(name)

        # Store in meta
        meta.update({"name_notag": name_notag, "name": name, "clean_name": clean_name})

        return name_notag, name, clean_name, []

    async def generate_tracker_name(
        self, meta: dict, tracker: str, custom_templates: dict | None = None, overrides: dict | None = None
    ) -> str:
        """Generate tracker-specific name with tracker-specific overrides."""
        processed_meta = self._preprocess_meta_for_context(meta)
        context = TemplateContext(processed_meta)

        # Set tracker-specific overrides
        if overrides:
            context.set_overrides(overrides)

        category, media_type = meta.get("category", ""), meta.get("type", "")
        source, is_disc = meta.get("source", ""), meta.get("is_disc", "")

        template = self.get_template(
            category, media_type, source, is_disc, custom_templates, tracker, meta.get("is_lossy")
        )

        if not template:
            tracker_name_notag = "NO_TEMPLATE_FOUND"
        else:
            try:
                formatted_dict = context.to_dict()
                tracker_name_notag = template.format(**formatted_dict)
                tracker_name_notag = self._clean_formatted_name(tracker_name_notag)
            except (KeyError, ValueError) as e:
                tmpl_keys = set(re.findall(r"\{(\w+)\}", template))
                missing = sorted(tmpl_keys - set(formatted_dict.keys()))
                log.warning(f"Tracker {tracker} template formatting error: missing {missing} ({e})")
                tracker_name_notag = "TEMPLATE_ERROR"

        # Handle tag inclusion
        tag = "" if meta.get("no_tag") or not meta.get("tag") else meta["tag"]
        tracker_name = tracker_name_notag if "{tag}" in template else tracker_name_notag + tag
        return tracker_name
