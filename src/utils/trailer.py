from __future__ import annotations

import asyncio
import contextlib
import random
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin

import niquests

from src.progress import ProgressManager
from src.utils.console import log


async def _run_ffmpeg(cmd: list[str], desc: str) -> tuple[int, str, str]:
    """Run ffmpeg asynchronously, streaming output and updating progress."""
    progress = ProgressManager.get_instance()
    if progress:
        progress.update(f"{desc}…")

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()

    stdout_str = stdout.decode(errors="ignore")
    stderr_str = stderr.decode(errors="ignore")

    if progress:
        progress.update(f"{desc} - {'Done' if proc.returncode == 0 else 'Failed'}")

    return proc.returncode, stdout_str, stderr_str


async def _resolve_m3u8(client: niquests.AsyncSession, url: str) -> str:
    content = await _fetch_m3u8(client, url)
    if not content:
        return url

    variants = _parse_master_playlist(content)
    if not variants:
        return url

    selected = _select_variant_url(variants)
    if not selected:
        return url

    return urljoin(url, selected)


async def _fetch_m3u8(client: niquests.AsyncSession, url: str) -> str | None:
    try:
        resp = await client.get(url, timeout=15.0)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        log.debug(f"Failed to fetch m3u8 master playlist: {e}")
        return None


def _parse_master_playlist(content: str) -> list[dict[str, int | str]]:
    variants: list[dict[str, int | str]] = []
    current_bandwidth: int | None = None
    current_resolution: str | None = None

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            if line.startswith("#EXT-X-STREAM-INF:"):
                current_bandwidth = None
                current_resolution = None
                bw_match = re.search(r"BANDWIDTH=(\d+)", line)
                if bw_match:
                    current_bandwidth = int(bw_match.group(1))
                res_match = re.search(r"RESOLUTION=(\d+x\d+)", line)
                if res_match:
                    current_resolution = res_match.group(1)
            continue

        if current_bandwidth is not None:
            variants.append(
                {
                    "url": line,
                    "bandwidth": current_bandwidth,
                    "resolution": current_resolution or "",
                }
            )
            current_bandwidth = None
            current_resolution = None

    return variants


def _select_variant_url(variants: list[dict[str, int | str]]) -> str | None:
    if not variants:
        return None

    def get_height(v: dict) -> int:
        res = v.get("resolution", "")
        if isinstance(res, str) and "x" in res:
            try:
                return int(res.split("x")[-1])
            except ValueError:
                pass
        return 0

    valid_video = [v for v in variants if get_height(v) > 0]

    if not valid_video:
        return variants[0].get("url")

    is_480 = [v for v in valid_video if get_height(v) == 480]
    if is_480:
        return min(is_480, key=lambda v: v.get("bandwidth", float("inf")))["url"]

    is_360_plus = [v for v in valid_video if get_height(v) >= 360]
    if is_360_plus:
        return min(is_360_plus, key=lambda v: (get_height(v), v.get("bandwidth", float("inf"))))["url"]

    return max(valid_video, key=lambda v: (get_height(v), -v.get("bandwidth", 0)))["url"]


