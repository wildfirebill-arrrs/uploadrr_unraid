from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import bencodepy as bencode
from torf import Torrent

from src.cleanrr import running_subprocesses
from src.progress import ProgressManager
from src.utils.console import console, log
from src.utils.mkbrr import ensure_mkbrr, get_mkbrr_path


class TorrentMaker:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.tracker = "https://fake.tracker"

    async def create(self, meta: dict, path: Path, piece_size_max: int | None = None) -> Torrent:
        progress = ProgressManager.get_instance()
        if not progress:
            log.error("ProgressManager not initialized")
            raise RuntimeError("ProgressManager not initialized")

        try:
            piece_size_max = int(piece_size_max) if piece_size_max is not None else 0
            path = self._prepare_path(meta, path)
            exclude, include = self._get_filters(meta)

            torrent = self._initialize(path, exclude, include)
            piece_size, piece_size_text = self._calculate_piece_size(torrent.size, piece_size_max)

            progress.update(f"Creating .torrent with piece size {piece_size_text}...")

            torrent_creation = meta.get("torrent_creation")
            log.debug(f"Tool: {torrent_creation}")
            output_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / "BASE.torrent"
            output_path.parent.mkdir(parents=True, exist_ok=True)

            log.debug(f"Path used for torrent: {path}")
            log.debug(f"Exclude patterns: {exclude}")
            log.debug(f"Include patterns: {include}")
            log.debug(f"Output path: {output_path}")
            log.debug(f"Using method: {torrent_creation}")

            await self._generate(
                torrent,
                path,
                output_path,
                piece_size,
                torrent_creation,
                piece_size_max,
                meta.get("deep_debug", False),
            )

            progress.update(".torrent created")
            return torrent
        except ValueError as e:
            log.error(f"Invalid input: {e}")
            raise
        except Exception as e:
            log.error(f"Torrent creation failed: {e}")
            raise

    def _prepare_path(self, meta: dict, path: Path) -> Path:
        if not meta.get("full_dir") and meta.get("is_dir"):
            if meta.get("category") == "MOVIE" and not meta.get("is_disc") and meta.get("video"):
                return Path(meta["video"])
            root_videos = [
                vf
                for vf in meta.get("video_files", [])
                if Path(vf).parent == path and "sample" not in Path(vf).name.lower()
            ]
            if len(root_videos) == 1:
                return Path(root_videos[0])
        return path

    def _get_filters(self, meta: dict) -> tuple[list[str], list[str]]:
        if any(meta.get(k) for k in ["full_dir", "is_disc", "is_music", "xpack"]):
            desc = meta["uuid"] if meta.get("is_dir", False) else Path(meta["uuid"]).stem
            return ["._*", "description.txt", f"{desc}.txt"], []
        return ["*.*", "*sample.*", "._*"], ["*.mkv", "*.mp4", "*.ts"]

    def _initialize(self, path: Path, exclude: list[str], include: list[str]) -> Torrent:
        return Torrent(
            path=path,
            trackers=self.tracker,
            source="Uploadrr",
            private=True,
            exclude_globs=exclude or [],
            include_globs=include or [],
            creation_date=datetime.now(),
            comment="Created by Uploadrr",
            created_by="Created by Uploadrr",
        )

    def _calculate_piece_size(self, file_size: int, piece_size_max: int) -> tuple[int, str]:
        size_thresholds = [
            (268435456, 18, "256KiB"),  # 256 MiB
            (1073741824, 19, "512KiB"),  # 1 GiB
            (2147483648, 20, "1MiB"),  # 2 GiB
            (4294967296, 21, "2MiB"),  # 4 GiB
            (8589934592, 22, "4MiB"),  # 8 GiB
            (17179869184, 23, "8MiB"),  # 16 GiB
        ]

        for _threshold, size, text in size_thresholds:
            if file_size < _threshold:
                if piece_size_max > 0 and size > piece_size_max:
                    size = piece_size_max
                    text = f"{2 ** (size - 10)}MiB" if size >= 20 else f"{2**size}KiB"
                return size, text

        size = 24
        text = "16MiB"
        if piece_size_max > 0 and size > piece_size_max:
            size = piece_size_max
            text = f"{2 ** (size - 10)}MiB" if size >= 20 else f"{2**size}KiB"
        return size, text

    async def _generate(
        self,
        torrent: Torrent,
        path: Path,
        output_path: Path,
        piece_size: int,
        method: str | None,
        piece_size_max: int = 0,
        debug: bool = False,
    ) -> None:
        exclude_globs = torrent.exclude_globs
        include_globs = torrent.include_globs
        threads = self.config["DEFAULT"].get("threads")
        if threads == "max":
            threads = os.cpu_count()

        output_path = output_path.resolve()
        progress = ProgressManager.get_instance()

        if method == "torrenttools":
            if self._check_tool("torrenttools"):
                args = [
                    "torrenttools",
                    "create",
                    "-a",
                    self.tracker,
                    "--private",
                    "on",
                    "--piece-size",
                    str(2**piece_size),
                    "--created-by",
                    "Created by Uploadrr",
                    "--no-cross-seed",
                    "-o",
                    str(output_path),
                ]
                if threads:
                    args.extend(["--threads", str(threads)])
                for pattern in exclude_globs:
                    if pattern != "._*":
                        args.extend(["--exclude", pattern])
                for pattern in include_globs:
                    args.extend(["--include", pattern])
                args.append(str(path))
                await self._hash_spinner(args, "torrenttools", debug=debug)

                # Validate output torrent
                if not output_path.exists() or output_path.stat().st_size == 0:
                    log.warning(f"Empty or missing torrent file at {output_path}. Falling back to torf...")
                    await self.use_torf(torrent, path, output_path, piece_size)
                    return
            else:
                progress.update("torrenttools not found in PATH, falling back to torf...")
                await self.use_torf(torrent, path, output_path, piece_size)

        elif method == "mktorrent":
            if self._check_tool("mktorrent"):
                if output_path.exists():
                    try:
                        output_path.unlink()
                    except OSError as e:
                        log.error(f"Failed to remove {output_path} ({e}), falling back to torf...")
                        await self.use_torf(torrent, path, output_path, piece_size)
                        return

                if Path(path).is_dir() and exclude_globs:
                    try:
                        with tempfile.TemporaryDirectory() as temp_dir:
                            temp_path = Path(temp_dir)
                            original_name = Path(path).name
                            target_dir = temp_path / original_name
                            target_dir.mkdir()
                            self._filter_directory_with_symlinks(
                                path, target_dir, exclude_globs, include_globs
                            )

                            # Validate temporary directory contents
                            temp_files = list(target_dir.rglob("*"))
                            if not temp_files:
                                log.warning(
                                    f"No files in temp directory {target_dir} after filtering. "
                                    "Falling back to torf..."
                                )
                                await self.use_torf(torrent, path, output_path, piece_size)
                                return

                            args = [
                                "mktorrent",
                                "-a",
                                self.tracker,
                                "-p",
                                "-l",
                                str(piece_size),
                                "-s",
                                "Uploadrr",
                                "-o",
                                str(output_path),
                                target_dir,
                            ]
                            await self._hash_spinner(args, "mktorrent", debug=debug)

                            # Validate output torrent
                            if not output_path.exists() or output_path.stat().st_size == 0:
                                log.warning(
                                    f"Empty or missing torrent file at {output_path}. Falling back to torf..."
                                )
                                await self.use_torf(torrent, path, output_path, piece_size)
                                return

                    except (OSError, subprocess.CalledProcessError) as e:
                        log.error(f"mktorrent failed ({e}), falling back to torf...")
                        await self.use_torf(torrent, path, output_path, piece_size)
                else:
                    args = [
                        "mktorrent",
                        "-a",
                        self.tracker,
                        "-p",
                        "-l",
                        str(piece_size),
                        "-s",
                        "Uploadrr",
                        "-o",
                        str(output_path),
                        str(path),
                    ]
                    try:
                        await self._hash_spinner(args, "mktorrent", debug=debug)

                        if not output_path.exists() or output_path.stat().st_size == 0:
                            log.warning(
                                f"Empty or missing torrent file at {output_path}. Falling back to torf..."
                            )
                            await self.use_torf(torrent, path, output_path, piece_size)
                            return

                    except subprocess.CalledProcessError as e:
                        log.error(f"mktorrent failed ({e}), falling back to torf...")
                        await self.use_torf(torrent, path, output_path, piece_size)
            else:
                progress.update("mktorrent not found in PATH, falling back to torf...")
                await self.use_torf(torrent, path, output_path, piece_size)

        elif method == "mkbrr":
            mkbrr_path = ensure_mkbrr()
            if not mkbrr_path:
                progress.update("mkbrr not found and could not be downloaded, falling back to torf...")
                await self.use_torf(torrent, path, output_path, piece_size)
                return

            args = [
                str(mkbrr_path),
                "create",
                str(path),
                "-t",
                self.tracker,
                "--private",
                "--source",
                "Uploadrr",
                "--comment",
                "Created by Uploadrr",
                "--skip-prefix",
                "-o",
                str(output_path),
            ]
            if threads:
                args.extend(["--workers", str(threads)])
            for pattern in exclude_globs:
                if pattern != "._*":
                    args.extend(["--exclude", pattern])
            for pattern in include_globs:
                args.extend(["--include", pattern])
            if piece_size_max > 0:
                args.extend(["--max-piece-length", str(piece_size)])
            if debug:
                args.insert(2, "-v")

            try:
                await self._hash_spinner(args, "mkbrr", debug=debug)
            except Exception:
                log.warning("mkbrr failed, falling back to torf...")
                await self.use_torf(torrent, path, output_path, piece_size)
                return

            if not output_path.exists() or output_path.stat().st_size == 0:
                log.warning(f"Empty or missing torrent file at {output_path}. Falling back to torf...")
                await self.use_torf(torrent, path, output_path, piece_size)

        else:
            await self.use_torf(torrent, path, output_path, piece_size)

    async def use_torf(self, torrent: Torrent, path: Path, output_path: Path, piece_size: int) -> None:
        progress = ProgressManager.get_instance()

        torrent.piece_size = 2**piece_size
        torrent.piece_size_max = 16777216
        progress.update("Starting torf hashing...")
        await asyncio.to_thread(torrent.generate, callback=self._torf_callback, interval=5)
        await asyncio.to_thread(torrent.write, output_path, overwrite=True)
        await asyncio.to_thread(torrent.verify_filesize, path)
        progress.update("Torf hashing complete")

    async def regenerate(
        self,
        meta: dict,
        torrent_path: Path,
        new_piece_size: int,
    ) -> None:
        progress = ProgressManager.get_instance()
        if not progress:
            log.error("ProgressManager not initialized")
            return

        path = Path(meta.get("path", ""))
        if not path.exists():
            log.error(f"Data path {path} does not exist for regeneration.")
            return

        exclude, include = self._get_filters(meta)
        torrent = self._initialize(path, exclude, include)

        try:
            existing_torrent = Torrent.read(torrent_path)
            torrent.trackers = existing_torrent.trackers
            torrent.source = existing_torrent.source
            torrent.comment = existing_torrent.comment
            torrent.private = existing_torrent.private
        except Exception as e:
            log.warning(f"Could not read existing torrent metadata, using defaults: {e}")

        method = meta.get("torrent_creation")
        progress.update(f"Regenerating torrent with piece size {2**new_piece_size} bytes...")

        await self._generate(
            torrent,
            path,
            torrent_path,
            new_piece_size,
            method,
            piece_size_max=0,
            debug=meta.get("deep_debug", False),
        )
        progress.update("Torrent regeneration complete")

    def _filter_directory_with_symlinks(
        self, source_path: Path, dest_path: Path, exclude_globs: list[str], include_globs: list[str]
    ) -> None:
        source_path = Path(source_path)
        dest_path = Path(dest_path)

        all_files = list(source_path.rglob("*")) + list(source_path.rglob(".*"))
        log.debug(f"Found {len(all_files)} items in {source_path}: {[str(f) for f in all_files]}")

        included_files = []
        for item in all_files:
            if not item.is_file():
                log.debug(f"Skipping {item.name} (not a file, likely a directory)")
                continue

            relative_path = str(item.relative_to(source_path))
            rel_path_lower = relative_path.casefold()

            # torf semantics: include overrides exclude
            if include_globs and any(
                fnmatch.fnmatch(rel_path_lower, pat.casefold()) for pat in include_globs
            ):
                pass  # explicitly included, don't exclude
            elif any(fnmatch.fnmatch(rel_path_lower, pat.casefold()) for pat in exclude_globs):
                log.debug(f"Excluding {relative_path} (matches exclude pattern)")
                continue

            dest_item = dest_path / relative_path
            dest_item.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.symlink(item, dest_item)
                included_files.append(relative_path)
            except OSError as e:
                log.warning(f"Failed to create symlink for {relative_path}: {e}")
                continue

        log.debug(f"Included {len(included_files)} files in {dest_path}: {included_files}")
        if not included_files:
            log.warning(
                "No files included in temporary directory. "
                "Check exclude/include patterns or source directory contents."
            )

    @staticmethod
    def _is_progress_line(line: str) -> bool:
        return "Hashing pieces" in line

    async def _hash_spinner(self, args: list[str | Path], tool_name: str, *, debug: bool = False) -> None:
        progress = ProgressManager.get_instance()
        if not progress:
            log.error("ProgressManager not initialized")
            return

        args = [str(a) for a in args]
        log.ddebug(f"Starting {tool_name} with args: {args}")
        process = None
        combined_output: list[str] = []
        try:
            progress.update(f"Hashing with {tool_name}...")
            process = await asyncio.create_subprocess_exec(
                *args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )
            running_subprocesses.add(process)
            log.ddebug(f"{tool_name} process started with PID: {process.pid}")
            start_time = time.time()
            assert process.stdout is not None
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace")
                combined_output.append(decoded)
                log.ddebug(f"[{tool_name}] {decoded.rstrip()}")
                if debug or self._is_progress_line(decoded):
                    with progress.pause_for_interaction():
                        console.print(decoded, end="")
            await process.wait()
            end_time = time.time()
            elapsed_time = end_time - start_time
            log.debug(f"Hashed in {elapsed_time:.2f} seconds")
            if process.returncode != 0:
                stderr_text = "".join(combined_output)
                log.error(f"[bold red]{tool_name} failed with code {process.returncode}: {stderr_text}")
                raise subprocess.CalledProcessError(process.returncode, args, stderr=stderr_text.encode())
        except FileNotFoundError:
            log.error(f"[bold red]{tool_name} not found in system PATH")
            raise
        except asyncio.CancelledError:
            log.debug(f"{tool_name} cancelled by SIGINT")
            if process and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            raise
        except Exception as e:
            log.error(f"Unexpected error in {tool_name}: {e}")
            raise
        finally:
            if process:
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=2)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
                        log.debug(f"{tool_name} process forcefully terminated")
                running_subprocesses.discard(process)
            progress.update("Hashing complete")

    def _check_tool(self, tool_name: str) -> bool:
        if tool_name == "mkbrr":
            return get_mkbrr_path() is not None
        return shutil.which(tool_name) is not None

    def _torf_callback(self, torrent: Torrent, filepath: str, pieces_done: int, pieces_total: int) -> None:
        progress = ProgressManager.get_instance()
        if not progress:
            return

        percentage = (pieces_done / pieces_total) * 100 if pieces_total > 0 else 0
        elapsed_time = time.time() - getattr(self, "start_time", time.time())
        self.start_time = time.time()

        progress.update(f"Hashing with torf: {percentage:.1f}% ({pieces_done}/{pieces_total} pieces)")

        if pieces_done >= pieces_total:
            log.debug(f"Hashed in {elapsed_time:.2f} seconds")

    async def create_random_torrents(self, base_dir: Path, uuid: str, num: int, path: Path) -> None:
        manual_name = re.sub(r"[^0-9a-zA-Z\[\]\'\-]+", ".", Path(path).name)
        base_path = Path(base_dir) / "tmp" / uuid / "BASE.torrent"
        base_torrent = Torrent.read(str(base_path))

        tasks = []
        for i in range(1, int(num) + 1):
            new_torrent = base_torrent.copy()
            new_torrent.metainfo["info"]["entropy"] = random.randint(1, 999999)
            output_path = Path(base_dir) / "tmp" / uuid / f"[RAND-{i}]{manual_name}.torrent"
            tasks.append(asyncio.to_thread(new_torrent.write, str(output_path), overwrite=True))

        await asyncio.gather(*tasks)

    async def from_existing(self, torrentpath: Path, base_dir: Path, uuid: str) -> None:
        if not Path(torrentpath).exists():
            return

        base_torrent = Torrent.read(torrentpath)
        base_torrent.creation_date = datetime.now()
        base_torrent.trackers = [self.tracker]
        base_torrent.comment = "Created by Uploadrr"
        base_torrent.created_by = "Created by Uploadrr"
        base_torrent.source = "Uploadrr"
        base_torrent.private = True

        valid_info_keys = {"files", "length", "name", "piece length", "pieces", "private", "source"}
        valid_meta_keys = {"announce", "comment", "creation date", "created by", "encoding", "info"}

        base_torrent.metainfo["info"] = {
            k: v for k, v in base_torrent.metainfo["info"].items() if k in valid_info_keys
        }

        for key in list(base_torrent.metainfo.keys()):
            if key not in valid_meta_keys:
                del base_torrent.metainfo[key]

        output_path = base_dir / "tmp" / uuid / "BASE.torrent"
        await asyncio.to_thread(base_torrent.write, str(output_path), overwrite=True)


