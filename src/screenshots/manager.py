from __future__ import annotations

import asyncio
import copy
import importlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union
from typing import Callable as TypingCallable

import psutil

from src.screenshots.generator import ScreenshotGenerator
from src.screenshots.rehoster import RehostManager
from src.screenshots.resizer import MAX_COVER_SIZE, resize_cover_image
from src.screenshots.up2host import ScreenshotUploader
from src.utils.console import log

ContactSheetRequirement = Union[dict[str, int], TypingCallable[[float], tuple[int, int]], None]
ContactSheetRequirements = Union[ContactSheetRequirement, list[ContactSheetRequirement]]


@dataclass(frozen=True)
class ExpectedContactSheet:
    local_file: str
    path: Path
    columns: int
    rows: int


class ScreenshotManager:
    def __init__(self, config: dict[str, Any], base_dir: str | Path):
        self.config = config
        self.base_dir = Path(base_dir)
        self.generator = ScreenshotGenerator(config, self.base_dir)
        self.uploader = ScreenshotUploader(config, self.base_dir)
        self.rehoster = RehostManager(config, self.base_dir, self.uploader, self.generator)

    async def handle_screenshots(
        self, meta: dict[str, Any], tracker_whitelists: dict[str, list[str]], task_widget: Any | None = None
    ) -> dict[str, Any]:
        log.debug("in handle_screenshots")
        try:
            if meta.get("is_music"):
                return await self._handle_music(meta, tracker_whitelists)
            return await self._handle_video_content(meta, tracker_whitelists, task_widget)
        except Exception as e:
            log.exception(f"Error in handle_screenshots: {e}")
            return meta
        finally:
            await self._cleanup(meta)
            self._reset_terminal()

    def _get_tracker_contact_sheet_requirements(
        self, tracker_names: list[str]
    ) -> list[ContactSheetRequirement]:
        requirements: list[ContactSheetRequirement] = []
        for tracker in tracker_names:
            try:
                module = importlib.import_module(f"src.trackers.{tracker}")
                tracker_class = getattr(module, tracker, None)
                if tracker_class and hasattr(tracker_class, "get_contact_sheet_requirement"):
                    instance = tracker_class(config=self.config)
                    req = instance.get_contact_sheet_requirement()
                    if req:
                        requirements.append(req)
            except (ImportError, AttributeError, KeyError, TypeError) as exc:
                log.warning(
                    f"[yellow]Could not load contact sheet requirement for tracker {tracker}: {exc}[/yellow]"
                )
                continue
        return requirements

    @staticmethod
    def _resolve_contact_sheet_requirement(
        requirement: ContactSheetRequirements, length: float
    ) -> dict[str, int] | None:
        if isinstance(requirement, list):
            dimensions = [
                ScreenshotManager._resolve_contact_sheet_requirement(item, length)
                for item in requirement
                if item
            ]
            dimensions = [d for d in dimensions if d]
            if not dimensions:
                return None
            return {
                "columns": max(d["columns"] for d in dimensions),
                "rows": max(d["rows"] for d in dimensions),
            }
        if not requirement:
            return None
        if callable(requirement):
            columns, rows = requirement(length)
            return {"columns": columns, "rows": rows}
        if isinstance(requirement, dict):
            return requirement
        return None

    def _get_contact_sheet_dimensions(
        self, requirement: ContactSheetRequirements, length: float
    ) -> dict[str, int]:
        resolved = self._resolve_contact_sheet_requirement(requirement, length)
        if resolved:
            return resolved
        columns, rows = self.generator._get_rows_columns(length)
        return {"columns": columns, "rows": rows}

    async def _handle_music(
        self, meta: dict[str, Any], tracker_whitelists: dict[str, list[str]]
    ) -> dict[str, Any]:
        log.debug("Handling music content")
        meta.setdefault("image_list", {"main": [], "episodes": {}, "contact_sheets": {}})
        image_list = meta["image_list"]
        needs_upload = False

        if meta.get("user_cover") and not self._is_valid_url(meta.get("album_cover")):
            needs_upload = True
        if meta.get("back_cover") and not self._is_valid_url(meta.get("album_back")):
            needs_upload = True
        if meta.get("user_proof") and not self._is_valid_url(meta.get("user_proof")):
            needs_upload = True

        if needs_upload and not meta.get("skip_imghost_upload", False):
            log.debug(">>> MUSIC UPLOAD PROCESS")
            music_images = await self._prepare_music_images(meta)
            if music_images:
                meta["image_list"] = await self.uploader.upload_screenshots(
                    meta, music_images, RehostManager._get_viable_hosts(self.config, meta, tracker_whitelists)
                )
                for img in meta["image_list"]["main"]:
                    img_url = img.get("raw_url") or img.get("img_url") or img.get("web_url")
                    if img.get("local_file", "") == meta.get("user_cover"):
                        meta["album_cover"] = img_url
                    elif img.get("local_file", "") == meta.get("back_cover"):
                        meta["album_back"] = img_url
                    elif img.get("local_file", "") == meta.get("user_proof"):
                        if "proof_url" not in meta:
                            meta["proof_url"] = img_url

        if tracker_whitelists and needs_upload:
            whitelist_hosts = {host for wl in tracker_whitelists.values() for host in wl}

            def non_compliant(section: list) -> bool:
                return any(
                    not RehostManager._extract_host(
                        img.get("img_url") or img.get("raw_url") or img.get("web_url", ""), whitelist_hosts
                    )
                    for img in section
                    if isinstance(img, dict) and any(img.get(k) for k in ("img_url", "raw_url", "web_url"))
                )

            if non_compliant(image_list["main"]):
                log.debug(">>> REHOSTER CALLED FOR MUSIC")
                await self.rehoster.ensure_compliance(meta, tracker_whitelists)

        return meta

    async def _handle_video_content(
        self, meta: dict[str, Any], tracker_whitelists: dict[str, list[str]], task_widget: Any | None = None
    ) -> dict[str, Any]:
        meta.setdefault("image_list", {"main": [], "episodes": {}, "contact_sheets": {}})
        image_list = meta["image_list"]

        if isinstance(image_list, list):  # backwards compatibility
            image_list = {"main": image_list, "episodes": {}}
            meta["image_list"] = image_list

        normalized_whitelists = {
            tracker: [] if wl == ["selfhost"] else wl for tracker, wl in tracker_whitelists.items()
        }
        if all(wl == ["selfhost"] for wl in tracker_whitelists.values()) and not meta.get(
            "skip_imghost_upload"
        ):
            log.debug("All trackers are self-hosting; skipping external image-host upload")
            meta["skip_imghost_upload"] = True

        screens_main = meta.get("screens", self.config["DEFAULT"].get("screens", 3))
        tmp_dir = self.base_dir / "tmp" / meta["uuid"]
        contact_sheet_requirements = self._get_tracker_contact_sheet_requirements(meta.get("trackers", []))

        need_generation = False
        need_upload = False
        cs_needs_generation = False

        # For XXX content: check contact sheet requirements BEFORE main screenshots
        contact_cfg = self.config.get("ADULT", {}).get("contact_sheet", {})
        if meta.get("xxx") and not contact_cfg.get("skip", False):
            contact_section = image_list.get("contact_sheets", [])
            if isinstance(contact_section, dict):
                contact_section = list(contact_section.values())

            expected_sheets = await self._get_expected_contact_sheet_paths(
                meta, contact_sheet_requirements, tmp_dir
            )
            uploaded_sheets = {
                entry["local_file"]: entry
                for entry in contact_section
                if isinstance(entry, dict)
                and "local_file" in entry
                and any(self._is_valid_url(entry.get(k)) for k in ("img_url", "raw_url", "web_url"))
            }

            for sheet in expected_sheets:
                log.debug(
                    f"Checking contact sheet: expected={sheet.local_file} ({sheet.columns}x{sheet.rows}), "
                    f"file_exists={sheet.path.exists()}, file_size={sheet.path.stat().st_size if sheet.path.exists() else 0}"
                )
                log.debug(
                    f"  Contact section entries: {len(contact_section)}, "
                    f"uploaded_sheets keys: {list(uploaded_sheets.keys())}"
                )

                if sheet.local_file in uploaded_sheets:
                    log.debug(f"[CS] Already has valid URL for {sheet.local_file}")
                    continue

                def _matches_sheet(entry: dict, sheet: ExpectedContactSheet) -> bool:
                    return entry.get("local_file") == sheet.local_file or (
                        entry.get("columns") == sheet.columns and entry.get("rows") == sheet.rows
                    )

                has_valid_url_for_expected = any(
                    isinstance(entry, dict)
                    and any(self._is_valid_url(entry.get(k)) for k in ("img_url", "raw_url", "web_url"))
                    and _matches_sheet(entry, sheet)
                    for entry in contact_section
                )
                if has_valid_url_for_expected:
                    log.debug(
                        f"[CS] Skipping generation: valid URL exists (matching {sheet.local_file} or {sheet.columns}x{sheet.rows})"
                    )
                    continue

                # Need to generate/upload contact sheet
                if sheet.path.exists() and sheet.path.stat().st_size > 1000:
                    log.debug("[CS] File exists on disk but no valid URL — need upload only")
                    need_upload = True
                else:
                    log.debug("[CS] Missing on disk with no URL — triggering regeneration")
                    need_generation = True
                    need_upload = True
                    cs_needs_generation = True

        if self._missing_urls(image_list["main"]):
            need_upload = True
            existing_count = len(list(tmp_dir.glob(f"{meta['filename']}-*.png")))
            if existing_count < screens_main:
                need_generation = True
            elif meta.get("xxx") and not need_generation:
                log.debug("XXX content with satisfied contact sheet - skipping screenshot regeneration")
            else:
                need_generation = True
        else:
            existing_files = list(tmp_dir.glob(f"{meta['filename']}-*.png"))
            uploaded_count = sum(
                1
                for entry in image_list["main"]
                if isinstance(entry, dict)
                and "local_file" in entry
                and any(self._is_valid_url(entry.get(k)) for k in ("img_url", "raw_url", "web_url"))
            )
            if uploaded_count < min(len(existing_files), screens_main):
                log.debug(
                    f"Main screenshot count mismatch: {uploaded_count} uploaded vs {min(len(existing_files), screens_main)} expected"
                )
                need_upload = True

        if meta.get("tv_pack") == 1:
            per_episode = self.config["SCREENS"].get("per_episode", 3)
            for ep_path in meta.get("filelist", []):
                if not self.generator._is_video_file(ep_path):
                    continue
                ep_name = self.generator._get_episode_identifier(ep_path)
                ep_screens = image_list["episodes"].get(ep_name, [])

                if self._missing_urls(ep_screens):
                    need_upload = True
                    if len(list(tmp_dir.glob(f"{meta['filename']}-{ep_name}-*.png"))) < per_episode:
                        need_generation = True
                else:
                    existing_files = list(tmp_dir.glob(f"{meta['filename']}-{ep_name}-*.png"))
                    uploaded_count = sum(
                        1
                        for entry in ep_screens
                        if isinstance(entry, dict)
                        and "local_file" in entry
                        and any(self._is_valid_url(entry.get(k)) for k in ("img_url", "raw_url", "web_url"))
                    )
                    if uploaded_count < min(len(existing_files), per_episode):
                        log.debug(
                            f"Episode {ep_name} screenshot count mismatch: {uploaded_count} uploaded vs {min(len(existing_files), per_episode)} expected"
                        )
                        need_upload = True

        if meta.get("xxx"):
            log.debug("Handling XXX content: checking user_cover and user_banner")
            xxx_images = {"main": [], "episodes": {}, "contact_sheets": {}}

            def needs_xxx_upload(field_key: str, current_url_key: str) -> bool:
                local_path = meta.get(field_key)
                current_url = meta.get(current_url_key)
                if not local_path or not Path(local_path).exists():
                    return False
                if self._is_valid_url(current_url):
                    if tracker_whitelists:
                        whitelist_hosts = {host for wl in tracker_whitelists.values() for host in wl}
                        if RehostManager._extract_host(current_url, whitelist_hosts):
                            return False
                    else:
                        return False
                return True

            cover_path = tmp_dir / "cover.jpg"
            if "LDU" in meta["trackers"] and not meta.get("cover_url") and cover_path.exists():
                xxx_images["main"].append({"local_file": str(cover_path.name), "type": "user_cover"})

            if needs_xxx_upload("user_cover", "cover_url"):
                xxx_images["main"].append({"local_file": meta["user_cover"], "type": "user_cover"})
            if needs_xxx_upload("user_banner", "banner_url"):
                xxx_images["main"].append({"local_file": meta["user_banner"], "type": "user_banner"})
            if needs_xxx_upload("trailer", "trailer_url"):
                xxx_images.setdefault("trailer", []).append(
                    {"local_file": str(Path(meta["trailer"]).name), "type": "trailer"}
                )

            if (xxx_images["main"] or xxx_images.get("trailer")) and not meta.get(
                "skip_imghost_upload", False
            ):
                log.debug(">>> UPLOADING XXX USER IMAGES")
                viable_hosts = RehostManager._get_viable_hosts(self.config, meta, tracker_whitelists)
                uploaded = await self.uploader.upload_screenshots(meta, xxx_images, viable_hosts)

                image_list["main"].extend(uploaded.get("main", []))
                for img in uploaded.get("main", []):
                    local_file = img.get("local_file", "")
                    img_url = img.get("raw_url") or img.get("img_url") or img.get("web_url")
                    if local_file == meta.get("user_cover") or local_file == str(cover_path.name):
                        meta["cover_url"] = img_url
                    elif local_file == meta.get("user_banner"):
                        meta["banner_url"] = img_url

                image_list.setdefault("trailer", []).extend(uploaded.get("trailer", []))
                for img in uploaded.get("trailer", []):
                    if img.get("local_file") == str(Path(meta.get("trailer", "")).name):
                        meta["trailer_url"] = img.get("raw_url") or img.get("img_url") or img.get("web_url")

                seen = set()
                deduped_main = []
                for img in image_list["main"]:
                    key = img.get("local_file") or img.get("raw_url")
                    if key and key not in seen:
                        seen.add(key)
                        deduped_main.append(img)
                image_list["main"] = deduped_main

        log.debug(f"Need Generation = {need_generation}\nNeed Upload = {need_upload}")

        if need_upload and not need_generation:
            log.debug("Populating missing local files in image_list without overwriting valid uploads.")

            existing_files = sorted(tmp_dir.glob(f"{meta['filename']}-*.png"))[:screens_main]
            valid_uploads = {
                entry["local_file"]: entry
                for entry in image_list["main"]
                if isinstance(entry, dict)
                and "local_file" in entry
                and {"img_url", "raw_url", "web_url"}.intersection(entry.keys())
            }
            new_main = list(valid_uploads.values())
            seen = set(valid_uploads)
            for f in existing_files:
                if f.name not in seen:
                    new_main.append({"local_file": f.name, "ss_time": 0})
                    seen.add(f.name)
            image_list["main"] = new_main[:screens_main]

            if meta.get("tv_pack") == 1:
                per_episode = self.config["SCREENS"].get("per_episode", 3)
                for ep_path in meta.get("filelist", []):
                    if not self.generator._is_video_file(ep_path):
                        continue
                    ep_name = self.generator._get_episode_identifier(ep_path)
                    ep_screens = image_list["episodes"].get(ep_name, [])
                    existing_files = sorted(tmp_dir.glob(f"{meta['filename']}-{ep_name}-*.png"))[:per_episode]
                    valid_uploads = {
                        entry["local_file"]: entry
                        for entry in ep_screens
                        if isinstance(entry, dict)
                        and "local_file" in entry
                        and {"img_url", "raw_url", "web_url"}.intersection(entry.keys())
                    }
                    new_ep = list(valid_uploads.values())
                    seen = set(valid_uploads)
                    for f in existing_files:
                        if f.name not in seen:
                            new_ep.append({"local_file": f.name, "ss_time": 0})
                            seen.add(f.name)
                    image_list["episodes"][ep_name] = new_ep[:per_episode]

            if meta.get("xxx") and not contact_cfg.get("skip", False):
                contact_section = image_list.get("contact_sheets", [])
                if isinstance(contact_section, dict):
                    contact_section = list(contact_section.values())

                expected_sheets = await self._get_expected_contact_sheet_paths(
                    meta, contact_sheet_requirements, tmp_dir
                )

                def _has_url(entry: Any) -> bool:
                    return isinstance(entry, dict) and any(
                        self._is_valid_url(entry.get(k)) for k in ("img_url", "raw_url", "web_url")
                    )

                valid_uploads = {
                    e["local_file"]: e
                    for e in contact_section
                    if isinstance(e, dict) and "local_file" in e and _has_url(e)
                }
                url_only = [
                    e
                    for e in contact_section
                    if isinstance(e, dict) and "local_file" not in e and _has_url(e)
                ]

                populated = list(valid_uploads.values())
                seen = set(valid_uploads)
                for sheet in expected_sheets:
                    if sheet.local_file in seen:
                        continue

                    url_matches_dims = any(
                        isinstance(e, dict)
                        and e.get("columns") == sheet.columns
                        and e.get("rows") == sheet.rows
                        for e in url_only
                    )
                    if url_matches_dims:
                        log.debug(
                            f"Contact sheet {sheet.local_file}: found URL-only entry with matching dimensions"
                        )
                        continue

                    if sheet.path.exists() and sheet.path.stat().st_size > 1000:
                        populated.append(
                            {"local_file": sheet.local_file, "columns": sheet.columns, "rows": sheet.rows}
                        )
                        seen.add(sheet.local_file)

                image_list["contact_sheets"] = populated + url_only

        if need_generation:
            log.debug(">>> GENERATING SCREENSHOTS")
            old_image_list = copy.deepcopy(image_list)

            cs_kwargs: dict[str, Any] = {}
            if cs_needs_generation and contact_sheet_requirements:
                cs_kwargs["contact_sheet_dimensions"] = contact_sheet_requirements

            meta["image_list"] = await self.generator.generate_screenshots(
                meta, meta["filename"], meta["uuid"], **cs_kwargs
            )
            image_list = meta["image_list"]

            def _restore_urls(old_entries: list[Any], new_entries: list[Any]) -> list[Any]:
                url_by_file = {
                    e["local_file"]: e
                    for e in old_entries
                    if isinstance(e, dict)
                    and e.get("local_file")
                    and any(self._is_valid_url(e.get(k)) for k in ("img_url", "raw_url", "web_url"))
                }
                merged = []
                for entry in new_entries:
                    if (
                        isinstance(entry, dict)
                        and entry.get("local_file")
                        and entry["local_file"] in url_by_file
                    ):
                        merged.append(entry | url_by_file[entry["local_file"]])
                    else:
                        merged.append(entry if isinstance(entry, dict) else {"local_file": entry})
                return merged

            image_list["main"] = _restore_urls(old_image_list.get("main", []), image_list.get("main", []))
            for ep_name in image_list.get("episodes", {}):
                image_list["episodes"][ep_name] = _restore_urls(
                    old_image_list.get("episodes", {}).get(ep_name, []),
                    image_list.get("episodes", {}).get(ep_name, []),
                )
            image_list["contact_sheets"] = _restore_urls(
                old_image_list.get("contact_sheets", []), image_list.get("contact_sheets", [])
            )
            image_list["trailer"] = _restore_urls(
                old_image_list.get("trailer", []), image_list.get("trailer", [])
            )
            meta["image_list"] = image_list

            def _has_url(entry: dict) -> bool:
                return any(self._is_valid_url(entry.get(k)) for k in ("img_url", "raw_url", "web_url"))

            need_upload = (
                any(
                    not _has_url(e)
                    for section in (image_list.get("main", []), image_list.get("trailer", []))
                    for e in section
                    if isinstance(e, dict)
                )
                or any(
                    not _has_url(e)
                    for ep in image_list.get("episodes", {}).values()
                    for e in ep
                    if isinstance(e, dict)
                )
                or any(not _has_url(e) for e in image_list.get("contact_sheets", []) if isinstance(e, dict))
            )

        if need_upload and not meta.get("skip_imghost_upload", False):
            log.debug(">>> UPLOADER CALLED")

            def _needs_upload(entry: dict) -> bool:
                return not any(self._is_valid_url(entry.get(k)) for k in ("img_url", "raw_url", "web_url"))

            upload_list: dict = {"main": [], "episodes": {}, "contact_sheets": [], "trailer": []}
            for entry in image_list["main"]:
                if isinstance(entry, dict) and _needs_upload(entry):
                    upload_list["main"].append(entry)
            for ep_name, ep_screens in image_list["episodes"].items():
                upload_list["episodes"][ep_name] = [
                    e for e in ep_screens if isinstance(e, dict) and _needs_upload(e)
                ]
            for entry in image_list.get("contact_sheets", []):
                if isinstance(entry, dict) and _needs_upload(entry):
                    upload_list["contact_sheets"].append(entry)
            for entry in image_list.get("trailer", []):
                if isinstance(entry, dict) and _needs_upload(entry):
                    upload_list["trailer"].append(entry)

            uploaded = await self.uploader.upload_screenshots(
                meta, upload_list, RehostManager._get_viable_hosts(self.config, meta, tracker_whitelists)
            )

            def _merge(section_name: str, full: list, fresh: list) -> list:
                by_file = {e["local_file"]: e for e in fresh if isinstance(e, dict) and "local_file" in e}
                merged = []
                for entry in full:
                    lf = entry.get("local_file") if isinstance(entry, dict) else None
                    if lf and lf in by_file:
                        merged.append(by_file[lf])
                    else:
                        merged.append(entry)
                return merged

            image_list["main"] = _merge("main", image_list["main"], uploaded.get("main", []))
            for ep_name in image_list["episodes"]:
                image_list["episodes"][ep_name] = _merge(
                    ep_name, image_list["episodes"][ep_name], uploaded.get("episodes", {}).get(ep_name, [])
                )
            image_list["contact_sheets"] = _merge(
                "contact_sheets", image_list.get("contact_sheets", []), uploaded.get("contact_sheets", [])
            )
            image_list["trailer"] = _merge(
                "trailer", image_list.get("trailer", []), uploaded.get("trailer", [])
            )

            for img in image_list.get("trailer", []):
                if img.get("local_file") == str(Path(meta.get("trailer", "")).name):
                    meta["trailer_url"] = img.get("raw_url") or img.get("img_url") or img.get("web_url")
            meta["image_list"] = image_list

        if tracker_whitelists:
            whitelist_hosts = {host for wl in tracker_whitelists.values() for host in wl}

            def non_compliant_section(section: list | dict) -> bool:
                return any(
                    not RehostManager._extract_host(
                        img.get("img_url") or img.get("raw_url") or img.get("web_url", ""), whitelist_hosts
                    )
                    for img in (section if isinstance(section, list) else section.values())
                    if isinstance(img, dict) and any(img.get(k) for k in ("img_url", "raw_url", "web_url"))
                )

            screens_non_compliant = non_compliant_section(image_list["main"]) or any(
                non_compliant_section(lst) for lst in image_list["episodes"].values()
            )

            urls_to_check = [meta.get("cover_url"), meta.get("banner_url"), meta.get("trailer_url")]
            urls_non_compliant = any(
                not RehostManager._extract_host(url, whitelist_hosts)
                for url in urls_to_check
                if self._is_valid_url(url)
            )

            if screens_non_compliant or urls_non_compliant:
                log.debug(">>> REHOSTER CALLED")
                await self.rehoster.ensure_compliance(meta, normalized_whitelists)

        return meta

    async def _get_expected_contact_sheet_paths(
        self, meta: dict[str, Any], contact_sheet_requirements: ContactSheetRequirements, tmp_dir: Path
    ) -> list[ExpectedContactSheet]:
        contact_cfg = self.config.get("ADULT", {}).get("contact_sheet", {})
        expected: list[ExpectedContactSheet] = []

        if meta.get("xmovie") or meta.get("xpack"):
            video_files = self.generator.build_adult_video_files(meta)
            max_sheets = contact_cfg.get("max", 1) if not meta.get("xmovie") else len(video_files)
            num_sheets = min(len(video_files), max_sheets)
            for i in range(1, num_sheets + 1):
                vf = video_files[i - 1]
                probe = await self.generator._ffprobe(vf, "-show_format")
                length = float(probe["format"]["duration"])
                dims = self._get_contact_sheet_dimensions(contact_sheet_requirements, length)
                name = f"contact_sheet_{dims['columns']}x{dims['rows']}_{i}.png"
                expected.append(ExpectedContactSheet(name, tmp_dir / name, dims["columns"], dims["rows"]))
        else:
            length = await self.generator._get_main_video_length(meta)
            dims = self._get_contact_sheet_dimensions(contact_sheet_requirements, length)
            name = f"contact_sheet_{dims['columns']}x{dims['rows']}.png"
            expected.append(ExpectedContactSheet(name, tmp_dir / name, dims["columns"], dims["rows"]))

        return expected

    async def _prepare_music_images(self, meta: dict[str, Any]) -> dict[str, Any]:
        music_images = {"main": [], "episodes": {}, "contact_sheets": []}
        tmp_dir = self.base_dir / "tmp" / meta["uuid"]

        if (
            meta.get("user_cover")
            and Path(meta["user_cover"]).exists()
            and not self._is_valid_url(meta.get("album_cover"))
        ):
            cover_path = Path(meta["user_cover"])
            try:
                needs_resize = cover_path.stat().st_size > MAX_COVER_SIZE
            except OSError:
                needs_resize = False
            if needs_resize:
                resized = await asyncio.to_thread(resize_cover_image, cover_path, tmp_dir)
                music_images["main"].append({"local_file": str(resized), "type": "front_cover"})
            else:
                music_images["main"].append({"local_file": meta["user_cover"], "type": "front_cover"})

        if (
            meta.get("back_cover")
            and Path(meta["back_cover"]).exists()
            and not self._is_valid_url(meta.get("album_back"))
        ):
            back_path = Path(meta["back_cover"])
            try:
                needs_resize = back_path.stat().st_size > MAX_COVER_SIZE
            except OSError:
                needs_resize = False
            if needs_resize:
                resized = await asyncio.to_thread(resize_cover_image, back_path, tmp_dir)
                music_images["main"].append({"local_file": str(resized), "type": "back_cover"})
            else:
                music_images["main"].append({"local_file": meta["back_cover"], "type": "back_cover"})

        if meta.get("user_proof") and Path(meta["user_proof"]).exists():
            proof_already_uploaded = any(
                isinstance(img, dict)
                and img.get("local_file", "").startswith("proof")
                and {"img_url", "raw_url", "web_url"}.intersection(img.keys())
                for img in meta.get("image_list", {}).get("main", [])
            )
            if not proof_already_uploaded:
                music_images["main"].append({"local_file": meta["user_proof"], "type": "proof"})

        return music_images

    def _is_valid_url(self, url: str | None) -> bool:
        if not url or not isinstance(url, str):
            return False
        return url.startswith(("http://", "https://"))

    def _missing_urls(self, section: Any) -> bool:
        if not section:
            return True

        def process_item(item: Any) -> bool:
            if isinstance(item, str):
                return True
            if isinstance(item, dict):
                return not any(self._is_valid_url(item.get(k)) for k in ("img_url", "raw_url", "web_url"))
            return False

        if isinstance(section, list):
            return any(process_item(item) for item in section)
        elif isinstance(section, dict):
            if len(section) == 1 and "local_file" in section:
                return True
            return any(process_item(value) for value in section.values())
        return False

    async def _cleanup(self, meta: dict[str, Any]) -> None:
        if self.config["SCREENS"].get("cleanup_screens", False):
            tmp_dir = self.base_dir / "tmp" / meta["uuid"]
            for f in tmp_dir.glob("*.png"):
                f.unlink(missing_ok=True)

        current_process = psutil.Process()
        for child in current_process.children(recursive=True):
            try:
                if "ffmpeg" in child.name().lower():
                    child.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    def _reset_terminal(self) -> None:
        if os.name == "posix":
            try:
                if not sys.stderr.closed:
                    sys.stderr.flush()
                if hasattr(sys.stdin, "isatty") and sys.stdin.isatty() and not sys.stdin.closed:
                    subprocess.run(["stty", "sane"], check=False, capture_output=True)
                    try:
                        import termios

                        termios.tcflush(sys.stdin.fileno(), termios.TCIOFLUSH)
                    except (ImportError, OSError, ValueError):
                        pass
                    sys.stdout.write("\033[0m\033[?25h")
                    sys.stdout.flush()
            except Exception as e:
                log.debug(f"Error during terminal reset: {e}")
        elif os.name == "nt":
            try:
                import ctypes

                kernel32 = ctypes.windll.kernel32
                handle = kernel32.GetStdHandle(-11)
                if handle not in (0, -1):
                    ansi_enabled = False
                    mode = ctypes.c_ulong()
                    if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                        new_mode = mode.value | 0x0004
                        if kernel32.SetConsoleMode(handle, new_mode):
                            ansi_enabled = True
                        else:
                            kernel32.SetConsoleMode(handle, mode.value)
                    if ansi_enabled and sys.stdout.isatty():
                        sys.stdout.write("\033[0m\033[?25h")
                        sys.stdout.flush()
                    else:
                        if sys.stdout.isatty():
                            sys.stdout.flush()
                        kernel32.SetConsoleTextAttribute(handle, 7)

                        class CONSOLE_CURSOR_INFO(ctypes.Structure):
                            _fields_ = [("dwSize", ctypes.c_ulong), ("bVisible", ctypes.c_int)]

                        cci = CONSOLE_CURSOR_INFO()
                        if kernel32.GetConsoleCursorInfo(handle, ctypes.byref(cci)):
                            if not cci.bVisible:
                                cci.bVisible = True
                                kernel32.SetConsoleCursorInfo(handle, ctypes.byref(cci))
                if hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
                    try:
                        import msvcrt

                        while msvcrt.kbhit():
                            msvcrt.getch()
                    except ImportError:
                        pass
            except Exception as e:
                log.debug(f"Error during terminal reset: {e}")