async def trailer_to_webp(video_path: Path, output_dir: Path, config: dict) -> Path | None:
    """
    Convert a downloaded trailer to an animated WebP.

    Config keys (all under config['ADULT']['trailer']):
        - fps: int | None      → frames per second (default 20)
        - maxwidth: int | None → max width in pixels (default 800)
        - crop: bool | None    → enable cropdetect (default True)
        - sample: str | None   → time to sample for cropdetect (default "1")
        - frames: int | None   → # of frames to analyze for crop (default 12)
        - scale: float | None  → base scaling before maxwidth (default 1.0)

    Returns Path to trailer.webp or None on failure.
    """
    trailer_cfg = config.get("ADULT", {}).get("trailer", {})

    fps = int(trailer_cfg.get("fps") or 20)
    max_width = int(trailer_cfg.get("maxwidth") or 720)
    do_crop = trailer_cfg.get("crop", True) is not False
    sample_time = str(trailer_cfg.get("sample") or "1")
    crop_frames = int(trailer_cfg.get("frames") or 12)
    base_scale = float(trailer_cfg.get("scale") or 1.0)

    output_path = output_dir / "trailer.webp"

    processing_path = video_path
    cleanup_paths: list[Path] = []

    # Shorten long trailers
    try:
        dur_probe = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "csv=p=0", str(video_path),
        ]  # fmt:off
        ret, stdout, _ = await _run_ffmpeg(dur_probe, "Probing trailer duration")
        if ret == 0:
            duration = float(stdout.strip())
            if duration > 120:
                log.debug(f"Trailer duration {duration:.1f}s > 120s, shortening")
                selected_times = []
                attempts = 0
                max_attempts = 50
                safe_start = 5
                safe_end = duration - 5 - 4

                if safe_start < safe_end:
                    while len(selected_times) < 5 and attempts < max_attempts:
                        t = random.uniform(safe_start, safe_end)
                        if all(abs(t - existing) >= 5 for existing in selected_times):
                            selected_times.append(t)
                        attempts += 1

                if selected_times:
                    concatenated = output_dir / "trailer_concat.mp4"
                    concat_file = output_dir / "trailer_concat.txt"
                    temp_segments: list[Path] = []

                    try:
                        for i, start in enumerate(selected_times):
                            seg = output_dir / f"trailer_seg_{i}.mp4"
                            temp_segments.append(seg)
                            seg_cmd = [
                                "ffmpeg", "-v", "error", "-ss", str(start), "-t", "4",
                                "-i", str(video_path), "-c", "copy", "-y", str(seg),
                            ]  # fmt:off
                            ret, _, stderr = await _run_ffmpeg(
                                seg_cmd, f"Extracting trailer clip {i + 1}/{len(selected_times)}"
                            )
                            if ret != 0:
                                raise RuntimeError(f"Clip {i} failed: {stderr}")

                        with open(concat_file, "w") as f:
                            for seg in temp_segments:
                                f.write(f"file '{seg.absolute()}'\n")

                        concat_cmd = [
                            "ffmpeg", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat_file),
                            "-c", "copy", "-y", str(concatenated),
                        ]  # fmt:off
                        ret, _, stderr = await _run_ffmpeg(concat_cmd, "Concatenating trailer clips")
                        if ret != 0:
                            raise RuntimeError(f"Concat failed: {stderr}")

                        processing_path = concatenated
                        cleanup_paths.append(concatenated)
                    finally:
                        # Clean up intermediate segments immediately
                        for seg in temp_segments:
                            if seg.exists():
                                seg.unlink()
                        if concat_file.exists():
                            concat_file.unlink()
    except Exception as e:
        log.warning(f"Trailer clip extraction failed, using full trailer: {e}")
        processing_path = video_path
        for p in cleanup_paths:
            if p.exists():
                p.unlink()
        cleanup_paths.clear()

    # Crop Detect
    crop_params = None
    if do_crop:
        crop_cmd = [
            "ffmpeg", "-ss", sample_time, "-i", str(processing_path),
            "-vframes", str(crop_frames), "-vf", "cropdetect=24:16:0",
            "-f", "null", "-",
        ]  # fmt:off
        ret, _, stderr = await _run_ffmpeg(crop_cmd, "Detecting crop")
        if ret != 0:
            log.warning("cropdetect failed - proceeding without cropping")
        else:
            # Use Counter to find the most reliable crop box rather than just the first match
            crops = re.findall(r"crop=(\d+:\d+:\d+:\d+)", stderr)
            if crops:
                crop_params = Counter(crops).most_common(1)[0][0]
                w, h, x, y = map(int, crop_params.split(":"))
                log.debug(f"Detected crop: crop={crop_params} ({w}x{h}+{x}+{y})")
            else:
                log.warning("cropdetect found no crop - using full frame")

    # Final Dimensions
    if crop_params:
        crop_w, crop_h, _, _ = map(int, crop_params.split(":"))
        new_w = int(crop_w * base_scale)
        new_h = int(crop_h * base_scale)
        log.debug(f"Cropping from: {crop_w}x{crop_h} -> scaled to {new_w}x{new_h}")
    else:
        probe_cmd = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x",
            str(processing_path),
        ]  # fmt:off
        ret, stdout, _ = await _run_ffmpeg(probe_cmd, "Probing resolution")
        if ret == 0 and "x" in stdout.strip():
            orig_w, orig_h = map(int, stdout.strip().split("x"))
            new_w = int(orig_w * base_scale)
            new_h = int(orig_h * base_scale)
            log.debug(f"Using original dimensions: {orig_w}x{orig_h} -> scaled to {new_w}x{new_h}")
        else:
            log.error("Failed to probe video size - aborting WebP conversion")
            return None

    if new_w > max_width:
        ratio = max_width / new_w
        new_w = int(new_w * ratio)
        new_h = int(new_h * ratio)

    # Even dimensions required by libwebp
    new_w = new_w - (new_w % 2)
    new_h = new_h - (new_h % 2)
    if new_w < 2:
        new_w = 2
    if new_h < 2:
        new_h = 2

    log.debug(f"Final WebP size: {new_w}x{new_h}, FPS: {fps}")

    vf = []
    if do_crop and crop_params:
        vf.append(f"crop={crop_params}")
    vf.append(f"scale={new_w}:{new_h}")
    vf.append(f"fps={fps}")

    encode_cmd = [
        "ffmpeg", "-v", "error", "-i", str(processing_path), "-an", "-vf", ",".join(vf),
        "-loop", "0", "-c:v", "libwebp", "-quality", "80", "-compression_level", "6",
        "-y", str(output_path),
    ]  # fmt:off

    ret, _, stderr = await _run_ffmpeg(encode_cmd, "Converting Trailer to WebP")

    # Cleanup shortening temp files after encode
    for p in cleanup_paths:
        if p.exists():
            with contextlib.suppress(Exception):
                p.unlink()

    if ret != 0:
        log.error(f"WebP encoding failed: {stderr.splitlines()[-3:]}")
        return None

    log.info(f"Trailer WebP saved: {output_path}")
    return str(output_path)


