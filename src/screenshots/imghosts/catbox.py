import mimetypes
from pathlib import Path

from src.utils.console import log

from .base import BaseImageHost


class CATBOX(BaseImageHost):
    def __init__(self, config):
        super().__init__(config)
        self.api_url = "https://catbox.moe/user/api.php"
        self.host_name = "catbox"
        self.api_key_name = "catbox"

    async def upload(self, client, image_path, title=None, adult=False):
        userhash = self.config["SCREENS"]["API_KEYS"].get("catbox")

        form_data = {"reqtype": "fileupload"}
        if userhash:
            form_data["userhash"] = userhash
            log.debug("[catbox] uploading with userhash")
        else:
            log.debug("[catbox] uploading anonymously, no userhash configured")

        mimetype = mimetypes.guess_type(image_path)[0] or "image/png"
        path_obj = Path(image_path)

        with path_obj.open("rb") as fileobj:
            files = {"fileToUpload": (path_obj.name, fileobj, mimetype)}
            resp = await client.post(self.api_url, data=form_data, files=files)

        if resp.status_code == 412:
            raise PermissionError(f"catbox is requiring authorization (412: {resp.text.strip()})")
        self._validate_response(resp, self.host_name)

        url = resp.text.strip()
        if not url.startswith("http"):
            raise ValueError(f"Invalid response from {self.host_name}: {url}")

        return {"img_url": url, "raw_url": url, "web_url": url}

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
            except PermissionError:
                raise
            except Exception as e:
                log.error(f"[{self.host_name}] Upload failed: {e}")
                results.append(None)
        return results
