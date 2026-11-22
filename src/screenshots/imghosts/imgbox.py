from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path

import niquests
from selectolax.parser import HTMLParser

from src.utils.console import log

from .base import BaseImageHost

# ported from pyimbox (https://codeberg.org/plotski/pyimgbox/src/tag/v1.0.7) only needed partial
# this drops httpx and bs4 from this project entirely + allows for better error handling
_SERVICE_DOMAIN = "imgbox.com"
_TOKEN_URL = f"https://{_SERVICE_DOMAIN}/ajax/token/generate"
_PROCESS_URL = f"https://{_SERVICE_DOMAIN}/upload/process"
_MAX_FILE_SIZE = 10_485_760  # 10 MiB
_MAX_TITLE_LENGTH = 100  # Gallery Creation Error if > 100 chars

_THUMBNAIL_SIZES_KEEP_ASPECT = {
    100: "100r",
    150: "150r",
    200: "200r",
    250: "250r",
    300: "300r",
    350: "350r",
    500: "500r",
    800: "800r",
}

_CONTENT_TYPES = {"family": "1", "adult": "2"}


def _find_closest_thumb_width(width: int) -> int:
    return min(_THUMBNAIL_SIZES_KEEP_ASPECT, key=lambda x: abs(x - width))


class IMGBOX(BaseImageHost):
    def __init__(self, config):
        super().__init__(config)
        self.host_name = "imgbox"
        self.host_domain = _SERVICE_DOMAIN

    async def upload(self, client, image_path, title=None, adult=False):
        gallery = _ImgboxGallery(client, title=title or "Untitled", thumb_width=350, adult=adult)
        result = await gallery.add(image_path)
        if result["success"]:
            return {
                "img_url": result["thumbnail_url"],
                "raw_url": result["image_url"],
                "web_url": result["web_url"],
                "local_file": result["filename"],
            }
        raise ValueError(f"imgbox upload failed: {result.get('error', 'Unknown error')}")

    async def upload_batch(self, client, image_paths, title=None, adult=False):
        log.debug(f"[imgbox] Preparing to upload {len(image_paths)} images")
        gallery = _ImgboxGallery(client, title=title or "Untitled", thumb_width=350, adult=adult)
        results = await gallery.upload_batch(image_paths)

        uploaded = [
            {
                "img_url": r["thumbnail_url"],
                "raw_url": r["image_url"],
                "web_url": r["web_url"],
                "local_file": r["filename"],
            }
            for r in results
            if r["success"]
        ]

        for r in results:
            if not r["success"]:
                log.debug(f"[imgbox] Failed to upload '{r['filename']}': {r.get('error')}")

        if uploaded:
            return uploaded
        raise ValueError("Imgbox upload failed")


