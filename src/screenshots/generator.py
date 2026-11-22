from __future__ import annotations

import asyncio
import glob
import json
import math
import os
import random
import re
import sys
from pathlib import Path
from typing import Callable as TypingCallable
from typing import Union

from PIL import Image
from pymediainfo import MediaInfo

from src.mediainfo import MediaInfoHandler
from src.progress import ProgressManager
from src.utils.console import log

from .contact_sheet import ContactSheetGenerator

ContactSheetRequirement = Union[dict[str, int], TypingCallable[[float], tuple[int, int]], None]
ContactSheetRequirements = Union[ContactSheetRequirement, list[ContactSheetRequirement]]

try:
    import oxipng

    OXIPNG_AVAILABLE = True
except ImportError:
    OXIPNG_AVAILABLE = False


class ScreenshotGenerator:
    def __init__(self, config: dict, base_dir: Path):
        self.config = config
        self.base_dir = Path(base_dir)
        self.mihandle = MediaInfoHandler(config, self.base_dir)
        self.contact = ContactSheetGenerator(config, self.base_dir, self.mihandle)
        self.contact_cfg = self.config.get("ADULT", {}).get("contact_sheet", {})
        self.tmp_dir: Path | None = None
        self.min_image_size = 75000
        self.cache: dict[str, list[dict]] = {}
        self.screens = self.config.get("SCREENS", {})
        self.use_oxipng = self.screens.get("use_oxipng", False) and OXIPNG_AVAILABLE

        if self.screens.get("use_oxipng", False) and not OXIPNG_AVAILABLE:
            log.warning("pyoxipng not available. PNG optimization will use ffmpeg only.")

        self.pyoxipng_level = self.screens.get("oxipng_level", 4)
        self.ffdebug = False

        cpu = os.cpu_count() or 4
        self.ffmpeg_sem = asyncio.Semaphore(max(2, cpu // 2))
        self.oxipng_sem = asyncio.Semaphore(1)

    @staticmethod
    def _resolve_contact_sheet_requirement(
        requirement: ContactSheetRequirements, length: float
    ) -> dict[str, int] | None:
        if isinstance(requirement, list):
            dimensions = [
                ScreenshotGenerator._resolve_contact_sheet_requirement(item, length)
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
        self, length: float, requirement: ContactSheetRequirements = None
    ) -> dict[str, int]:
        resolved = self._resolve_contact_sheet_requirement(requirement, length)
        if resolved:
            return resolved

        cfg_rows = self.contact_cfg.get("rows")
        cfg_cols = self.contact_cfg.get("columns")
        if cfg_rows is not None and cfg_cols is not None:
            return {"columns": int(cfg_cols), "rows": int(cfg_rows)}

        rows, columns = self._get_rows_columns(length)
        return {"columns": columns, "rows": rows}

    async def generate_screenshots(
        self,
        meta: dict,
        filename: str,
        uuid: str,
        contact_sheet_dimensions: ContactSheetRequirements = None,
    ) -> dict:
        pm = ProgressManager.get_instance()
        if pm:
            pm.update(f"Starting screenshot generation for {meta.get('name', filename)}")

        self.ffdebug = meta.get("ffdebug", False) or meta.get("deep_debug")
        screens = meta.get("screens", self.config.get("DEFAULT", {}).get("screens", 3))
        contact_cfg = self.config.get("ADULT", {}).get("contact_sheet", {})
        contact_screens = 0

        if contact_sheet_dimensions and meta.get("xxx", False):
            length = await self._get_length(meta)
            dimensions = self._get_contact_sheet_dimensions(length, contact_sheet_dimensions)
            contact_screens = dimensions["rows"] * dimensions["columns"]

        self.tmp_dir = tmp_dir = self.base_dir / "tmp" / uuid
        cached = self.cache.get(uuid, [])
        existing = meta.get("image_list", {"main": [], "episodes": {}})

        if cached and self._is_sufficient(existing, meta, screens, uuid):
            if pm:
                pm.update("Using cached screenshots")
            return existing

        try:
            if pm:
                pm.update("Generating screenshots")

            if meta.get("bdinfo") not in (None, " "):
                result = await self._generate_bdmv_screenshots(meta, filename, uuid, screens, contact_screens)
            elif meta.get("discs"):
                result = await self._generate_dvd_screenshots(meta, filename, uuid, screens, contact_screens)
            elif meta.get("tv_pack") == 1:
                result = await self._generate_tv_pack_screenshots(
                    meta, filename, uuid, screens, contact_screens
                )
            else:
                result = await self._generate_file_screenshots(meta, filename, uuid, screens, contact_screens)

            if pm:
                pm.update("Screenshot generation complete")

            if contact_screens > 0:
                if pm:
                    pm.update("Creating contact sheet")

                video_files = self.build_adult_video_files(meta)
                max_sheets = contact_cfg.get("max", 1) if not meta.get("xmovie") else len(video_files)
                num_sheets = min(len(video_files), max_sheets)

                existing_contact_map = {
                    entry["local_file"]: entry
                    for entry in meta.get("image_list", {}).get("contact_sheets", [])
                    if isinstance(entry, dict) and "local_file" in entry
                }

                result["contact_sheets"] = []

                if meta.get("xmovie"):
                    for idx, vf in enumerate(video_files[:num_sheets]):
                        scene_filename = f"{filename}-scene_{idx + 1}"
                        length = float((await self._ffprobe(vf, "-show_format"))["format"]["duration"])
                        dimensions = self._get_contact_sheet_dimensions(length, contact_sheet_dimensions)
                        scene_rows, scene_columns = dimensions["rows"], dimensions["columns"]
                        out_path = tmp_dir / f"contact_sheet_{scene_columns}x{scene_rows}_{idx + 1}.png"

                        if out_path.exists() and out_path.stat().st_size > 1000:
                            existing_entry = existing_contact_map.get(out_path.name)
                            if existing_entry and any(
                                existing_entry.get(k) for k in ("img_url", "raw_url", "web_url")
                            ):
                                if pm:
                                    pm.update(f"Reusing existing contact sheet {idx + 1}")
                                result["contact_sheets"].append(existing_entry)
                            else:
                                if pm:
                                    pm.update(f"Using existing local contact sheet {idx + 1} for upload")
                                result["contact_sheets"].append(
                                    {
                                        "local_file": out_path.name,
                                        "rows": scene_rows,
                                        "columns": scene_columns,
                                    }
                                )
                            continue

                        if pm:
                            pm.update(f"Generating screenshots for contact sheet {idx + 1}")

                        file_screenshots = await self._generate_file_screenshots(
                            meta,
                            scene_filename,
                            uuid,
                            screens=scene_rows * scene_columns,
                            contact_screens=0,
                            video_path=vf,
                        )
                        screenshots = file_screenshots["main"]

                        meta_with_cache = meta.copy()
                        meta_with_cache.pop("cached_screenshots", None)

                        await asyncio.to_thread(
                            self.contact.generate_contact_sheet,
                            screenshots,
                            meta_with_cache,
                            out_path,
                            scene_rows,
                            scene_columns,
                            video_file=vf,
                        )
                        result["contact_sheets"].append(
                            {"local_file": out_path.name, "rows": scene_rows, "columns": scene_columns}
                        )

                        for ss in screenshots:
                            (tmp_dir / ss["local_file"]).unlink(missing_ok=True)
                else:
                    length = await self._get_main_video_length(meta)
                    dimensions = self._get_contact_sheet_dimensions(length, contact_sheet_dimensions)
                    rows, columns = dimensions["rows"], dimensions["columns"]
                    out_path = tmp_dir / f"contact_sheet_{columns}x{rows}.png"
                    existing_entry = existing_contact_map.get(out_path.name)

                    if out_path.exists() and out_path.stat().st_size > 1000:
                        if existing_entry and any(
                            existing_entry.get(k) for k in ("img_url", "raw_url", "web_url")
                        ):
                            if pm:
                                pm.update("Reusing existing contact sheet")
                            result["contact_sheets"].append(existing_entry)
                        else:
                            if pm:
                                pm.update("Using existing local contact sheet for upload")
                            result["contact_sheets"].append(
                                {"local_file": out_path.name, "columns": columns, "rows": rows}
                            )
                    else:
                        if pm:
                            pm.update("Generating contact sheet")
                        meta_with_cache = meta.copy()
                        meta_with_cache["cached_screenshots"] = self.cache.get(uuid, [])

                        await asyncio.to_thread(
                            self.contact.generate_contact_sheet,
                            result["main"],
                            meta_with_cache,
                            out_path,
                            columns,
                            rows,
                        )
                        result["contact_sheets"].append(
                            {"local_file": out_path.name, "columns": columns, "rows": rows}
                        )

            result["main"] = result["main"][:screens]

            if self.use_oxipng and OXIPNG_AVAILABLE:
                if pm:
                    pm.update("Optimizing with oxipng")
                optimize_tasks = [self._optimize_image(tmp_dir / img["local_file"]) for img in result["main"]]
                await asyncio.gather(*optimize_tasks)
                if pm:
                    pm.update("Optimization Complete")

            return result
        finally:
            self.cache.pop(uuid, None)

    async def _generate_file_screenshots(
        self,
        meta: dict,
        filename: str,
        uuid: str,
        screens: int,
        contact_screens: int,
        video_path: str | None = None,
    ) -> dict:
        if video_path is None:
            video_path = meta.get("video")
        num_needed = max(screens, contact_screens)

        pattern = f"{filename}-*.png"
        existing_screenshots = await self._find_existing_screenshots(pattern)

        ss_time_map = {
            img["local_file"]: img.get("ss_time", 0) for img in meta.get("image_list", {}).get("main", [])
        }

        if contact_screens > 0:
            num_to_generate = num_needed - sum(
                1 for img in existing_screenshots if ss_time_map.get(Path(img).name, 0) > 0
            )
        else:
            num_to_generate = num_needed - len(existing_screenshots)

        if num_to_generate > 0:
            existing_times = {t for t in ss_time_map.values() if t > 0}
            new_screenshots = await self._generate_file(
                meta, filename, uuid, num_to_generate, video_path, avoid_times=existing_times
            )
            existing_pairs = [(img, ss_time_map.get(Path(img).name, 0)) for img in existing_screenshots]
            combined = existing_pairs + new_screenshots
            all_screenshots = [{"local_file": Path(path).name, "ss_time": t} for path, t in combined]
        else:
            all_screenshots = [
                {"local_file": Path(img).name, "ss_time": ss_time_map.get(Path(img).name, 0)}
                for img in existing_screenshots
            ]

        def _ss_sort_key(item: dict) -> tuple[int, float | int]:
            t = item.get("ss_time") or 0
            if t == 0:
                m = re.search(r"-(\d+)\.png$", item["local_file"])
                return (1, int(m.group(1)) if m else 0)
            return (0, t)

        all_screenshots.sort(key=_ss_sort_key)
        self.cache[uuid] = all_screenshots
        return {"main": all_screenshots, "episodes": {}}

    async def _generate_tv_pack_screenshots(
        self, meta: dict, filename: str, uuid: str, screens: int, contact_screens: int
    ) -> dict:
        per_episode = self.screens.get("per_episode", 3)
        filelist = sorted(meta.get("filelist", []), key=self._get_episode_identifier)
        result = {"main": [], "episodes": {}}

        ss_time_map_main = {
            img["local_file"]: img.get("ss_time", 0) for img in meta.get("image_list", {}).get("main", [])
        }
        episodes_images = meta.get("image_list", {}).get("episodes", {})

        for ep_path in filelist:
            if not self._is_video_file(ep_path):
                continue
            ep_name = self._get_episode_identifier(ep_path)
            ep_filename = f"{filename}-{ep_name}"
            num_needed = max(screens, contact_screens) if ep_name == "E01" else per_episode

            pattern = f"{ep_filename}-*.png"
            existing_screenshots = await self._find_existing_screenshots(pattern)
            num_existing = len(existing_screenshots)
            num_to_generate = num_needed - num_existing

            ep_ss_map = (
                {img["local_file"]: img.get("ss_time", 0) for img in episodes_images.get(ep_name, [])}
                if episodes_images
                else {}
            )

            if num_to_generate > 0:
                ep_existing_times = {t for t in {**ep_ss_map, **ss_time_map_main}.values() if t > 0}
                new_screenshots = await self._generate_file(
                    meta, ep_filename, uuid, num_to_generate, ep_path, avoid_times=ep_existing_times
                )
                existing_pairs = [
                    (img, ep_ss_map.get(Path(img).name, ss_time_map_main.get(Path(img).name, 0)))
                    for img in existing_screenshots
                ]
                combined = existing_pairs + new_screenshots
                ep_screens = [{"local_file": Path(path).name, "ss_time": t} for path, t in combined]
            else:
                ep_screens = [
                    {
                        "local_file": Path(img).name,
                        "ss_time": ep_ss_map.get(Path(img).name, ss_time_map_main.get(Path(img).name, 0)),
                    }
                    for img in existing_screenshots
                ]

            if ep_name == "E01":
                result["main"] = ep_screens
            result["episodes"][ep_name] = ep_screens[:num_needed]

        return result

    async def _generate_bdmv_screenshots(
        self, meta: dict, filename: str, uuid: str, screens: int, contact_screens: int
    ) -> dict:
        num_needed = max(screens, contact_screens)
        pattern = f"{filename}-*.png"
        existing_screenshots = await self._find_existing_screenshots(pattern)
        num_existing = len(existing_screenshots)
        num_to_generate = num_needed - num_existing

        ss_time_map = {
            img["local_file"]: img.get("ss_time", 0) for img in meta.get("image_list", {}).get("main", [])
        }

        if num_to_generate > 0:
            bdinfo = meta["bdinfo"]
            existing_times = {t for t in ss_time_map.values() if t > 0}
            new_screenshots = await self._generate_bdmv(
                meta, filename, uuid, bdinfo, num_to_generate, avoid_times=existing_times
            )
            existing_pairs = [(img, ss_time_map.get(Path(img).name, 0)) for img in existing_screenshots]
            combined = existing_pairs + new_screenshots
            all_screenshots = [{"local_file": Path(path).name, "ss_time": t} for path, t in combined]
        else:
            all_screenshots = [
                {"local_file": Path(img).name, "ss_time": ss_time_map.get(Path(img).name, 0)}
                for img in existing_screenshots
            ]

        self.cache[uuid] = all_screenshots
        return {"main": all_screenshots, "episodes": {}}

    async def _generate_dvd_screenshots(
        self, meta: dict, filename: str, uuid: str, screens: int, contact_screens: int
    ) -> dict:
        num_needed = max(screens, contact_screens)
        pattern = f"{filename}-*.png"
        existing_screenshots = await self._find_existing_screenshots(pattern)
        num_existing = len(existing_screenshots)
        num_to_generate = num_needed - num_existing

        ss_time_map = {
            img["local_file"]: img.get("ss_time", 0) for img in meta.get("image_list", {}).get("main", [])
        }

        if num_to_generate > 0:
            existing_times = {t for t in ss_time_map.values() if t > 0}
            new_screenshots = await self._generate_dvd(
                meta, filename, num_to_generate, avoid_times=existing_times
            )
            existing_pairs = [(img, ss_time_map.get(Path(img).name, 0)) for img in existing_screenshots]
            combined = existing_pairs + new_screenshots
            all_screenshots = [{"local_file": Path(path).name, "ss_time": t} for path, t in combined]
        else:
            all_screenshots = [
                {"local_file": Path(img).name, "ss_time": ss_time_map.get(Path(img).name, 0)}
                for img in existing_screenshots
            ]

        self.cache[uuid] = all_screenshots
        return {"main": all_screenshots, "episodes": {}}

    async def _generate_bdmv(
        self,
        meta: dict,
        filename: str,
        uuid: str,
        bdinfo: dict,
        num_screens: int,
        avoid_times: set[float] | None = None,
    ) -> list[tuple[str, float]]:
        file, length = await self._get_main_m2ts_and_duration(uuid, bdinfo)
        keyframe = (
            "nokey" if ("VC-1" in bdinfo["video"][0]["codec"] or bdinfo["video"][0]["hdr_dv"]) else "none"
        )

        if avoid_times:
            ss_times = self._generate_valid_ss_times(length, num_screens, avoid_times=avoid_times)
        else:
            ss_times = [
                img.get("ss_time") for img in meta.get("image_list", {}).get("main", []) if "ss_time" in img
            ]
            if not ss_times or len(ss_times) < num_screens:
                ss_times = self._generate_valid_ss_times(length, num_screens)

        indices = []
        for p in self.tmp_dir.glob(f"{filename}-*.png"):
            if match := re.search(r"-(\d+)\.png$", p.name):
                indices.append(int(match.group(1)))
        max_idx = max([*indices, -1])

        log.debug(f"[bdmv] Spawning {num_screens} screenshot tasks for {file}")
        tasks = [
            self._take_screenshot(
                file, f"{filename}-{max_idx + 1 + i}.png", t, keyframe, self.ffdebug, hdr=meta["hdr"]
            )
            for i, t in enumerate(ss_times)
        ]
        results = await asyncio.gather(*tasks)
        return await self._validate_and_retry(meta, results, file, filename, length, keyframe, num_screens)

    async def _generate_dvd(
        self, meta: dict, filename: str, num_screens: int, avoid_times: set[float] | None = None
    ) -> list[tuple[str, float]]:
        disc_info = meta["discs"][0]
        vob_paths = sorted(glob.glob(f"{disc_info['path']}/VTS_*_*.VOB"))
        probe_tasks = [self._ffprobe(vob, "-show_format") for vob in vob_paths]
        probe_results = await asyncio.gather(*probe_tasks, return_exceptions=True)

        vobs = []
        for vob, probe in zip(vob_paths, probe_results):
            if isinstance(probe, Exception):
                log.warning(f"[dvd] Failed to probe {vob}: {probe}")
                continue
            try:
                duration = float(probe["format"]["duration"])
                if duration >= 60:
                    vobs.append({"file": vob, "duration": duration})
            except (KeyError, ValueError):
                continue

        if not vobs:
            raise ValueError("No valid VOBs found")

        total_length = sum(vob["duration"] for vob in vobs)
        screens_per_vob = num_screens // len(vobs)
        extra_screens = num_screens % len(vobs)

        indices = [
            int(m.group(1))
            for p in self.tmp_dir.glob(f"{filename}-*.png")
            if (m := re.search(r"-(\d+)\.png$", p.name))
        ]
        max_idx = max([*indices, -1])

        tasks = []
        counter = 0
        for vob_idx, vob in enumerate(vobs):
            num = screens_per_vob + (1 if vob_idx < extra_screens else 0)
            avoid = avoid_times if avoid_times else set()
            ss_times = self._generate_valid_ss_times(vob["duration"], num, avoid_times=avoid)
            for _i, t in enumerate(ss_times):
                tasks.append(
                    self._take_screenshot(
                        vob["file"],
                        f"{filename}-{max_idx + 1 + counter}.png",
                        t,
                        "none",
                        self.ffdebug,
                        vobs=vobs,
                    )
                )
                counter += 1

        results = await asyncio.gather(*tasks)
        return await self._validate_and_retry(
            meta, results, vobs[0]["file"], filename, total_length, "none", num_screens, vobs
        )

    async def _generate_file(
        self,
        meta: dict,
        filename: str,
        uuid: str,
        num_screens: int,
        file_path: str,
        avoid_times: set[float] | None = None,
    ) -> list[tuple[str, float]]:
        if not Path(file_path).is_file():
            return []

        keyframe = "none"
        try:
            probe = await self._ffprobe(
                file_path,
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=r_frame_rate",
                ffdebug=self.ffdebug,
            )
            format_info = await self._ffprobe(file_path, "-show_format", ffdebug=self.ffdebug)
            length = float(format_info["format"]["duration"])
            num, denom = map(int, probe["streams"][0]["r_frame_rate"].split("/"))
            frame_rate = num / denom
        except (KeyError, ValueError, RuntimeError) as e:
            try:
                probe = await self._ffprobe(file_path, "-show_format", ffdebug=self.ffdebug)
                length = float(probe["format"]["duration"])
                frame_rate = 23.976
                log.warning(f"Failed to probe frame rate for {file_path}: {e}")
            except (KeyError, ValueError, RuntimeError) as e2:
                log.error(f"Failed to probe file {file_path}: {e2}")
                raise RuntimeError(f"Failed to probe file {file_path}") from e2

        if avoid_times:
            ss_times = self._generate_valid_ss_times(length, num_screens, avoid_times=avoid_times)
        else:
            ss_times = [
                img.get("ss_time")
                for img in meta.get("image_list", {}).get("main", [])
                if img.get("ss_time") and img.get("ss_time") != 0
            ]
            if not ss_times or len(ss_times) < num_screens:
                ss_times = self._generate_valid_ss_times(length, num_screens)

        keyframes = []
        if self.screens.get("keyframes_only", False) and self.screens.get("frame_stats", False):
            try:
                intervals = ", ".join(f"{max(0, t - 30)}%{t + 30}" for t in ss_times)
                keyframe_probe = await self._ffprobe(
                    file_path,
                    "-select_streams",
                    "v:0",
                    "-skip_frame",
                    "nokey",
                    "-show_entries",
                    "frame=pkt_pts_time,key_frame",
                    "-show_frames",
                    "-read_intervals",
                    intervals,
                    ffdebug=self.ffdebug,
                )
                if self.ffdebug:
                    log.info(f"Raw ffprobe output for keyframes: {keyframe_probe}")
                for frame in keyframe_probe.get("frames", []):
                    if frame.get("key_frame") == 1:
                        try:
                            keyframes.append(float(frame["pts_time"]))
                        except (ValueError, KeyError):
                            continue
                if not keyframes:
                    log.warning(
                        f"No keyframes found for {file_path} in intervals {intervals}. Using original ss_times."
                    )
                else:
                    log.debug(f"Found {len(keyframes)} keyframes for {file_path}")
            except Exception as e:
                log.warning(f"Failed to probe keyframes for {file_path}: {e}")

        adjusted_ss_times = []
        if keyframes:
            keyframe = "nokey"
            for t in ss_times:
                closest_keyframe = min(keyframes, key=lambda x: abs(x - t))
                adjusted_ss_times.append(closest_keyframe)
        else:
            adjusted_ss_times = ss_times

        indices = [
            int(m.group(1))
            for p in self.tmp_dir.glob(f"{filename}-*.png")
            if (m := re.search(r"-(\d+)\.png$", p.name))
        ]
        max_idx = max([*indices, -1])

        tasks = [
            self._take_screenshot(
                file_path,
                f"{filename}-{max_idx + 1 + i}.png",
                t,
                keyframe,
                self.ffdebug,
                hdr=meta.get("hdr"),
                frame_rate=frame_rate,
                adjusted_ss_time=t,
            )
            for i, t in enumerate(adjusted_ss_times)
        ]
        results = await asyncio.gather(*tasks)
        return await self._validate_and_retry(meta, results, file_path, filename, length, "none", num_screens)

    async def _take_screenshot(
        self,
        file: str,
        output: str,
        ss_time: float,
        keyframe: str,
        ffdebug: bool,
        retries: int = 3,
        vobs: list | None = None,
        hdr: str | None = None,
        frame_rate: float = 23.976,
        adjusted_ss_time: float | None = None,
    ) -> tuple[str, float] | None:
        out_path = self.tmp_dir / output if not Path(output).is_absolute() else Path(output)
        output_str = str(out_path)
        loglevel = "verbose" if ffdebug else "error"
        vf_filters = []

        if vobs:
            vf_filters.append(self.screens.get("deinterlace_method", "yadif"))
        if hdr and self.screens.get("tonemap_hdr", True):
            vf_filters.append("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=709")
            vf_filters.append(f"tonemap={self.config['SCREENS'].get('hdr_method', 'reinhard:desat=0')}")
            vf_filters.append("zscale=t=bt709:m=bt709:r=full,format=rgb24")

        if self.screens.get("frame_stats", False):
            font = str(Path(__file__).parent / "fonts" / "Dosis.ttf")
            if sys.platform == "win32":
                font = font.replace("\\", "\\\\").replace(":", "\\:")
            frame_time = adjusted_ss_time if adjusted_ss_time is not None else ss_time
            frame_num = round(frame_time * frame_rate)
            stats = (
                f"drawtext=fontfile='{font}':fontsize=24:fontcolor=white:x=10:y=10:box=1:boxcolor=black@0.5:boxborderw=5:"
                f"text='%{{pict_type}}-Frame\\nFrame\\: {frame_num}\\n%{{pts\\:hms}}'"
            )
            vf_filters.append(stats)

        vf_filter = ", ".join(vf_filters)
        optimize = self.screens.get("optimize", False)
        o_lvl = (6 if optimize is True else min(optimize, 9)) if not self.use_oxipng and optimize else 0

        for _attempt in range(retries):
            try:
                args = [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    loglevel,
                    "-hide_banner",
                    "-ss",
                    str(ss_time),
                    "-copyts",
                    "-i",
                    file,
                    "-vframes",
                    "1",
                    "-compression_level",
                    str(o_lvl),
                    "-pred",
                    "mixed",
                    output_str,
                ]
                if vf_filter:
                    args.insert(-3, "-vf")
                    args.insert(-3, vf_filter)
                if keyframe == "nokey":
                    args.insert(1, "-skip_frame")
                    args.insert(2, "nokey")

                if ffdebug:
                    log.info(f"COMMAND CALLED:\n[blue]{args}\n")

                log.debug(f"[ffmpeg] Spawning subprocess for {output_str} at {ss_time}s")
                async with self.ffmpeg_sem:
                    process = await asyncio.create_subprocess_exec(
                        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                    )
                    try:
                        _stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300)
                    except asyncio.TimeoutError:
                        log.error(
                            f"[ffmpeg] Timeout after 300s for {output_str} at {ss_time}s — killing process"
                        )
                        try:
                            process.kill()
                            await process.wait()
                        except Exception:
                            pass
                        if Path(output_str).exists():
                            Path(output_str).unlink(missing_ok=True)
                        return None

                stderr_text = stderr.decode("utf-8", errors="ignore")
                if ffdebug:
                    log.info(f"ffmpeg stderr:\n{stderr_text}")

                if process.returncode != 0:
                    log.error(
                        f"FFmpeg failed for {output_str} with returncode {process.returncode}: {stderr_text}"
                    )
                    continue

                if process.returncode == 0 and Path(output_str).exists():
                    size = Path(output_str).stat().st_size
                    is_mono = await asyncio.to_thread(self.is_monochromatic, output_str)
                    if self.min_image_size <= size and not is_mono:
                        return (output_str, ss_time)
                    log.debug(f"Discarding {output_str}: {'monochromatic' if is_mono else 'invalid size'}")
                    Path(output_str).unlink()
            except Exception as e:
                log.error(f"FFmpeg failed for {output_str}: {e}")
                if Path(output_str).exists():
                    Path(output_str).unlink()
        return None

    def _check_screenshot(self, path_str: str) -> bool:
        p = Path(path_str)
        if not p.exists() or p.stat().st_size < self.min_image_size:
            return False
        return not self.is_monochromatic(path_str)

    async def _validate_and_retry(
        self,
        meta: dict,
        results: list,
        file: str,
        filename: str,
        length: float,
        keyframe: str,
        num_screens: int,
        vobs: list | None = None,
        max_retries: int = 3,
    ) -> list[tuple[str, float]]:
        valid = [(r[0], r[1]) for r in results if r and await asyncio.to_thread(self._check_screenshot, r[0])]
        missing = num_screens - len(valid)
        log.debug(f"[validate] {len(valid)}/{num_screens} valid, {missing} missing")

        attempt = 0
        while missing > 0 and attempt < max_retries:
            attempt += 1
            used_times = {t for _, t in valid}
            new_times = self._generate_valid_ss_times(length, missing, avoid_times=used_times)
            start_idx = len(valid) + 1
            log.debug(f"[validate] Retry {attempt}/{max_retries}: generating {len(new_times)} more")

            tasks = [
                self._take_screenshot(
                    file,
                    f"{filename}-{start_idx + i}.png",
                    t,
                    keyframe,
                    self.ffdebug,
                    vobs=vobs,
                    hdr=meta.get("hdr"),
                )
                for i, t in enumerate(new_times)
            ]
            retry_results = await asyncio.gather(*tasks)
            for r in retry_results:
                if r and await asyncio.to_thread(self._check_screenshot, r[0]):
                    valid.append((r[0], r[1]))
            missing = num_screens - len(valid)

        if missing > 0:
            log.warning(
                f"[validate] Could not produce {num_screens} valid screenshots; returning {len(valid)}"
            )
        return valid

    def _generate_valid_ss_times(
        self, length: float, num_screens: int, avoid_times: set[float] | None = None
    ) -> list[float]:
        avoid_times = avoid_times or set()
        start_buffer = max(length * 0.1, 10)
        end_buffer = min(length * 0.9, length - 10)
        interval = (end_buffer - start_buffer) / max(num_screens, 1) if num_screens > 0 else 0
        times = set()

        for i in range(num_screens):
            base = start_buffer + i * interval
            t = None
            for _ in range(100):
                candidate = base + random.uniform(-interval / 4, interval / 4)
                if candidate not in avoid_times and start_buffer <= candidate <= end_buffer:
                    t = candidate
                    break
            if t is None:
                t = base
                log.debug(f"_generate_valid_ss_times: fallback to base time {t}")
            times.add(t)

        if len(times) < num_screens:
            log.warning(
                f"_generate_valid_ss_times: expected {num_screens}, got {len(times)}. Adding fallbacks."
            )
            existing = sorted(times)
            while len(times) < num_screens:
                for i in range(len(existing) - 1):
                    midpoint = (existing[i] + existing[i + 1]) / 2
                    if midpoint not in avoid_times:
                        times.add(midpoint)
                        existing = sorted(times)
                        if len(times) >= num_screens:
                            break
                else:
                    step = (end_buffer - start_buffer) / (num_screens + 1)
                    for j in range(1, num_screens + 1):
                        t = start_buffer + j * step
                        if t not in avoid_times:
                            times.add(t)
                        if len(times) >= num_screens:
                            break
                    break
        return sorted(times)

    def is_monochromatic(self, image_path: str, sample_size: int = 100) -> bool:
        try:
            with Image.open(image_path) as img:
                img = img.convert("RGB")
                width, height = img.size
                if width * height < sample_size:
                    pixels = list(img.getdata())
                else:
                    pixels = [
                        img.getpixel((random.randint(0, width - 1), random.randint(0, height - 1)))
                        for _ in range(sample_size)
                    ]
                first_pixel = pixels[0]
                return all(pixel == first_pixel for pixel in pixels)
        except Exception as e:
            log.error(f"Error checking if {image_path} is monochromatic: {e}")
            return False

    def _is_sufficient(self, existing: dict, meta: dict, screens: int, uuid: str) -> bool:
        if not existing:
            return False

        def files_exist(section: list) -> bool:
            tmp_path = self.base_dir / "tmp" / uuid
            for img in section:
                img_url = img.get("img_url")
                if img_url and img_url.strip():
                    continue
                local_file = img.get("local_file", "")
                if local_file and (tmp_path / local_file).exists():
                    continue
                return False
            return True

        contact_ok = True
        if meta.get("xxx") and not self.config.get("ADULT", {}).get("contact_sheet", {}).get("skip", False):
            contact_cfg = self.config.get("ADULT", {}).get("contact_sheet", {})
            video_files = self.build_adult_video_files(meta)
            if meta.get("xmovie") or meta.get("xpack"):
                max_sheets = contact_cfg.get("max", 1) if not meta.get("xmovie") else len(video_files)
                num_expected = min(len(video_files), max_sheets)
            else:
                num_expected = 1
            contact_section = existing.get("contact_sheets", [])
            contact_ok = len(contact_section) >= num_expected and files_exist(contact_section)

        if meta.get("tv_pack") == 1:
            per_episode = self.screens.get("per_episode", 3)
            filelist = meta.get("filelist", [])
            if not filelist:
                return (
                    len(existing.get("main", [])) >= screens
                    and files_exist(existing.get("main", []))
                    and contact_ok
                )
            return (
                len(existing.get("main", [])) >= screens
                and files_exist(existing.get("main", []))
                and all(
                    len(existing["episodes"].get(self._get_episode_identifier(path), [])) >= per_episode
                    and files_exist(existing["episodes"].get(self._get_episode_identifier(path), []))
                    for path in filelist
                    if self._is_video_file(path)
                )
                and contact_ok
            )

        return (
            len(existing.get("main", [])) >= screens and files_exist(existing.get("main", [])) and contact_ok
        )

    async def _get_main_m2ts_and_duration(self, uuid: str, bdinfo: dict) -> tuple[str, float]:
        def _scan() -> str:
            stream_path = Path(bdinfo["path"]) / "STREAM"
            if not stream_path.exists():
                raise FileNotFoundError(f"STREAM directory does not exist at {stream_path}")
            m2ts_files = list(stream_path.glob("*.m2ts"))
            if not m2ts_files:
                raise FileNotFoundError(f"No .m2ts files found in {stream_path}")
            return str(max(m2ts_files, key=lambda p: p.stat().st_size))

        largest = await asyncio.to_thread(_scan)
        cached = self.mihandle._cache.get(uuid)
        duration = None

        if cached:
            for track in cached.get("media", {}).get("track", []):
                if track.get("@type") == "Video" and "Duration" in track:
                    duration = float(track["Duration"])
                    break

        if duration is None:
            mi = await asyncio.to_thread(MediaInfo.parse, largest)
            for track in mi.tracks:
                if track.track_type == "Video" and track.duration:
                    duration = float(track.duration)
                    break

        if duration is None or duration <= 0:
            raise ValueError(f"Could not extract valid duration from {largest}: duration={duration}")
        if duration < 60:
            raise ValueError(f"Duration {duration} seconds is too short for {largest}")

        log.debug(f"RETURNING LARGEST ({largest}) + DURATION ({duration})")
        return largest, duration

    def _get_episode_identifier(self, path: str) -> str:
        if match := re.search(r"S\d{2}E(\d{2})", str(path), re.IGNORECASE):
            return f"E{match.group(1)}"
        if match := re.search(r"E(\d{2})", str(path), re.IGNORECASE):
            return f"E{match.group(1)}"
        return Path(path).stem.split(".")[0]

    def _is_video_file(self, path: str) -> bool:
        return Path(path).suffix.lower() in {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm"}

    def build_adult_video_files(self, meta: dict) -> list[str]:
        BONUS_RE = re.compile(
            r"(?:^|[_\W])(blooper|bloopers|bonus|credit|credits|extra|extras|gallery|intro|outro|photo|photos|photoset|photosets|sample|trailer)(?:\d+)?(?:$|[_\W])",
            re.I,
        )

        def _is_bonus(path: str) -> bool:
            return bool(BONUS_RE.search(Path(path).stem))

        video_files = [vf for vf in meta.get("video_files", [meta.get("video")]) if vf and not _is_bonus(vf)]

        if meta.get("xmovie"):
            video_files.sort(
                key=lambda p: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", Path(p).stem)]
            )
        elif meta.get("xpack"):
            contact_cfg = self.config.get("ADULT", {}).get("contact_sheet", {})
            pack_limit = contact_cfg.get("pack_max", 6)

            def _file_size(p: str) -> int:
                try:
                    return Path(p).stat().st_size
                except OSError:
                    return 0

            video_files = sorted(video_files, key=_file_size, reverse=True)[:pack_limit]

        return video_files

    async def _get_main_video_length(self, meta: dict) -> float:
        video_files = meta.get("video_files", [meta.get("video")])
        probe = await self._ffprobe(video_files[0], "-show_format")
        return float(probe["format"]["duration"])

    async def _get_length(self, meta: dict) -> float:
        try:
            if meta.get("bdinfo") not in (None, " "):
                _, duration = await self._get_main_m2ts_and_duration(meta["uuid"], meta["bdinfo"])
                return duration
            elif meta.get("discs"):
                disc_info = meta["discs"][0]
                vob_paths = sorted(glob.glob(f"{disc_info['path']}/VTS_*_*.VOB"))
                probe_tasks = [self._ffprobe(vob, "-show_format") for vob in vob_paths]
                probe_results = await asyncio.gather(*probe_tasks, return_exceptions=True)
                total = 0.0
                for _vob, probe in zip(vob_paths, probe_results):
                    if isinstance(probe, Exception):
                        continue
                    try:
                        dur = float(probe["format"]["duration"])
                        if dur >= 60:
                            total += dur
                    except (KeyError, ValueError):
                        continue
                return total
            elif meta.get("tv_pack") == 1:
                filelist = sorted(meta.get("filelist", []), key=self._get_episode_identifier)
                first_ep = next((p for p in filelist if self._is_video_file(p)), None)
                if first_ep:
                    probe = await self._ffprobe(first_ep, "-show_format")
                    return float(probe["format"]["duration"])
                return 0
            else:
                video = meta.get("video", meta.get("video_files", [None])[0])
                if video and Path(video).is_file():
                    probe = await self._ffprobe(video, "-show_format")
                    return float(probe["format"]["duration"])
                return 0
        except Exception as e:
            log.warning(f"Failed to get length for contact sheet: {e}")
            return 7200

    def _get_rows_columns(self, length: float) -> tuple[int, int]:
        frames = min(30, max(9, round(length / 300)))

        cols = 3
        rows = math.ceil(frames / cols)

        return rows, cols

    async def _ffprobe(self, file_path: str, *extra_args: str, ffdebug: bool = False) -> dict:
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", *extra_args, str(file_path)]
        if ffdebug:
            log.info(f"Running ffprobe: {' '.join(cmd)!r}")

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300)
            except asyncio.TimeoutError as err:
                process.kill()
                await process.wait()
                raise RuntimeError(f"ffprobe timed out for {file_path}") from err

            stderr_text = stderr.decode("utf-8", errors="ignore").strip()
            if process.returncode != 0:
                raise RuntimeError(f"ffprobe failed with code {process.returncode}: {stderr_text}")
            if not stdout:
                raise RuntimeError("ffprobe returned empty output")
            return json.loads(stdout.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise RuntimeError(f"ffprobe returned invalid JSON: {e}") from e
        except Exception as e:
            raise RuntimeError(f"ffprobe error: {e!s}") from e

    async def _optimize_image(self, image_path: Path):
        try:
            async with self.oxipng_sem:
                await asyncio.to_thread(oxipng.optimize, str(image_path), level=self.pyoxipng_level)
            log.debug(f"Optimized {image_path} with pyoxipng level {self.pyoxipng_level}")
        except Exception as e:
            log.warning(f"Failed to optimize {image_path} with pyoxipng: {e}")

    async def _find_existing_screenshots(self, pattern: str) -> list[str]:
        def _scan() -> list[str]:
            return [str(p) for p in self.tmp_dir.glob(pattern) if p.stat().st_size > 1000]

        return await asyncio.to_thread(_scan)