async def create_trailer(video_path: Path, output_dir: Path, config: dict) -> Path | None:
    """
    Create a 20-second trailer by taking 4 random 5-second sections from the main video.
    """
    probe_cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", str(video_path),
    ]  # fmt:off
    ret, stdout, _ = await _run_ffmpeg(probe_cmd, "Probing video duration")
    if ret != 0:
        log.error("Failed to get video duration")
        return None

    try:
        duration = float(stdout.strip())
    except ValueError:
        log.error(f"Could not parse video duration: {stdout.strip()}")
        return None

    if duration <= 40:
        log.warning(f"Video too short ({duration}s) to create trailer with credit avoidance")
        return await trailer_to_webp(video_path, output_dir, config)

    start_range = 20
    end_range = duration - 20 - 5

    if start_range >= end_range:
        log.warning("Video too short to safely avoid credits")
        return await trailer_to_webp(video_path, output_dir, config)

    selected_times = []
    attempts = 0
    max_attempts = 50

    while len(selected_times) < 4 and attempts < max_attempts:
        start_time = random.uniform(start_range, end_range)
        if all(abs(start_time - existing_time) >= 6 for existing_time in selected_times):
            selected_times.append(start_time)
        attempts += 1

    if len(selected_times) < 4:
        log.warning(f"Could only select {len(selected_times)} segments, expected 4")
        if not selected_times:
            return None

    selected_times.sort()

    concat_file = output_dir / "concat_list.txt"
    concatenated = output_dir / "temp_concatenated.mp4"
    temp_segments: list[Path] = []

    try:
        for i, start_time in enumerate(selected_times):
            temp_path = output_dir / f"temp_segment_{i}.mp4"
            temp_segments.append(temp_path)

            # Added -v error
            segment_cmd = [
                "ffmpeg", "-v", "error", "-ss", str(start_time), "-t", "5",
                "-i", str(video_path), "-c", "copy", "-y", str(temp_path),
            ]  # fmt:off

            ret, _, stderr = await _run_ffmpeg(segment_cmd, f"Creating segment {i + 1}/4")
            if ret != 0:
                log.error(f"Failed to create segment {i + 1}: {stderr}")
                return None

        with open(concat_file, "w") as f:
            for temp_path in temp_segments:
                f.write(f"file '{temp_path.absolute()}'\n")

        # Added -v error
        concat_cmd = [
            "ffmpeg", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-c", "copy", "-y", str(concatenated),
        ]  # fmt:off

        ret, _, stderr = await _run_ffmpeg(concat_cmd, "Concatenating segments")
        if ret != 0:
            log.error(f"Failed to concatenate segments: {stderr}")
            return None

        webp_path = await trailer_to_webp(concatenated, output_dir, config)
        return str(webp_path) if webp_path else None

    finally:
        # Robust Cleanup: Ensure temp files are deleted even if an exception occurs
        if concatenated.exists():
            concatenated.unlink()
        for temp_path in temp_segments:
            if temp_path.exists():
                temp_path.unlink()
        if concat_file.exists():
            concat_file.unlink()


async def download_trailer(
    client: niquests.AsyncSession,
    url: str,
    local_path: Path,
    *,
    chunk_size: int = 1024 * 256,  # 256 KiB
) -> Path | None:
    """Stream-download a video file and save it to *local_path*."""
    if not url:
        return None

    try:
        head_resp = await client.head(url, timeout=10.0)
        content_type = head_resp.headers.get("Content-Type", "").lower()
        is_m3u8 = "m3u" in content_type or url.lower().endswith((".m3u8", ".m3u"))
        local_path.parent.mkdir(parents=True, exist_ok=True)

        if is_m3u8:
            log.debug(f"Detected m3u8 stream: {url}. Resolving best variant.")
            resolved_url = await _resolve_m3u8(client, url)
            log.debug(f"Using resolved m3u8 URL: {resolved_url}")

            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-v", "error", "-i", resolved_url, "-map", "0:v", "-an",
                "-c", "copy", "-protocol_whitelist", "file,http,https,tcp,tls,crypto", "-y",
                str(local_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )  # fmt:off
            _stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                log.warning(f"ffmpeg failed to download {url}: {stderr.decode(errors='ignore').strip()}")
                return None
            log.debug("Trailer Downloaded Successfully.")
        else:
            log.debug(f"Detected direct video: {url}. Using niquests stream.")
            resp = await client.get(url, stream=True)
            resp.raise_for_status()
            with local_path.open("wb") as f:
                # Note: Kept original await syntax, assuming niquests specific implementation
                async for chunk in await resp.iter_content(chunk_size):
                    f.write(chunk)
            log.debug("Trailer Downloaded Successfully.")

        if local_path.exists() and local_path.stat().st_size > 0:
            return local_path
        else:
            log.warning(f"Downloaded file {local_path} is empty or missing.")
            return None
    except Exception as e:
        log.warning(f"Failed to download trailer {url}: {e}")
        return None
