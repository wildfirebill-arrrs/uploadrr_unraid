from __future__ import annotations

import asyncio
from pathlib import Path

from charset_normalizer import from_bytes

from src.mediainfo import MediaInfoHandler
from src.metascraper import MetaScraper
from src.musicprocessor import MusicProcessor
from src.name import NameGenerator
from src.progress import ProgressManager
from src.utils.console import console, log
from src.utils.constants import (
    IMAGE_EXTS,
    LOG_EXT,
    MUSIC_EXTS,
    NFO_EXT,
    TXT_EXT,
    VIDEO_EXTS,
)
from src.utils.descgen import DescGenerator
from src.videoprocessor import VideoProcessor


class Prep:
    """
    Prepare for upload:
    Mediainfo/BDInfo
    Screenshots
    Database Identifiers (TMDB/IMDB/MAL/etc)
    Create Name
    """

    def __init__(self, config: dict, base_dir: Path):
        self.config = config
        self.base_dir = base_dir
        self.mihandle = MediaInfoHandler(config, base_dir)
        self.desc_generator = DescGenerator(self.config, self.base_dir)

    async def gather_prep(self, meta):
        # Initialize gather
        meta.update(
            is_dir=Path(meta["path"]).is_dir(),
            is_music=False,
            is_video=False,
            filelist=[],
            user_images=[],
            log_files=[],
            nfo_file="",
        )

        # Create temp directory
        tmp_dir = self.base_dir / "tmp" / f"{meta['uuid']}"
        await asyncio.to_thread(tmp_dir.mkdir, parents=True, exist_ok=True)

        log.debug(f"[cyan]ID: {meta['uuid']}")

        # File collection and initial processing
        analyzer = FileAnalyzer()
        meta = await analyzer.analyze(meta)

        # Skip processing if invalid media
        if meta.get("invalid_media"):
            return meta

        if meta["is_music"]:
            # Process Music
            music_prep = MusicProcessor(self.mihandle, self.base_dir)
            meta = await music_prep.process_music(meta)
            meta = await self.desc_generator.gen_desc(meta)
        else:
            # Process Video
            content = "pr0n" if meta["xxx"] else "video"
            console.print(f"[red]Processing as {content}")
            video_prep = VideoProcessor(self.config, self.mihandle, self.base_dir)
            meta = await video_prep.process_video(meta)
            meta = await self.desc_generator.gen_desc(meta)

        if not meta["xxx"] and not meta["is_music"]:
            integrator = MetaScraper(self.config, pm=ProgressManager.get_instance())
            meta = await integrator.integrate(meta)

        # Create Name
        if not meta.get("is_sports"):
            log.ddebug(
                f"[DEBUG-vc] pre-get_name keys: video_codec={'video_codec' in meta}, video_encode={'video_encode' in meta}, is_disc={meta.get('is_disc')}, type={meta.get('type')}"
            )
            (
                meta["name_notag"],
                meta["name"],
                meta["clean_name"],
                meta["potential_missing"],
            ) = await NameGenerator().get_name(meta)
        else:
            meta.setdefault("name_notag", meta.get("name", ""))
            meta.setdefault("clean_name", NameGenerator.clean_filename(meta.get("name", "")))
            meta.setdefault("potential_missing", [])

        return meta


