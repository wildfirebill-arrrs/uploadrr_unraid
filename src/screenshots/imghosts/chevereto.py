import base64

import aiofiles

from src.exceptions import ApiKeyFormatError, FileTooLargeError
from src.utils.console import log

from .base import BaseImageHost


class CheveretoTemplate(BaseImageHost):
    def __init__(self, config, api_url, imghost, legacy=False):
        super().__init__(config)
        self.api_url = api_url
        self.host = imghost
        self.legacy = legacy

    async def upload(self, client, image_path, title=None, adult=False):
        """Upload an image to Chevereto and return a dict with img_url, raw_url, and web_url."""
        if adult:
            adult_cfg = self.config.get("ADULT", {})
            adult_keys = adult_cfg.get("API_KEYS", {})
            adult_host_only = adult_cfg.get("adult_host_only", True)

            if self.host in adult_keys:
                api_key = adult_keys[self.host]
            elif not adult_host_only and self.host in self.config["SCREENS"]["API_KEYS"]:
                api_key = self.config["SCREENS"]["API_KEYS"][self.host]

            elif adult_cfg.get("imgbox_only", False) and self.host == "imgbox":
                api_key = self.config["SCREENS"]["API_KEYS"].get("imgbox")
            else:
                raise KeyError(f"Host '{self.host}' not allowed for adult upload")
        else:
            api_key = self.config["SCREENS"]["API_KEYS"].get(self.host)
            if not api_key:
                raise KeyError(f"API key for '{self.host}' not found in SCREENS")

        if not api_key:
            raise KeyError(f"API key for '{self.host}' missing")

        log.debug(f"Using API key for {self.host} (adult={adult}): {api_key}")
        try:
            async with aiofiles.open(image_path, "rb") as f:
                img = await f.read()

            headers = {} if self.legacy else {"X-API-Key": api_key}
            params = {"key": api_key} if self.legacy else {}
            data = {"image": base64.b64encode(img).decode("utf8"), "nsfw": "1" if adult else "0"}
            if title:
                data["title"] = title

            resp = await client.post(self.api_url, headers=headers, params=params, data=data)

            content_type = resp.headers.get("content-type")
            text = resp.text

            log.debug(f"[{self.host}] HTTP {resp.status_code}")
            log.debug(f"[{self.host}] Content-Type: {content_type}")
            log.debug(f"[{self.host}] Raw Body: {text}")

            if "application/json" not in content_type:
                if "crowdsec" in text.lower():
                    raise PermissionError(
                        f"[{self.host}] CrowdSec banned your IP, if you're using a VPN try another exit node"
                    )
                raise ValueError(f"{self.host} response was not JSON")

            result = resp.json()

            if result.get("status_code", 200) == 200 or result.get("success"):
                image_data = result["data"].get("image", result["data"])
                return {
                    "img_url": result["data"].get("medium", {}).get("url", image_data["url"]),
                    "raw_url": image_data["url"],
                    "web_url": result["data"].get("url_viewer", image_data["url"]),
                }
            else:
                error = result.get("error", result)
                msg = str(error) if not isinstance(error, str) else error
                msg_lower = msg.lower()
                if "too big" in msg_lower or "max" in msg_lower or "size" in msg_lower:
                    raise FileTooLargeError(f"{self.host} error: {error}")
                if (
                    "api key format is no longer supported" in msg_lower
                    or "generate a new api key" in msg_lower
                ):
                    raise ApiKeyFormatError(
                        f"{self.host} API key format is no longer supported. "
                        f"Please generate a new API key for {self.host}."
                    )
                raise ValueError(f"{self.host} error: {error}")

        except Exception as e:
            log.error(f"[{self.host}] Failed to upload {image_path}: {e!r}")
            raise

    async def upload_batch(self, client, image_paths, title=None, adult=False):
        """Upload multiple images and return a list of results."""
        results = []
        for path in image_paths:
            try:
                result = await self.upload(client, path, title, adult)
                if result and all(k in result for k in ("img_url", "raw_url", "web_url")):
                    results.append(result)
                else:
                    log.warning(f"[{self.host}] Invalid upload result: {result}")
                    results.append(None)
            except (PermissionError, FileTooLargeError, ApiKeyFormatError):
                raise
            except Exception as e:
                log.error(f"[{self.host}] Upload failed: {e}")
                results.append(None)
        return results
