from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path

import niquests
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from src.utils.console import log

from .base import BaseImageHost

_SERVICE_DOMAIN = "www.imagebam.com"
_UPLOAD_SESSION_URL = f"https://{_SERVICE_DOMAIN}/upload/session"
_UPLOAD_URL = f"https://{_SERVICE_DOMAIN}/upload"
_MAX_FILE_SIZE = 10_485_760  # 10 MiB


async def _validate_for_upload(filepath: str, max_size: int) -> tuple[bool, str]:
    fpath = Path(filepath)
    if not await asyncio.to_thread(fpath.exists):
        return (False, f"File not found: {filepath}")
    size = await asyncio.to_thread(lambda: fpath.stat().st_size)
    if size > max_size:
        return (False, f"File is larger than {max_size} bytes")
    return (True, "")


class IMAGEBAM(BaseImageHost):
    adult_only = False

    def __init__(self, config):
        super().__init__(config)
        self.host_name = "imagebam"
        self.host_domain = _SERVICE_DOMAIN

    async def upload(self, client, image_path, title=None, adult=False):
        results = await self.upload_batch(client, [image_path], title=title, adult=adult)
        if results:
            return results[0]
        raise ValueError("Imagebam upload failed")

    async def upload_batch(self, client, image_paths, title=None, adult=False):
        max_retries = 3
        last_error = ""

        for attempt in range(1, max_retries + 1):
            try:
                return await self._do_upload_batch(client, image_paths, title=title, adult=adult)
            except (niquests.exceptions.RequestException, RuntimeError, ValueError) as e:
                log.warning(
                    f"[imagebam] Batch attempt {attempt}/{max_retries} error: {type(e).__name__}: {e}"
                )
                last_error = str(e)
                if attempt < max_retries:
                    await asyncio.sleep(2.0)

        log.error(f"[imagebam] Batch upload failed after {max_retries} attempts. Last: {last_error}")
        return [None] * len(image_paths)

    async def _do_upload_batch(self, client, image_paths, title=None, adult=False):
        resp = await client.get(f"https://{_SERVICE_DOMAIN}/")
        resp.raise_for_status()
        tree = HTMLParser(resp.text)
        csrf_token = ""
        for meta in tree.css("meta[name='csrf-token']"):
            csrf_token = meta.attributes.get("content", "")
            if csrf_token:
                break
        if not csrf_token:
            raise RuntimeError("Couldn't find CSRF token in HTML head")

        content_type = "nsfw" if adult else "sfw"
        session_resp = await client.post(
            _UPLOAD_SESSION_URL,
            data={
                "thumbnail_size": "4",
                "content_type": content_type,
                "comments_enabled": "false",
                "gallery": "true",
                "gallery_title": title or "Untitled",
            },
            headers={"X-CSRF-Token": csrf_token},
        )
        session_resp.raise_for_status()
        session_data = session_resp.json()
        if not isinstance(session_data, dict):
            raise RuntimeError(f"Unexpected session response: {session_data!r}")
        encrypted_payload = session_data.get("data")
        session_id = session_data.get("session")
        if not encrypted_payload or not session_id:
            raise RuntimeError(f"Missing session fields: {session_data!r}")

        read_results = await asyncio.gather(*(asyncio.to_thread(self._read_bytes, p) for p in image_paths))

        async def _upload_one(idx: int, filepath: str, content: bytes) -> dict | None:
            fname = Path(filepath).name
            mimetype = mimetypes.guess_type(filepath)[0] or "image/png"
            try:
                upload_resp = await client.post(
                    _UPLOAD_URL,
                    data={"data": encrypted_payload, "_token": csrf_token},
                    files={f"files[{idx}]": (fname, content, mimetype)},
                    headers={"X-CSRF-Token": csrf_token},
                )
                upload_resp.raise_for_status()
                upload_data = upload_resp.json()
                if not isinstance(upload_data, dict) or "success" not in upload_data:
                    raise RuntimeError(f"Unexpected upload response: {upload_data!r}")
            except niquests.exceptions.RequestException as e:
                log.debug(f"[imagebam] Upload {idx} ({fname}) failed: {e}")
                return None

            try:
                result_resp = await client.get(upload_data["success"], timeout=30)
                result_resp.raise_for_status()
            except niquests.exceptions.RequestException as e:
                log.debug(f"[imagebam] Result fetch for {fname} failed: {e}")
                return None

            result_tree = HTMLParser(result_resp.text)
            for item in result_tree.css("ul.images li.image"):
                title_node = item.css_first("span.title")
                thumb_link = item.css_first("a.thumbnail")
                thumb_img = item.css_first("a.thumbnail img")
                if not title_node or not thumb_link or not thumb_img:
                    continue
                filename = title_node.text(strip=True)
                img_url = thumb_img.attributes.get("src", "")
                web_url = thumb_link.attributes.get("href", "")
                raw_url = (
                    img_url.replace("thumbs4.imagebam.com", "images4.imagebam.com").replace("_t.png", ".png")
                    if img_url
                    else ""
                )
                if filename and img_url:
                    return {
                        "img_url": img_url,
                        "raw_url": raw_url,
                        "web_url": web_url,
                        "local_file": filename,
                    }
            log.debug(f"[imagebam] Image result not found in response page for {fname}")
            return None

        ordered_results: list[dict | None] = []
        for i, (filepath, (_, content, error)) in enumerate(zip(image_paths, read_results)):
            if error:
                log.debug(f"[imagebam] Skipping {Path(filepath).name}: {error}")
                ordered_results.append(None)
            else:
                ordered_results.append(await _upload_one(i, filepath, content))

        return ordered_results

    @staticmethod
    def _read_bytes(filepath: str) -> tuple[str, bytes, str]:
        fpath = Path(filepath)
        if not fpath.exists():
            return (filepath, b"", f"File not found: {filepath}")
        content = fpath.read_bytes()
        return (filepath, content, "")
