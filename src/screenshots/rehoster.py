from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from src.utils.console import log

from .generator import ScreenshotGenerator
from .up2host import ScreenshotUploader


class RehostManager:
    def __init__(
        self,
        config: dict[str, Any],
        base_dir: Path,
        uploader: ScreenshotUploader,
        generator: ScreenshotGenerator,
    ):
        self.config = config
        self.base_dir = base_dir
        self.uploader = uploader
        self.generator = generator

    async def ensure_compliance(
        self, meta: dict[str, Any], tracker_whitelists: dict[str, list[str]]
    ) -> dict[str, Any]:
        if meta.get("skip_imghost_upload", False) or not tracker_whitelists:
            log.debug("Skipping rehosting: upload skipped or no tracker whitelists")
            return meta

        meta["rehost_list"] = meta.get("rehost_list", {})
        base_images = meta.get("image_list", {"main": [], "episodes": {}, "contact_sheets": []})
        screens_count: int = meta.get("screens", 3)
        per_episode: int = self.config["SCREENS"].get("per_episode", 3)

        for tracker, whitelist in tracker_whitelists.items():
            if not whitelist:
                log.debug(f"Skipping tracker {tracker}: empty whitelist")
                continue
            whitelist_hosts: set[str] = set(whitelist)

            # --- Main screenshots --- #
            compliant_main = self._get_compliant_images(
                base_images["main"], meta["rehost_list"], whitelist_hosts, screens_count
            )
            if len(compliant_main) < screens_count:
                shortfall = screens_count - len(compliant_main)
                log.debug(f"Need to rehost {shortfall} main images for tracker {tracker}")
                viable_hosts = self._get_viable_hosts(self.config, meta, {tracker: whitelist})
                viable_hosts = [
                    h
                    for h in viable_hosts
                    if h not in meta["rehost_list"]
                    or len(meta["rehost_list"].get(h, {}).get("main", [])) < screens_count
                ]
                if viable_hosts:
                    target_host = viable_hosts[0]
                    log.debug(f"Rehosting to {target_host} for tracker {tracker}")
                    rehosted = await self._rehost_images(
                        meta,
                        base_images["main"],
                        compliant_main,
                        shortfall,
                        target_host,
                        label=f"Rehost main - {meta.get('name', '')}",
                    )
                    meta["rehost_list"].setdefault(target_host, {"main": [], "episodes": {}})
                    meta["rehost_list"][target_host]["main"] = rehosted[:screens_count]
                    log.debug(f"Stored {len(rehosted)} rehosted main images in rehost_list[{target_host}]")
                else:
                    log.warning(f"No viable hosts for tracker {tracker} to rehost {shortfall} main images")
            else:
                log.debug(
                    f"Skipping rehost for tracker {tracker}: "
                    f"{len(compliant_main)} compliant main images found"
                )

            # --- Episode screenshots --- #
            if meta.get("tv_pack") == 1:
                for ep_name, ep_images in base_images.get("episodes", {}).items():
                    compliant_ep = self._get_compliant_images(
                        ep_images,
                        meta["rehost_list"],
                        whitelist_hosts,
                        per_episode,
                        section_key="episodes",
                        sub_key=ep_name,
                    )
                    if len(compliant_ep) < per_episode:
                        shortfall = per_episode - len(compliant_ep)
                        log.debug(
                            f"Need to rehost {shortfall} images for episode {ep_name} and tracker {tracker}"
                        )
                        viable_hosts = self._get_viable_hosts(self.config, meta, {tracker: whitelist})
                        viable_hosts = [
                            h
                            for h in viable_hosts
                            if h not in meta["rehost_list"]
                            or len(meta["rehost_list"].get(h, {}).get("episodes", {}).get(ep_name, []))
                            < per_episode
                        ]
                        if viable_hosts:
                            target_host = viable_hosts[0]
                            log.debug(f"Rehosting episode {ep_name} to {target_host} for tracker {tracker}")
                            rehosted = await self._rehost_images(
                                meta,
                                ep_images,
                                compliant_ep,
                                shortfall,
                                target_host,
                                label=f"Rehost {ep_name} - {meta.get('name', '')}",
                            )
                            meta["rehost_list"].setdefault(target_host, {"main": [], "episodes": {}})
                            meta["rehost_list"][target_host]["episodes"][ep_name] = rehosted[:per_episode]
                            log.debug(
                                f"Stored {len(rehosted)} rehosted images for "
                                f"episode {ep_name} in rehost_list[{target_host}]"
                            )
                        else:
                            log.warning(
                                f"No viable hosts for tracker {tracker} to rehost "
                                f"{shortfall} images for episode {ep_name}"
                            )
                    else:
                        log.debug(
                            f"Skipping rehost for episode {ep_name} and tracker "
                            f"{tracker}: {len(compliant_ep)} compliant images found"
                        )

            # --- Contact sheets --- #
            contact_section: list[dict[str, Any]] = base_images.get("contact_sheets", [])
            if contact_section:
                compliant_cs = self._get_compliant_images(
                    contact_section,
                    meta["rehost_list"],
                    whitelist_hosts,
                    len(contact_section),
                    section_key="contact_sheets",
                )
                if len(compliant_cs) < len(contact_section):
                    shortfall = len(contact_section) - len(compliant_cs)
                    log.debug(f"Need to rehost {shortfall} contact sheet images for tracker {tracker}")
                    viable_hosts = self._get_viable_hosts(self.config, meta, {tracker: whitelist})
                    viable_hosts = [
                        h
                        for h in viable_hosts
                        if h not in meta["rehost_list"]
                        or len(meta["rehost_list"].get(h, {}).get("contact_sheets", []))
                        < len(contact_section)
                    ]
                    if viable_hosts:
                        target_host = viable_hosts[0]
                        log.debug(f"Rehosting contact sheets to {target_host} for tracker {tracker}")
                        rehosted = await self._rehost_images(
                            meta,
                            contact_section,
                            compliant_cs,
                            shortfall,
                            target_host,
                            label=f"Rehost CS - {meta.get('name', '')}",
                        )
                        meta["rehost_list"].setdefault(
                            target_host, {"main": [], "episodes": {}, "contact_sheets": []}
                        )
                        meta["rehost_list"][target_host]["contact_sheets"] = rehosted[-len(contact_section) :]
                        log.debug(
                            f"Stored {len(rehosted)} rehosted contact sheets in rehost_list[{target_host}]"
                        )
                    else:
                        log.warning(
                            f"No viable hosts for tracker {tracker} to rehost "
                            f"{shortfall} contact sheet images"
                        )
                else:
                    log.debug(
                        f"Skipping contact sheet rehost for tracker {tracker}: "
                        f"{len(compliant_cs)} compliant images found"
                    )

        meta["image_list"]["rehost_list"] = meta["rehost_list"]
        return meta

    def _get_compliant_images(
        self,
        images: list[dict[str, Any]],
        rehost_images: dict[str, dict[str, Any]],
        whitelist_hosts: set[str],
        count: int,
        section_key: str = "main",
        sub_key: str | None = None,
    ) -> list[dict[str, Any]]:
        compliant: list[dict[str, Any]] = []
        seen_files: set[str] = set()

        def _get_url(img: dict[str, Any]) -> str:
            return img.get("img_url") or img.get("raw_url") or img.get("web_url", "")

        def _try_add(img: dict[str, Any]) -> bool:
            lf = img.get("local_file", "")
            if lf in seen_files:
                return False
            seen_files.add(lf)
            compliant.append(img)
            return len(compliant) >= count

        for img in images:
            if not isinstance(img, dict):
                continue
            url = _get_url(img)
            if not url:
                continue
            host = self._extract_host(url, whitelist_hosts)
            if host in whitelist_hosts:
                if _try_add(img):
                    break

        for host in whitelist_hosts:
            if len(compliant) >= count:
                break
            rehost_section = rehost_images.get(host, {})
            if sub_key and section_key == "episodes":
                rehost_entries = rehost_section.get("episodes", {}).get(sub_key, [])
            else:
                rehost_entries = rehost_section.get(section_key, [])
            for img in rehost_entries:
                if not isinstance(img, dict):
                    continue
                url = _get_url(img)
                if not url:
                    continue
                img_host = self._extract_host(url, {host})
                if img_host == host:
                    if _try_add(img):
                        break

        rehost_total = sum(len(r.get("main", [])) for r in rehost_images.values())
        log.debug(
            f"Found {len(compliant)} compliant images out of {len(images)} "
            f"image_list and {rehost_total} rehost_list images for "
            f"whitelist: {whitelist_hosts}"
        )
        return compliant[:count]

    async def _rehost_images(
        self,
        meta: dict[str, Any],
        base_images: list[dict[str, Any]],
        compliant: list[dict[str, Any]],
        shortfall: int,
        target_host: str,
        label: str = "Rehost",
    ) -> list[dict[str, Any]]:
        tmp_dir = self.base_dir / "tmp" / meta["uuid"]
        compliant_files = {img.get("local_file") for img in compliant}
        to_rehost = [
            img
            for img in base_images
            if img.get("local_file") not in compliant_files
            and img.get("local_file")
            and (tmp_dir / img["local_file"]).exists()
        ][:shortfall]
        log.debug(
            f"Selected {len(to_rehost)} images to rehost to {target_host}: "
            f"{[img['local_file'] for img in to_rehost]}"
        )
        if len(to_rehost) < shortfall:
            gen_shortfall = shortfall - len(to_rehost)
            log.debug(f"Generating {gen_shortfall} additional screenshots to meet shortfall")
            new_screens = await self.generator.generate_screenshots(meta, meta["filename"], meta["uuid"])
            to_rehost.extend(new_screens["main"][:gen_shortfall])
            log.debug(
                f"After generation, {len(to_rehost)} images to rehost: "
                f"{[img['local_file'] for img in to_rehost]}"
            )
        rehosted = await self.uploader.upload_screenshots(meta, {"main": to_rehost}, [target_host])
        log.debug(
            f"Rehosted {len(rehosted['main'])} images to {target_host}: "
            f"{[img.get('img_url', '') for img in rehosted['main']]}"
        )
        return rehosted["main"]

    @staticmethod
    def _extract_host(url: str, whitelist: set[str]) -> str:
        if not whitelist:
            return urlparse(url).hostname or ""
        hostname = urlparse(url).hostname or ""
        host_lower = hostname.lower()
        host_labels = host_lower.split(".")
        for wl in whitelist:
            wl_lower = wl.lower()
            if host_lower == wl_lower:
                return wl
            if "." not in wl_lower:
                if wl_lower in host_labels:
                    return wl
            elif host_lower == wl_lower or host_lower.endswith("." + wl_lower):
                return wl
        return ""

    @staticmethod
    def _get_viable_hosts(
        config: dict[str, Any], meta: dict[str, Any], tracker_whitelists: dict[str, list[str]]
    ) -> list[str]:
        real_whitelists: list[set[str]] = []
        for wl in tracker_whitelists.values():
            real = {h for h in wl if h != "selfhost"}
            if real:
                real_whitelists.append(real)
        merged_whitelist: set[str] | None = set.intersection(*real_whitelists) if real_whitelists else None

        preferred: list[str] = []
        if meta.get("imghost"):
            preferred.append(meta["imghost"])
        preferred.extend(config["SCREENS"].get("img_hosts", []))
        preferred.append("imgbox")
        preferred.append("imagebam")

        if not meta.get("xxx"):
            preferred = [h for h in preferred if not ScreenshotUploader._is_host_adult_only(config, h)]

        if merged_whitelist is not None:
            filtered = [host for host in preferred if host in merged_whitelist]
            if filtered:
                return filtered
            return list(merged_whitelist)
        return preferred