async def edit_torrent(
    meta: dict | None = None,
    torrent_path: Path | None = None,
    tracker: str | None = None,
    source_flag: str | None = None,
    announce_url: str | None = None,
    comment: str | None = None,
) -> Path:
    try:
        if torrent_path is not None:
            existing = Path(torrent_path)
            if not existing.exists():
                raise FileNotFoundError(f"Torrent not found at {existing}")
        elif meta is not None:
            torrent_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
            existing = torrent_path / "BASE.torrent"
            if not existing.exists():
                if meta.get("nohash", False):
                    log.debug(f"Skipping torrent edit — nohash mode, no BASE.torrent at {existing}")
                    if tracker is not None:
                        return torrent_path / f"[{tracker}]{meta['clean_name']}.torrent"
                    return existing
                log.error(f"Base torrent not found at {existing}")
                raise FileNotFoundError(f"Base torrent not found at {existing}")
        else:
            raise ValueError("Either 'meta' or 'torrent_path' must be provided")

        if meta is not None and tracker is not None:
            output_dir = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
            output_path = output_dir / f"[{tracker}]{meta['clean_name']}.torrent"
            if sys.platform == "win32":
                output_path = Path(f"\\\\?\\{output_path.resolve()}")
        elif torrent_path is not None:
            input_path = Path(torrent_path)
            output_path = input_path
        else:
            raise ValueError("Cannot determine output path: need meta+tracker or torrent_path")

        new_torrent = Torrent.read(existing)

        if meta is not None:
            valid_meta_keys = {"creation date", "encoding", "info"}
            for key in list(new_torrent.metainfo.keys()):
                if key not in valid_meta_keys:
                    new_torrent.metainfo.pop(key, None)
            new_torrent.metainfo["created by"] = "Uploadrr"

        if announce_url is not None:
            new_torrent.metainfo["announce"] = announce_url.strip()

        if source_flag is not None:
            new_torrent.metainfo["info"]["source"] = source_flag

        if comment is not None:
            if comment == "":
                new_torrent.metainfo.pop("comment", None)
            else:
                new_torrent.metainfo["comment"] = comment

        Torrent.copy(new_torrent).write(output_path, overwrite=True)
        return output_path

    except Exception as e:
        log.error(f"Failed to edit torrent: {e}")
        raise