class _ImgboxGallery:
    def __init__(self, client, title=None, thumb_width=100, square_thumbs=False, adult=False):
        self._client = client
        self.title = (title or "")[:_MAX_TITLE_LENGTH]
        self.square_thumbs = square_thumbs
        self.thumb_width = thumb_width
        self.adult = adult
        self._gallery_token: dict = {}
        self._csrf_token: str = ""

    @property
    def thumb_width(self) -> int:
        return getattr(self, "_thumb_width", 100)

    @thumb_width.setter
    def thumb_width(self, value: int):
        self._thumb_width = _find_closest_thumb_width(int(value))
        self._thumbnail_size = _THUMBNAIL_SIZES_KEEP_ASPECT[self._thumb_width]

    @property
    def adult(self) -> bool:
        return self._content_type != _CONTENT_TYPES["family"]

    @adult.setter
    def adult(self, value: bool):
        self._content_type = _CONTENT_TYPES["adult"] if value else _CONTENT_TYPES["family"]

    def _build_upload_data(self) -> dict:
        return {
            "token_id": str(self._gallery_token["token_id"]),
            "token_secret": str(self._gallery_token["token_secret"]),
            "gallery_id": str(self._gallery_token.get("gallery_id", "null")),
            "gallery_secret": str(self._gallery_token.get("gallery_secret", "null")),
            "content_type": str(self._content_type),
            "thumbnail_size": str(self._thumbnail_size),
            "comments_enabled": "0",
        }

    async def _ensure_gallery(self) -> None:
        if self._gallery_token and self._gallery_token.get("ok") is not False:
            return

        max_retries = 3
        last_error: str = ""

        for attempt in range(1, max_retries + 1):
            try:
                resp = await self._client.get(f"https://{_SERVICE_DOMAIN}/")
                resp.raise_for_status()
                tree = HTMLParser(resp.text)

                csrf_token = ""
                for meta in tree.css("meta[name='csrf-token']"):
                    csrf_token = meta.attributes.get("content", "")
                    if csrf_token:
                        break
                if not csrf_token:
                    raise RuntimeError("Couldn't find CSRF token in HTML head")
                self._csrf_token = csrf_token

                token_resp = await self._client.post(
                    _TOKEN_URL,
                    data={"gallery": "true", "gallery_title": self.title, "comments_enabled": "0"},
                    headers={"X-CSRF-Token": csrf_token},
                )
                token_resp.raise_for_status()
                gallery_token = token_resp.json()

                if (
                    isinstance(gallery_token, dict)
                    and gallery_token.get("ok") is True
                    and "token_id" in gallery_token
                ):
                    self._gallery_token = gallery_token
                    log.debug("[imgbox] Gallery token obtained")
                    return

                log.warning(f"[imgbox] Gallery attempt {attempt}/{max_retries} failed: {gallery_token!r}")
                last_error = f"ok=False: {gallery_token!r}"

            except (niquests.exceptions.RequestException, RuntimeError) as e:
                log.warning(f"[imgbox] Gallery attempt {attempt}/{max_retries} error: {e}")
                last_error = str(e)

            if attempt < max_retries:
                await asyncio.sleep(2.0)

        raise RuntimeError(f"Failed to create gallery after {max_retries} attempts. Last: {last_error}")

    def _handle_upload_response(self, resp, filenames: list[str]) -> list[dict]:
        try:
            returned = resp.json().get("files", [])
        except (KeyError, ValueError) as exc:
            log.error(f"[imgbox] Unexpected response: {resp.text}")
            return [{"success": False, "error": f"API parse error: {exc}", "filename": n} for n in filenames]

        results = []
        for idx, name in enumerate(filenames):
            if idx < len(returned):
                info = returned[idx]
                results.append(
                    {
                        "success": True,
                        "error": None,
                        "filename": name,
                        "image_url": info.get("original_url", ""),
                        "thumbnail_url": info.get("thumbnail_url", ""),
                        "web_url": info.get("url", ""),
                    }
                )
            else:
                results.append({"success": False, "error": "Missing from response", "filename": name})
        return results

    async def _read_and_validate(self, filepath: str) -> tuple[str, bytes | None, str | None]:
        fpath = Path(filepath)
        try:
            content = await asyncio.to_thread(fpath.read_bytes)
        except OSError as e:
            return (fpath.name, None, str(e))
        if len(content) > _MAX_FILE_SIZE:
            return (fpath.name, None, f"Exceeds {_MAX_FILE_SIZE} bytes")
        return (fpath.name, content, None)

    async def add(self, filepath: str) -> dict:
        fpath = Path(filepath)
        filename, content, error = await self._read_and_validate(filepath)
        if error:
            return {"success": False, "error": error, "filename": filename}

        await self._ensure_gallery()

        mimetype = mimetypes.guess_type(filepath)[0] or "image/png"
        try:
            resp = await self._client.post(
                _PROCESS_URL,
                data=self._build_upload_data(),
                files={"files[]": (fpath.name, content, mimetype)},
                headers={"X-CSRF-Token": self._csrf_token},
            )
            resp.raise_for_status()
        except niquests.exceptions.RequestException as e:
            return {"success": False, "error": str(e), "filename": filename}

        return self._handle_upload_response(resp, [filename])[0]

    async def upload_batch(self, filepaths: list[str]) -> list[dict]:
        await self._ensure_gallery()
        read_results = await asyncio.gather(*(self._read_and_validate(fp) for fp in filepaths))

        results: dict[str, dict] = {}

        for _filepath, (filename, _content, error) in zip(filepaths, read_results):
            if error:
                results[filename] = {"success": False, "error": error, "filename": filename}

        async def _upload(filepath: str, filename: str, content: bytes) -> dict:
            mimetype = mimetypes.guess_type(filepath)[0] or "image/png"
            try:
                resp = await self._client.post(
                    _PROCESS_URL,
                    data=self._build_upload_data(),
                    files={"files[]": (filename, content, mimetype)},
                    headers={"X-CSRF-Token": self._csrf_token},
                )
                resp.raise_for_status()
            except niquests.exceptions.RequestException as e:
                return {"success": False, "error": str(e), "filename": filename}
            return self._handle_upload_response(resp, [filename])[0]

        task_results = await asyncio.gather(
            *(
                _upload(fp, fname, content)
                for fp, (fname, content, err) in zip(filepaths, read_results)
                if err is None
            )
        )

        for r in task_results:
            results[r["filename"]] = r

        return [
            results.get(
                Path(fp).name,
                {"success": False, "error": "Upload skipped", "filename": Path(fp).name},
            )
            for fp in filepaths
        ]
