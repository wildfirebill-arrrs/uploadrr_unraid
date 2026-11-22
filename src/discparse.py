from __future__ import annotations

import asyncio
import json
import math
import os
import re
import sys
import traceback
import xml.etree.ElementTree as ET
from collections import OrderedDict
from dataclasses import dataclass
from glob import glob
from pathlib import Path

from pymediainfo import MediaInfo

from src.exceptions import BDInfoExtractionError
from src.progress import ProgressManager
from src.utils.bdinfo import ensure_bdinfo
from src.utils.console import console, log


@dataclass
class PlaylistEntry:
    index: int
    group: int
    playlist: str
    duration: str
    duration_minutes: float
    estimated_bytes: int
    measured_bytes: int | None


class DiscParse:
    def __init__(self):
        pass

    async def get_bdinfo(self, discs: list[dict], folder_id: str, base_dir: str) -> tuple[list[dict], dict]:
        save_dir = Path(base_dir) / "tmp" / folder_id
        pm = ProgressManager.get_instance()

        for i, disc in enumerate(discs):
            path = Path(disc["path"]).resolve()
            summary_file = save_dir / f"BD_SUMMARY_{str(i).zfill(2)}.txt"
            report_file = save_dir / f"BD_REPORT_{str(i).zfill(2)}.txt"
            ext_file = save_dir / "BD_SUMMARY_EXT.txt"

            bdinfo_text: Path | None = None
            title_from_xml: str | None = None

            if summary_file.exists() and summary_file.stat().st_size > 0:
                bdinfo_text = summary_file
                summary_content = await asyncio.to_thread(summary_file.read_text, encoding="utf-8")
                if summary_content.startswith("Title: "):
                    title_from_xml = summary_content.splitlines()[0].replace("Title: ", "").strip()
                    log.debug(f"Using existing title from {summary_file}: {title_from_xml}")
            else:
                title_from_xml = await self._get_title_from_xml(path)
                log.debug(f"Extracted title from XML: {title_from_xml}")

            if not bdinfo_text:
                await self._prepare_disc(path)
                try:
                    pm.update("Scanning playlists...")
                    listing = await self._run_bdinfo_list(path)
                    entries = self._parse_playlist_listing(listing)
                    candidate = self._select_candidate_playlist(entries)
                    pm.update(f"Selected {candidate} for scan...")
                    pm.update(f"Scanning {candidate}...")
                    await self._run_bdinfo_targeted(path, save_dir, candidate)
                    bdinfo_text = await self._find_bdinfo_report(save_dir)
                    if bdinfo_text is None:
                        raise BDInfoExtractionError("Targeted scan produced no report file")
                except Exception:
                    log.warning("Two-phase scan failed, falling back to full disc scan")
                    pm.update("Full disc scan (fallback)...")
                    await self._run_bdinfo_whole(path, save_dir)
                    bdinfo_text = await self._find_bdinfo_report(save_dir)

                if bdinfo_text and bdinfo_text != report_file:
                    await asyncio.to_thread(bdinfo_text.rename, report_file)
                    bdinfo_text = report_file

            if bdinfo_text:
                try:
                    bd_summary, ext_bd_summary, files_section = await self._extract_bdinfo_sections(
                        bdinfo_text
                    )

                    if not summary_file.exists() or summary_file.stat().st_size == 0:
                        summary_content = f"Title: {title_from_xml or 'Unknown'}\n{bd_summary.strip()}"
                        await asyncio.to_thread(summary_file.write_text, summary_content, encoding="utf-8")
                        log.debug(f"Wrote new {summary_file} with title: {title_from_xml}")
                    if not ext_file.exists() or ext_file.stat().st_size == 0:
                        await asyncio.to_thread(ext_file.write_text, ext_bd_summary.strip(), encoding="utf-8")

                    bdinfo = await self.parse_bdinfo(bd_summary, files_section, path)
                    if bdinfo.get("title") is None or not bdinfo.get("title"):
                        bdinfo["title"] = title_from_xml
                    elif title_from_xml and bdinfo.get("title") != title_from_xml:
                        log.debug(
                            f"BDInfo title '{bdinfo['title']}' overridden by XML title '{title_from_xml}'"
                        )
                        bdinfo["title"] = title_from_xml

                    disc["summary"] = bd_summary.strip()
                    disc["bdinfo"] = bdinfo
                    pm.update(f"BDInfo scan complete: {path.name}")

                except Exception:
                    console.print(f"Error processing BDInfo: {traceback.format_exc()}")
                    await asyncio.sleep(5)
                    continue
        if not discs or "bdinfo" not in discs[0]:
            raise BDInfoExtractionError(f"No BDInfo extracted for folder: {folder_id}")

        return discs, discs[0]["bdinfo"]

    async def _prepare_disc(self, path: Path) -> None:
        if sys.platform.startswith("darwin"):
            await self.remove_system_files(path)

    async def _find_bdinfo_report(self, save_dir: Path) -> Path | None:
        for file in await asyncio.to_thread(list, save_dir.iterdir()):
            if file.name.startswith("BDINFO"):
                return file
        return None

    async def _run_bdinfo_list(self, path: Path) -> str:
        binary = ensure_bdinfo()
        if not binary:
            raise BDInfoExtractionError("BDInfo binary not found and could not be downloaded")
        cmd = [str(binary), "-l", str(path)]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise BDInfoExtractionError(f"BDInfo -l failed: {stderr.decode()}")
        return stdout.decode("utf-8")

    def _parse_playlist_listing(self, text: str) -> list[PlaylistEntry]:
        pattern = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\w+\.MPLS)\s+(\d+:\d+:\d+)\s+([\d,]+)\s+([\d,-]+)")
        entries: list[PlaylistEntry] = []
        for line in text.splitlines():
            m = pattern.match(line)
            if not m:
                continue
            index = int(m.group(1))
            group = int(m.group(2))
            playlist = m.group(3)
            duration = m.group(4)
            parts = duration.split(":")
            hours, minutes, seconds = int(parts[0]), int(parts[1]), int(parts[2])
            duration_minutes = hours * 60 + minutes + seconds / 60
            estimated_bytes = int(m.group(5).replace(",", ""))
            measured_raw = m.group(6).replace(",", "")
            measured_bytes = int(measured_raw) if measured_raw != "-" else None
            entries.append(
                PlaylistEntry(
                    index=index,
                    group=group,
                    playlist=playlist,
                    duration=duration,
                    duration_minutes=duration_minutes,
                    estimated_bytes=estimated_bytes,
                    measured_bytes=measured_bytes,
                )
            )
        return entries

    @staticmethod
    def _score_entry(entry: PlaylistEntry) -> float:
        base = entry.estimated_bytes
        if entry.measured_bytes is None or entry.measured_bytes == 0:
            return base
        return base / (1 + math.log10(max(entry.measured_bytes, 1)))

    def _select_candidate_playlist(self, entries: list[PlaylistEntry]) -> str:
        if not entries:
            raise BDInfoExtractionError("No playlists found in BDInfo -l output")

        long_enough = [e for e in entries if e.duration_minutes >= 10]
        pool = long_enough if long_enough else entries

        sorted_pool = sorted(pool, key=lambda e: e.duration_minutes)
        clusters: list[list[PlaylistEntry]] = []
        current: list[PlaylistEntry] = [sorted_pool[0]]
        for entry in sorted_pool[1:]:
            if entry.duration_minutes - current[-1].duration_minutes <= 2:
                current.append(entry)
            else:
                clusters.append(current)
                current = [entry]
        clusters.append(current)

        largest_cluster = max(clusters, key=len)
        is_tv = len(largest_cluster) >= 3 and largest_cluster[0].duration_minutes > 20

        best = max(largest_cluster, key=self._score_entry) if is_tv else max(pool, key=self._score_entry)

        return best.playlist

    async def _run_bdinfo_targeted(self, path: Path, save_dir: Path, playlist: str) -> None:
        binary = ensure_bdinfo()
        if not binary:
            raise BDInfoExtractionError("BDInfo binary not found and could not be downloaded")
        cmd = [str(binary), "-m", playlist, str(path), str(save_dir)]
        pm = ProgressManager.get_instance()
        with pm.pause_for_interaction():
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.wait()
        if proc.returncode != 0:
            raise BDInfoExtractionError(f"BDInfo -m failed with return code {proc.returncode}")

    async def _run_bdinfo_whole(self, path: Path, save_dir: Path) -> None:
        binary = ensure_bdinfo()
        if not binary:
            raise BDInfoExtractionError("BDInfo binary not found and could not be downloaded")
        cmd = [str(binary), "-w", str(path), str(save_dir)]
        pm = ProgressManager.get_instance()
        with pm.pause_for_interaction():
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.wait()
        if proc.returncode != 0:
            raise BDInfoExtractionError(f"BDInfo -w failed with return code {proc.returncode}")

    async def _extract_bdinfo_sections(self, bdinfo_text: Path) -> tuple[str, str, str]:
        """Extract relevant sections from BDInfo text asynchronously with fallback."""
        text = await asyncio.to_thread(bdinfo_text.read_text, encoding="utf-8")

        files_section = ""
        quick_summary = ""
        ext_bd_summary = ""

        if "QUICK SUMMARY:" in text:
            main_sections = text.split("QUICK SUMMARY:", 2)
            if "FILES:" in main_sections[0]:
                files_parts = main_sections[0].split("FILES:", 2)
                if len(files_parts) > 1 and "CHAPTERS:" in files_parts[1]:
                    files_section = files_parts[1].split("CHAPTERS:", 2)[0].split("-------------")[-1]
                elif len(files_parts) > 1:
                    files_section = files_parts[1]
            if len(main_sections) > 1:
                quick_summary = main_sections[1].split("********************", 1)[0].rstrip()
        else:
            quick_summary = text.split("********************", 1)[0].rstrip()

        if "[code]" in text:
            ext_sections = text.split("[code]", 3)
            if len(ext_sections) > 2 and "FILES:" in ext_sections[2]:
                ext_bd_summary = ext_sections[2].split("FILES:", 1)[0].rstrip()
            elif len(ext_sections) > 2:
                ext_bd_summary = ext_sections[2].rstrip()

        # Fallback if sections are empty
        if not files_section:
            files_section = "No files section found"
        if not quick_summary:
            quick_summary = "No quick summary found"
        if not ext_bd_summary:
            ext_bd_summary = "No extended summary found"

        return quick_summary, ext_bd_summary, files_section

    async def remove_system_files(self, path: Path) -> None:
        """Removes macOS indexing (spotlight) files starting with '._'"""

        async def remove_file(file_path: Path):
            try:
                file_path.unlink()
                log.debug(f"Removed system file: {file_path}")
            except Exception as e:
                log.error(f"Error removing {file_path}: {e}")

        tasks = []
        for root, _dirs, files in await asyncio.to_thread(path.walk):
            for filename in files:
                if filename.startswith("._"):
                    file_path = Path(root) / filename
                    tasks.append(remove_file(file_path))

        if tasks:
            await asyncio.gather(*tasks)

    async def _get_title_from_xml(self, path: Path) -> str | None:
        """Extract title from bdmt_*.xml file if available."""
        xml_files = list((path / "META" / "DL").glob("bdmt_*.xml"))
        if not xml_files:
            log.debug(f"No bdmt_*.xml file found in {path / 'META' / 'DL'}")
            return ""

        try:
            xml_content = await asyncio.to_thread(Path(xml_files[0]).read_text, encoding="utf-8")
            root = ET.fromstring(xml_content)
            namespace = {"di": "urn:BDA:bdmv;discinfo"}
            title_elem = root.find(".//di:title/di:name", namespace)
            return title_elem.text if title_elem is not None else ""
        except (ET.ParseError, IndexError) as e:
            log.error(f"Error parsing XML for title: {e}")
            return ""

    async def parse_bdinfo(self, bdinfo_input: str, files: str, path: Path) -> dict:
        """Parse BDInfo data into structured dictionary."""
        bdinfo = {
            "video": [],
            "audio": [],
            "subtitles": [],
            "path": str(path),
            "title": None,  # Default to None
        }

        for line in bdinfo_input.splitlines():
            line_lower = line.strip().lower()
            if line_lower.startswith("*"):
                line_lower = line_lower[1:].strip()

            if line_lower.startswith("playlist:"):
                bdinfo["playlist"] = line.split(":", 1)[1].split(".", 1)[0].strip()
            elif line_lower.startswith("disc size:"):
                size = float(line.split(":", 1)[1].split("bytes", 1)[0].replace(",", "")) / (1 << 30)
                bdinfo["size"] = size
            elif line_lower.startswith("length:"):
                bdinfo["length"] = line.split(":", 1)[1].split(".", 1)[0].strip()
            elif line_lower.startswith("video:"):
                video_part = line.split(":", 1)[1]
                video_data = video_part.split("/", 12)
                n = 1 if len(video_data) > 2 and "Eye" in video_data[2] else 0
                required_len = n + 9
                if len(video_data) < required_len:
                    video_data.extend([""] * (required_len - len(video_data)))
                bdinfo["video"].append(
                    {
                        "codec": video_data[0].strip(),
                        "bitrate": video_data[1].strip(),
                        "res": video_data[n + 2].strip(),
                        "fps": video_data[n + 3].strip(),
                        "aspect_ratio": video_data[n + 4].strip(),
                        "profile": video_data[n + 5].strip(),
                        "bit_depth": video_data[n + 6].strip(),
                        "hdr_dv": video_data[n + 7].strip(),
                        "color": video_data[n + 8].strip(),
                        "3d": video_data[2].strip() if n else "",
                    }
                )
            elif line_lower.startswith("audio:"):
                audio_line = line.split("(")[0].strip()
                audio_data = audio_line.split(":", 1)[1].split("/")
                n = 1 if len(audio_data) > 2 and "Atmos" in audio_data[2].strip() else 0
                if len(audio_data) < 12:
                    audio_data.extend([""] * (12 - len(audio_data)))
                bdinfo["audio"].append(
                    {
                        "language": audio_data[0].strip(),
                        "codec": audio_data[1].strip(),
                        "channels": audio_data[n + 2].strip(),
                        "sample_rate": audio_data[n + 3].strip(),
                        "bitrate": audio_data[n + 4].strip(),
                        "bit_depth": audio_data[n + 5].strip() if n + 5 < len(audio_data) else "",
                        "atmos_why_you_be_like_this": audio_data[2].strip() if n else "",
                    }
                )
            elif line_lower.startswith("disc title:"):
                bdinfo["title"] = line.split(":", 1)[1].strip()
            elif line_lower.startswith("disc label:"):
                bdinfo["label"] = line.split(":", 1)[1].strip()
            elif line_lower.startswith("subtitle:"):
                bdinfo["subtitles"].append(line.split(":", 1)[1].split("/")[0].strip())

        bdinfo["files"] = []
        for line in files.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                filename, _, duration = parts[0], parts[1], parts[2]
                if all(c.isdigit() or c == ":" for c in duration) and duration.count(":") >= 1:
                    bdinfo["files"].append({"file": filename, "length": duration})

        return bdinfo

    """
    Parse VIDEO_TS and get mediainfos
    """

    async def get_dvdinfo(self, discs):
        for each in discs:
            path = each.get("path")
            os.chdir(path)
            files = glob("VTS_*.VOB")
            files.sort()
            # Switch to ordered dictionary
            filesdict = OrderedDict()
            main_set = []
            # Use ordered dictionary in place of list of lists
            for file in files:
                trimmed = file[4:]
                if trimmed[:2] not in filesdict:
                    filesdict[trimmed[:2]] = []
                filesdict[trimmed[:2]].append(trimmed)
            main_set_duration = 0
            for vob_set in filesdict.values():
                # Parse media info for this VOB set
                vob_set_mi = MediaInfo.parse(f"VTS_{vob_set[0][:2]}_0.IFO", output="JSON")
                vob_set_mi = json.loads(vob_set_mi)
                vob_set_duration = vob_set_mi["media"]["track"][1]["Duration"]

                # If the duration of the new vob set > main set by more than 10% then it's our new main set
                # This should make it so TV shows pick the first episode
                if (float(vob_set_duration) * 1.00) > (float(main_set_duration) * 1.10) or len(main_set) < 1:
                    main_set = vob_set
                    main_set_duration = vob_set_duration
            each["main_set"] = main_set
            set = main_set[0][:2]
            each["vob"] = vob = f"{path}/VTS_{set}_1.VOB"
            each["ifo"] = ifo = f"{path}/VTS_{set}_0.IFO"
            each["vob_mi"] = MediaInfo.parse(
                os.path.basename(vob), output="STRING", full=False, mediainfo_options={"inform_version": "1"}
            ).replace("\r\n", "\n")
            each["ifo_mi"] = MediaInfo.parse(
                os.path.basename(ifo), output="STRING", full=False, mediainfo_options={"inform_version": "1"}
            ).replace("\r\n", "\n")
            each["vob_mi_full"] = MediaInfo.parse(
                vob, output="STRING", full=False, mediainfo_options={"inform_version": "1"}
            ).replace("\r\n", "\n")
            each["ifo_mi_full"] = MediaInfo.parse(
                ifo, output="STRING", full=False, mediainfo_options={"inform_version": "1"}
            ).replace("\r\n", "\n")

            size = sum(os.path.getsize(f) for f in os.listdir(".") if os.path.isfile(f)) / float(1 << 30)
            if size <= 7.95:
                dvd_size = "DVD9"
                if size <= 4.37:
                    dvd_size = "DVD5"
            each["size"] = dvd_size
        return discs

    async def get_hddvd_info(self, discs):
        for each in discs:
            path = each.get("path")
            os.chdir(path)
            files = glob("*.EVO")
            size = 0
            largest = files[0]
            # get largest file from files
            for file in files:
                file_size = os.path.getsize(file)
                if file_size > size:
                    largest = file
                    size = file_size
            each["evo_mi"] = MediaInfo.parse(
                os.path.basename(largest),
                output="STRING",
                full=False,
                mediainfo_options={"inform_version": "1"},
            )
            each["largest_evo"] = os.path.abspath(f"{path}/{largest}")
        return discs