async def handle_torrent_creation(config: dict, meta: dict) -> dict:
    maketorrent = TorrentMaker(config)
    torrent_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / "BASE.torrent"
    rehash = meta.get("rehash", False)
    nohash = meta.get("nohash", False)
    randomized = int(meta.get("randomized", 0))
    piece_size_max = meta.get("piece_size_max", 0)
    path = Path(meta["path"])

    if nohash:
        meta["client"] = "none"
    else:
        if rehash or not torrent_path.exists():
            if not rehash:
                from src.clients import Clients

                client = Clients(config)
                reuse_torrent = await client.find_existing_torrent(meta)
                if reuse_torrent:
                    await maketorrent.from_existing(reuse_torrent, Path(meta["base_dir"]), meta["uuid"])
                    return meta
            await maketorrent.create(meta, path, piece_size_max)

    if randomized >= 1:
        await maketorrent.create_random_torrents(Path(meta["base_dir"]), meta["uuid"], randomized, path)
    return meta


def extract_size_from_torrent(base_dir: str, uuid: str) -> int:
    base_torrent = Path(base_dir) / "tmp" / f"{uuid}" / "BASE.torrent"
    try:
        if base_torrent.exists():
            with open(base_torrent, "rb") as f:
                torrent_data = bencode.decode(f.read())
            info = torrent_data[b"info"]
            return sum(file[b"length"] for file in info[b"files"]) if b"files" in info else info[b"length"]
        return 0
    except (FileNotFoundError, KeyError, bencode.BencodeDecodeError) as e:
        log.debug(f"Could not extract size from torrent: {e}")
        return 0


def get_infohash(torrent_path: Path) -> str:
    with open(torrent_path, "rb") as f:
        torrent_data = bencode.decode(f.read())
    return hashlib.sha1(bencode.encode(torrent_data[b"info"])).hexdigest()
