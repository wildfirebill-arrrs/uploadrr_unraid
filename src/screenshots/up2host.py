from __future__ import annotations

import asyncio
import socket
from importlib import import_module
from pathlib import Path

import niquests

from src.exceptions import ApiKeyFormatError, FileTooLargeError
from src.progress import ProgressManager
from src.utils.console import log

_NO_API_KEY_HOSTS: set[str] = {"imgbox", "pixhost", "fastpic", "catbox", "imagebam"}


async def _host_available(hostname: str, port: int = 443, timeout: float = 2.0) -> bool:
    """Quick TCP pre-flight check to avoid slow TCP timeout cycles on dead hosts."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setblocking(False)
    try:
        loop = asyncio.get_running_loop()
        await asyncio.wait_for(loop.sock_connect(sock, (hostname, port)), timeout=timeout)
        return True
    except Exception:
        return False
    finally:
        sock.close()


class ScreenshotUploader:
    def __init__(self, config, base_dir):
        self.config = config
        self.base_dir = base_dir
        self.timeout = niquests.TimeoutConfiguration(60.0)
        self.client = None
        self._failed_hosts: set[str] = set()
        self._host_cache: dict[str, object] = {}

    async def __aenter__(self):
        self.client = niquests.AsyncSession(timeout=self.timeout, disable_http3=True)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.close()

    @staticmethod
    def _is_host_adult_only(config, host_name: str) -> bool:
        module_name = host_name.split(".")[0]
        class_name = module_name.upper()
        try:
            module = import_module(f"src.screenshots.imghosts.{module_name}")
            host_class = getattr(module, class_name)
            if hasattr(host_class, "adult_only"):
                return bool(host_class.adult_only)
            return getattr(host_class(config), "adult_only", False)
        except Exception:
            return False

    def _load_host(self, host_name: str):
        """Dynamically load and instantiate an image host plugin."""
        if host_name in self._host_cache:
            return self._host_cache[host_name]

        module_name = host_name.split(".")[0]
        class_name = module_name.upper()

        try:
            module = import_module(f"src.screenshots.imghosts.{module_name}")
            host_class = getattr(module, class_name)
            host = host_class(self.config)
            self._host_cache[host_name] = host
            return host
        except (ImportError, AttributeError, KeyError) as e:
            log.warning(f"[Uploader] Failed to load host '{module_name}': {e}")
            return None

    def _is_host_configured(self, host: str, adult: bool) -> bool:
        host_base = host.split(".")[0]
        if host_base in _NO_API_KEY_HOSTS:
            return True

        screens_keys = self.config.get("SCREENS", {}).get("API_KEYS", {})
        if adult:
            adult_cfg = self.config.get("ADULT", {})
            adult_keys = adult_cfg.get("API_KEYS", {})
            if adult_keys.get(host_base):
                return True
            if not adult_cfg.get("adult_host_only", True):
                if screens_keys.get(host_base):
                    return True
            return bool(adult_cfg.get("imgbox_only", False) and host_base == "imgbox")

        return bool(screens_keys.get(host_base))

    async def upload_screenshots(self, meta: dict, screens: dict, viable_hosts: list[str]) -> dict:
        async with self:
            pm = ProgressManager.get_instance()
            if pm:
                total_to_upload = 0
                if "main" in screens:
                    total_to_upload += (
                        len(screens["main"])
                        if meta.get("is_music", False)
                        else min(len(screens["main"]), meta.get("screens", 3))
                    )
                if "episodes" in screens:
                    per_episode = self.config.get("SCREENS", {}).get("per_episode", 3)
                    for ep_screens in screens["episodes"].values():
                        total_to_upload += min(len(ep_screens), per_episode)
                if "contact_sheets" in screens:
                    contact_images = (
                        screens["contact_sheets"]
                        if isinstance(screens["contact_sheets"], list)
                        else [screens["contact_sheets"]]
                    )
                    total_to_upload += len(contact_images)
                pm.screen_start(total_to_upload)

            result = {"main": [], "episodes": {}, "contact_sheets": [], "trailer": []}
            total_uploaded = 0

            for section, images in screens.items():
                if section == "main":
                    max_images = len(images) if meta.get("is_music", False) else meta.get("screens", 3)
                    result["main"] = await self._upload_batch(
                        meta, images, viable_hosts, meta["name"], max_images, meta["xxx"]
                    )
                    total_uploaded += len(result["main"])

                elif section == "episodes":
                    per_episode = self.config.get("SCREENS", {}).get("per_episode", 3)
                    ep_tasks = [
                        self._upload_batch(
                            meta,
                            ep_screens,
                            viable_hosts,
                            f"{ep_name} - {meta['name']}",
                            per_episode,
                            meta["xxx"],
                        )
                        for ep_name, ep_screens in images.items()
                    ]
                    ep_results = await asyncio.gather(*ep_tasks)
                    for ep_name, uploaded in zip(images.keys(), ep_results):
                        result["episodes"][ep_name] = uploaded
                        total_uploaded += len(uploaded)

                elif section == "contact_sheets":
                    uploaded = await self._upload_batch(
                        meta, images, viable_hosts, f"CS - {meta['name']}", len(images), meta["xxx"]
                    )
                    result["contact_sheets"] = uploaded
                    total_uploaded += len(uploaded)

                elif section == "trailer":
                    uploaded = await self._upload_batch(
                        meta, images, viable_hosts, f"Trailer - {meta['name']}", len(images), meta["xxx"]
                    )
                    result["trailer"] = uploaded
                    total_uploaded += len(uploaded)

            if pm and total_uploaded < total_to_upload:
                pm.screen_up(total_to_upload - total_uploaded)

            if pm:
                pm.screen_done()
            return result

    async def _upload_batch(self, meta, images, viable_hosts, label, max_images, adult=False):
        # Ensure images is a list of dicts
        images = [
            {"local_file": img} if isinstance(img, str) else img
            for img in (images if isinstance(images, list) else [images])
        ]
        if not images:
            log.debug(f"No images to upload for {label}")
            return []

        if meta.get("is_music", False):
            valid_images = []
            image_paths = []
            for img in images[:max_images]:
                if "local_file" not in img:
                    continue
                img_path = Path(img["local_file"]).resolve()
                if img_path.exists():
                    valid_images.append(img)
                    image_paths.append(str(img_path))
                else:
                    log.warning(f"Music image file does not exist: {img['local_file']}")
        else:
            tmp_dir = self.base_dir / "tmp" / meta["uuid"]
            valid_images = [
                img
                for img in images[:max_images]
                if "local_file" in img and (tmp_dir / img["local_file"]).exists()
            ]
            image_paths = [(tmp_dir / img["local_file"]).as_posix() for img in valid_images]

        if not image_paths:
            if "CS -" not in label:
                log.warning(f"No valid image paths found for {label}")
            return []

        last_exc = None
        viable_hosts = [
            h for h in viable_hosts if self._is_host_configured(h, adult) and h not in self._failed_hosts
        ]
        if not adult:
            viable_hosts = [h for h in viable_hosts if not self._is_host_adult_only(self.config, h)]

        # imgbox/imagebam does not support WebP
        if any(Path(p).suffix.lower() == ".webp" for p in image_paths):
            log.debug("Excluding imgbox and imagebam from viable hosts: .webp not supported")
            viable_hosts = [h for h in viable_hosts if h.split(".")[0] not in ("imgbox", "imagebam")]

        if not viable_hosts:
            log.warning(f"No viable hosts configured for {label}")
            return []

        for host in viable_hosts:
            try:
                uploaded = await self._upload_with_retries(
                    host, valid_images, image_paths, label, adult=adult
                )
                if uploaded:
                    return uploaded
                else:
                    log.warning(f"No images uploaded to {host} for {label}, retrying next host")
            except (ConnectionError, TimeoutError) as e:
                log.warning(f"{host} unreachable ({e}), trying next host")
                self._failed_hosts.add(host)
            except Exception as e:
                last_exc = e
                log.error(f"[Uploader] {host} failed batch for {label}: {e!r}")
        err_msg = f"Failed to upload {label} to any host"
        if last_exc:
            err_msg += f": last error: {last_exc!r}"
        log.error(err_msg)
        return []

    async def _upload_with_retries(self, host_name, images, image_paths, title, adult=False, max_retries=3):
        pm = ProgressManager.get_instance()
        host = self._load_host(host_name)
        if not host:
            log.error(f"[Uploader] Skipping upload: could not load host '{host_name}' (adult={adult})")
            return []

        if not image_paths:
            log.warning(f"No valid image paths for upload to {host_name}")
            return []

        if not self._is_host_configured(host_name, adult):
            log.warning(f"Skipping {host_name}: no API key configured")
            return []

        domain = getattr(host, "host_domain", None) or host_name.split(".")[0]

        for attempt in range(max_retries):
            try:
                results = []
                if hasattr(host, "upload_batch"):
                    batch_results = await host.upload_batch(
                        self.client, image_paths, title=title, adult=adult
                    )
                    if not isinstance(batch_results, list):
                        log.warning(
                            f"{host_name} upload_batch did not return a list. "
                            f"Got {type(batch_results).__name__}."
                        )
                        batch_results = []
                    successful = sum(
                        1
                        for r in batch_results
                        if r
                        and isinstance(r, dict)
                        and any(r.get(k) for k in ("img_url", "raw_url", "web_url"))
                    )
                    if pm:
                        pm.screen_up(successful)
                    results = batch_results
                else:
                    tasks = [host.upload(self.client, path, title=title, adult=adult) for path in image_paths]
                    raw_results = await asyncio.gather(*tasks, return_exceptions=True)
                    successful = 0
                    for r in raw_results:
                        if isinstance(r, (PermissionError, FileTooLargeError)):
                            raise r
                        if isinstance(r, Exception):
                            log.debug(f"Upload failed: {r}")
                            results.append(None)
                        else:
                            log.debug(f"Raw single result from {host_name}: {r}")
                            if (
                                r
                                and isinstance(r, dict)
                                and any(r.get(k) for k in ("img_url", "raw_url", "web_url"))
                            ):
                                successful += 1
                            results.append(r)
                    if pm and successful:
                        pm.screen_up(successful)

                # Filter valid results
                valid_results = []
                for r, img in zip(results, images):
                    if not r:
                        continue
                    if not isinstance(r, dict):
                        continue
                    if not any(r.get(k) for k in ("img_url", "raw_url", "web_url")):
                        continue
                    result = {
                        "img_url": r.get("img_url", ""),
                        "raw_url": r.get("raw_url", ""),
                        "web_url": r.get("web_url", ""),
                        "local_file": img["local_file"],
                        "ss_time": img.get("ss_time", 0),
                    }
                    valid_results.append(result)
                if valid_results:
                    log.info(f"[{host_name}] Uploaded {len(valid_results)} images for {title}")
                    return valid_results
                else:
                    log.warning(f"No valid uploads to {host_name} on attempt {attempt + 1}, retrying")

            except PermissionError as e:
                log.error(f"{host_name} access denied, skipping: {e}")
                self._failed_hosts.add(host_name)
                return []
            except FileTooLargeError as e:
                log.error(f"{host_name} rejected upload (file too large), skipping: {e}")
                self._failed_hosts.add(host_name)
                return []
            except ApiKeyFormatError as e:
                log.error(f"{host_name} {e}")
                self._failed_hosts.add(host_name)
                return []
            except (ConnectionError, TimeoutError) as e:
                if domain and not await _host_available(domain):
                    log.warning(f"{host_name} confirmed unreachable, removing from rotation")
                    self._failed_hosts.add(host_name)
                    return []
                log.warning(f"{host_name} transient timeout on attempt {attempt + 1}, will retry: {e}")
            except Exception as e:
                # missing API key / host not allowed
                if isinstance(e, KeyError) and ("API key" in str(e) or "not allowed" in str(e)):
                    log.warning(f"Skipping {host_name}: {e}")
                    return []  # no point in retrying
                log.warning(f"{host_name} attempt {attempt + 1}/{max_retries} failed: {e!r}")
                await asyncio.sleep(0.3)

        log.warning(f"All retries exhausted for {host_name}, removing from rotation")
        self._failed_hosts.add(host_name)
        return []  # Return empty list to indicate total failure
