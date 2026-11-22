from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path

from src.utils.console import log

from .base import BaseImageHost


class DIGITALCLUB(BaseImageHost):
    def __init__(self, config):
        super().__init__(config)
        self.api_url = "https://img.digitalcore.club/api/upload"
        self.host_name = "digitalclub"
        self.api_key_name = "digitalclub"

    async def upload(self, client, image_path, title=None, adult=False):
        api_key = self.config["SCREENS"]["API_KEYS"].get("digitalclub")
        if not api_key:
            raise KeyError("API key for 'digitalclub' not found in SCREENS")

        path_obj = Path(image_path)
        content = await asyncio.to_thread(path_obj.read_bytes)
        mimetype = mimetypes.guess_type(image_path)[0] or "image/png"

        headers = {"Authorization": api_key}
        files = {"file": (path_obj.name, content, mimetype)}
        data = {}
        if title:
            data["title"] = title

        resp = await client.post(self.api_url, headers=headers, data=data, files=files)
        self._validate_response(resp, self.host_name)

        response_data = resp.json()
        log.debug(f"[{self.host_name}] Full server response: {response_data}")

        data = response_data.get("data", {})
        link = data.get("link") or response_data.get("link")
        page = data.get("page")
        if not link:
            raise ValueError(f"[{self.host_name}] No link in response: {response_data}")

        return {"img_url": link, "raw_url": link, "web_url": page or link}

    async def upload_batch(self, client, image_paths, title=None, adult=False):
        results = []
        for path in image_paths:
            try:
                result = await self.upload(client, path, title, adult)
                if result and all(k in result for k in ("img_url", "raw_url", "web_url")):
                    results.append(result)
                else:
                    log.warning(f"[{self.host_name}] Invalid upload result: {result}")
                    results.append(None)
            except Exception as e:
                log.error(f"[{self.host_name}] Upload failed: {e}")
                results.append(None)
        return results