class FileAnalyzer:
    """
    Analyzes a file or directory path, categorizes contents, and updates metadata.
    Handles both single files and directories recursively, with video priority.
    """

    def __init__(self):
        self.pm = ProgressManager.get_instance()

    async def analyze(self, meta: dict) -> dict:
        """Analyzes path dictates process route."""
        path = Path(meta["path"])
        meta.update(
            is_dir=path.is_dir(),
            is_music=False,
            is_video=False,
            filelist=[],
            user_images=[],
            log_files=[],
            nfo_file="",
            content_size=0,
        )

        if self.pm:
            self.pm.update(f"Analyzing {path.name}...")

        if meta["is_dir"]:
            meta = await self._analyze_directory(meta, path)
        else:
            meta = await self._analyze_file(meta, path)

        if self.pm:
            self.pm.update(f"Analysis complete for {path.name}")

        return meta

    @staticmethod
    def _read_text_bom_aware(path: Path) -> str:
        """Read text file without modifying it, handling common encodings."""
        raw = path.read_bytes()
        if raw[:2] == b"\xff\xfe":
            return raw.decode("utf-16-le", errors="ignore")
        if raw[:2] == b"\xfe\xff":
            return raw.decode("utf-16-be", errors="ignore")
        result = from_bytes(raw)
        if result is not None:
            best = result.best()
            if best is not None:
                return str(best)
        return raw.decode("utf-8", errors="ignore")

    async def _analyze_directory(self, meta: dict, path: Path) -> dict:
        """Async directory analysis."""
        all_files = []
        total_size = 0

        if self.pm:
            self.pm.update("Scanning directory...")

        async for file_path in self._async_iterdir(path):
            if file_path.is_file() and not file_path.name.startswith("."):
                all_files.append(str(file_path))
                total_size += file_path.stat().st_size
        if self.pm:
            self.pm.update(f"Scanned {len(all_files)} files in {path.name}")

        meta["content_size"] = total_size
        meta["filelist"] = sorted(all_files)  # Sort for consistency

        if self.pm:
            self.pm.update("Categorizing files...")

        # Categorize
        video_files = [f for f in all_files if Path(f).suffix.lower() in VIDEO_EXTS]
        music_files = [f for f in all_files if Path(f).suffix.lower() in MUSIC_EXTS]
        meta["user_images"] = [f for f in all_files if Path(f).suffix.lower() in IMAGE_EXTS]
        meta["log_files"] = [f for f in all_files if Path(f).suffix.lower() == LOG_EXT]
        foo_dr_files: list[str] = []
        foo_dr_content: list[tuple[str, str]] = []
        aucdtect_files: list[str] = []
        aucdtect_content: list[tuple[str, str]] = []
        for txt in all_files:
            if Path(txt).suffix.lower() == TXT_EXT:
                try:
                    if Path(txt).stat().st_size > 124 * 1024:
                        log.debug(f"[PREP] skip {txt}: size exceeds 124 KiB")
                        continue
                    text = await asyncio.to_thread(self._read_text_bom_aware, Path(txt))
                    lower = text.lower()
                    foo_match = any(k in lower for k in ("foo_dr", "foo dr", "dynamic range"))
                    auc_match = any(
                        k in lower for k in ("aucdtect", "au cd tect", "accuratecd", "cd quality")
                    )
                    log.ddebug(
                        f"{txt}  size={Path(txt).stat().st_size}  foo_dr={foo_match}  aucdtect={auc_match}\n"
                        f"{lower[:120]!r}"
                    )
                    if foo_match:
                        foo_dr_files.append(txt)
                        foo_dr_content.append((txt, text))
                    if auc_match:
                        aucdtect_files.append(txt)
                        aucdtect_content.append((txt, text))
                except OSError as e:
                    log.error(f"OSError reading {txt}: {e}")
                    continue
        meta["foo_dr"] = foo_dr_content
        meta["foo_dr_files"] = foo_dr_files
        meta["aucdtect"] = aucdtect_content
        meta["aucdtect_files"] = aucdtect_files
        nfo_files = [f for f in all_files if Path(f).suffix.lower() == NFO_EXT]
        base_name = path.name
        meta["nfo_file"] = next(
            (nfo for nfo in nfo_files if Path(nfo).name == f"{base_name}{NFO_EXT}"),
            nfo_files[0] if nfo_files else "",
        )

        # Prioritize video
        base_video = any(Path(f).parent == path for f in video_files)
        video_count = len(video_files)
        music_count = len(music_files)

        if video_count > 0 and (base_video or video_count > music_count * 2 or not meta.get("is_music")):
            meta["is_video"] = True
            meta["video_files"] = video_files
        elif music_count > 0:
            meta["is_music"] = True
            meta["music_files"] = music_files

        return meta

    async def _analyze_file(self, meta: dict, path: Path) -> dict:
        """Async single file analysis."""
        if self.pm:
            self.pm.update(f"Analyzing file {path.name}...")

        ext = path.suffix.lower()
        try:
            meta["content_size"] = path.stat().st_size
        except (OSError, FileNotFoundError):
            meta["content_size"] = 0

        meta["filelist"] = [str(path)]

        if ext in VIDEO_EXTS:
            meta["is_video"] = True
            meta["video_files"] = [str(path)]
            base_name = path.stem
            nfo_path = path.with_name(f"{base_name}{NFO_EXT}")
            meta["nfo_file"] = str(nfo_path) if nfo_path.exists() else ""
            if not meta["nfo_file"] and meta.get("nfo"):
                nfo_files = [str(f) for f in path.parent.iterdir() if f.suffix.lower() == NFO_EXT]
                meta["nfo_file"] = nfo_files[0] if nfo_files else ""
        elif ext in MUSIC_EXTS:
            meta["is_music"] = True
            meta["music_files"] = [str(path)]

        if not meta.get("is_video", False) and not meta.get("is_music", False):
            meta["invalid_media"] = True

        return meta

    async def _async_iterdir(self, path: Path):
        """Async generator for recursive iterdir to allow concurrency if needed."""
        loop = asyncio.get_running_loop()
        for file_path in await loop.run_in_executor(None, list, path.rglob("*")):
            yield file_path
