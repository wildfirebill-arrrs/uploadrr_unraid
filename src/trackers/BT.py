from __future__ import annotations

import contextlib
import math
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import niquests
from bs4 import BeautifulSoup
from PIL import Image
from rich.pretty import Pretty
from rich.prompt import Prompt
from torf import Torrent

from src.progress import ProgressManager
from src.trackers.COMMON import COMMON
from src.utils.bbcode import BBCODE
from src.utils.console import console, log
from src.utils.cookieme import CookieMonster
from src.utils.phash import PerceptualHash
from src.utils.size_parser import parse_size
from src.utils.torrentman import TorrentMaker, edit_torrent, extract_size_from_torrent
from src.version import version


class BT(COMMON):
    def __init__(self, config):
        super().__init__(config)
        self.config = config
        self.type = "http"
        self.tracker = "BT"
        self.source_flag = "BootyTape"
        self.domain = "https://ssl.bootytape.com"
        self.upload_url = f"{self.domain}/takeupload.php"
        self.login_url = f"{self.domain}/login.php"
        self.allowed_imghosts = ["imgbox", "imagebam"]
        self.banned_groups = []

    async def get_type_id(self, meta: dict[str, Any]) -> str:
        porndb_tags = meta.get("porndb", {}).get("tags", "")
        user_keywords = meta.get("keywords", "") if meta.get("keywords") else ""
        tags = (
            f"{porndb_tags}, {user_keywords}"
            if porndb_tags and user_keywords
            else porndb_tags or user_keywords
        )
        if not tags:
            pm = ProgressManager.get_instance()
            if pm and not pm.unattended and not pm.quiet:
                with pm.pause_for_interaction():
                    console.print("[yellow]No porndb tags found. Please select category:[/yellow]")
                    console.print("  1: Straight (52)")
                    console.print("  2: Animation (70)")
                    console.print("  3: X Play Hard (82)")
                    console.print("  4: X Play Soft (92)")
                    console.print("  5: Girl on Girl (22)")
                    choice = Prompt.ask("Enter choice", choices=["1", "2", "3", "4", "5"], default="1")
                category_map = {
                    "2": "70",  # Animation
                    "3": "82",  # X Play Hard
                    "4": "92",  # X Play Soft
                    "5": "22",  # Girl on Girl
                }
                return category_map.get(choice, "52")
            return "52"
        tags = [tag.lower() for tag in tags.split(",")]

        if any(
            keyword in tag
            for keyword in ["animation", "cartoon", "anime", "animated", "hentai"]
            for tag in tags
        ):
            return "70"  # Animation

        if any(
            keyword in tag
            for keyword in ["gay", "shemale", "bisexual", "bi-sex", "trans", "futa", "tgirl", "tguy"]
            for tag in tags
        ):
            return "82"  # X Play Hard: Gay, Shemale, Bi-Sex Content

        if any(keyword in tag for keyword in ["vomit", "piss", "scat"] for tag in tags):
            return "92"  # X Play Soft: Vomit and Piss Content

        if any(
            keyword in tag
            for keyword in [
                "lesbian",
                "girl-girl",
                "girl-only",
                "no men allowed",
                "all girl",
                "lesbea",
                "girl on girl",
            ]
            for tag in tags
        ):
            return "22"  # Girl on Girl

        # 43 Images Only

        return "52"  # default to Straight

    async def edit_name(self, meta) -> str:
        bt_name = (
            meta["name"]
            .replace(meta["audio"], "")
            .replace(meta["video_encode"], "")
            .replace(meta["tag"], "")
            .strip()
        )
        meta["bt_name"] = bt_name
        return bt_name

    @staticmethod
    def convert_image(input_path: Path, output_path: Path) -> None:
        """Convert image to JPG format."""
        img = Image.open(input_path)
        img.convert("RGB").save(output_path, "JPEG")

    @staticmethod
    def close_bbcode_tags(text: str) -> str:
        """Ensure all BBCode tags are properly closed."""
        stack = []
        result = []
        i = 0
        while i < len(text):
            if text[i] == "[":
                j = i + 1
                while j < len(text) and text[j] != "]":
                    j += 1
                if j < len(text):
                    tag_content = text[i + 1 : j]
                    if not tag_content.startswith("/"):
                        # Opening tag, extract tag name
                        tag_name = tag_content.split("=")[0].split(" ")[0]
                        stack.append(tag_name)
                        result.append(text[i : j + 1])
                    else:
                        # Closing tag
                        close_tag_name = tag_content[1:].split("=")[0].split(" ")[0]
                        if stack and stack[-1] == close_tag_name:
                            stack.pop()
                        result.append(text[i : j + 1])
                    i = j + 1
                else:
                    result.append(text[i])
                    i += 1
            else:
                result.append(text[i])
                i += 1
        # Close any remaining unclosed tags
        while stack:
            tag = stack.pop()
            result.append(f"[/{tag}]")
        return "".join(result)

    async def login(self, client: niquests.AsyncSession, base_dir: Path) -> bool:
        """NOTE BootyTape only ever allows one sension at a time. So another disconnects you."""
        cookie_path = base_dir / "data" / "cookies" / f"{self.tracker}.pkl"
        cm = CookieMonster(cookie_path)

        # Try to load existing cookies
        loaded = await cm.load(client)
        if loaded:
            validated = await cm.validate(
                client, f"{self.domain}/upload.php", lambda r: "takeupload.php" in r.text
            )
            if validated:
                log.debug("[green]Reusing cookies[/green]")
                return True
            else:
                log.debug("[yellow]Stale cookies, logging in..[/yellow]")

        # Perform login
        username = self.config["TRACKERS"][self.tracker]["username"]
        password = self.config["TRACKERS"][self.tracker]["password"]

        login_data = {"take_login": "1", "username": username, "password": password, "returnto": "upload.php"}

        response = await client.post(self.login_url, data=login_data)
        if response.status_code == 200:
            if "takeupload.php" in response.text or response.url.path == "/upload.php":
                await cm.save(client)
                return True
            else:
                log.error("[red]Login failed[/red]")
                return False
        else:
            log.error(f"[red]BT: Login failed with status {response.status_code}[/red]")
            return False

    def get_size_tag(self, meta: dict[str, Any]) -> str:
        content_size = meta.get("content_size") or extract_size_from_torrent(meta["base_dir"], meta["uuid"])
        size_mb = content_size / (1024 * 1024)  # Convert to MB
        if size_mb <= 250:
            return "0mb to 250mb"
        elif size_mb <= 500:
            return "250mb to 500mb"
        elif size_mb <= 750:
            return "500mb to 750mb"
        elif size_mb <= 1000:
            return "750mb to 1gb"
        elif size_mb <= 1500:
            return "1gb to 1.5gb"
        elif size_mb <= 2500:
            return "1.5gb to 2.5gb"
        elif size_mb <= 5000:
            return "2.5gb to 5gb"
        elif size_mb <= 10000:
            return "5gb to 10gb"
        elif size_mb <= 100000:
            return "10gb to 100gb"
        else:
            return "100gb to 2tb"

    async def get_tags(self, meta: dict[str, Any]) -> str:
        """Generate at least 6 category tags based on size, year, and porndb tags."""
        tags = []

        tags.append(self.get_size_tag(meta))
        tags.append(meta["resolution"])
        if meta.get("is_disc"):
            tags.append(meta["is_disc"])
        else:
            tags.append(Path(meta["video"]).suffix[1:])

        # Year-based tags (decades)
        year = (lambda x: int(x) if x and str(x).isdigit() else 0)(meta.get("search_year")) or (
            lambda x: int(x) if x and str(x).isdigit() else 0
        )(meta.get("porndb", {}).get("year"))
        if year:
            decade = (year // 10) * 10
            tags.append(f"{decade}s")

        porndb_tags = meta.get("porndb", {}).get("tags", "")
        user_keywords = meta.get("keywords", "") if meta.get("keywords") else ""
        combined = f"{porndb_tags}, {user_keywords}"
        seen = set()
        for tag in combined.split(","):
            tag = tag.strip()
            if tag and tag.lower() not in seen:
                seen.add(tag.lower())
                tags.append(tag)

        # BT Requires Min 6 Tags, ask if need more.
        pm = ProgressManager.get_instance()
        while len(tags) < 6:
            console.print(
                f"[yellow]Only {len(tags)} tags so far. BootyTape requires at least 6 tags.[/yellow]"
            )
            with pm.pause_for_interaction():
                additional_input = input("Enter additional tags (comma separated): ").strip()
            if additional_input:
                additional_tags = [tag.strip() for tag in additional_input.split(",") if tag.strip()]
                for tag in additional_tags:
                    if tag not in tags:
                        tags.append(tag)
                        if len(tags) >= 6:
                            break
            else:
                console.print("[yellow]BT: No additional tags provided.[/yellow]")
                break

        return ", ".join(tags)

    async def check_torrent_size(self, meta: dict[str, Any], torrent_path: Path, announce_url: str) -> bool:
        """Check if torrent file is <= 1MB, if not, rehash with calculated larger piece size."""
        size = torrent_path.stat().st_size
        if size <= 1024 * 1024:  # 1MB
            return True

        console.print("[yellow]BT: Torrent file > 1MB, rehashing with larger piece size[/yellow]")
        base_torrent_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / "BASE.torrent"
        if not base_torrent_path.exists():
            console.print("[red]BT: BASE.torrent not found, cannot rehash[/red]")
            return False

        content_size = meta.get("content_size", 0)
        if content_size == 0:
            content_size = extract_size_from_torrent(meta["base_dir"], meta["uuid"])
            meta["content_size"] = content_size

        # Estimate required piece size
        # Overhead ~2KB, max torrent size 1MB
        overhead = 2048
        max_torrent_size = 1024 * 1024
        pieces_bytes_limit = max_torrent_size - overhead
        max_pieces = pieces_bytes_limit // 20  # 20 bytes per piece hash
        required_piece_size_bytes = math.ceil(content_size / max_pieces)
        required_piece_size_exp = math.ceil(math.log2(required_piece_size_bytes))

        # Load current torrent to get current exp
        torrent = Torrent.read(base_torrent_path)
        current_exp = torrent.piece_size.bit_length() - 1
        new_exp = max(required_piece_size_exp, current_exp + 1)  # Ensure increase

        if new_exp > 24:
            console.print("[red]BT: Required piece size too large[/red]")
            return False

        maketorrent = TorrentMaker(self.config)
        await maketorrent.regenerate(base_torrent_path, new_exp)
        # Re-edit the tracker torrent
        await edit_torrent(meta, None, self.tracker, self.source_flag, announce_url)
        new_size = torrent_path.stat().st_size

        if new_size <= 1024 * 1024:
            console.print("[green]BT: Rehash successful, torrent size now <= 1MB[/green]")
            return True
        else:
            console.print("[red]BT: Rehash failed, torrent still > 1MB[/red]")
            return False

    async def upload(self, meta: dict[str, Any]) -> bool:
        async with niquests.AsyncSession(disable_http3=True) as client:
            base_dir = Path(meta["base_dir"])
            tmp_dir = base_dir / "tmp" / meta["uuid"]

            # Login
            if not await self.login(client, base_dir):
                return False

            pid = self.config["TRACKERS"][self.tracker].get("PID", "PASSKEY_HERE")
            if pid.startswith(("http://", "https://")):
                announce_url = pid
            else:
                announce_url = f"{self.domain}:2096/{pid}/announce"

            await edit_torrent(meta, None, self.tracker, self.source_flag, announce_url)

            # Check torrent size
            torrent_path = tmp_dir / f"[{self.tracker}]{meta['clean_name']}.torrent"
            if not await self.check_torrent_size(meta, torrent_path, announce_url):
                console.print("[red]BT: Torrent size check failed[/red]")
                return False

            await self.build_desc(meta)
            bt_name = meta.get("bt_name") or await self.edit_name(meta)
            # Prepare form data
            name = meta.get("manual_name") or bt_name
            artist = meta.get("porndb", {}).get("performers", "")
            desc = open(tmp_dir / f"[{self.tracker}]DESCRIPTION.txt", encoding="utf-8").read()
            tags = await self.get_tags(meta)

            # category
            type_id = await self.get_type_id(meta)

            data = {"name": name, "artist": artist, "tagterm": tags, "type": type_id, "descr": desc}

            # (2) Images Required
            image_paths = []

            # First image: cover.jpg
            cover_jpg = tmp_dir / "cover.jpg"
            if cover_jpg.exists():
                image_paths.append(cover_jpg)
            else:
                # Check for cover.webp or cover.png
                cover_webp = tmp_dir / "cover.webp"
                cover_png = tmp_dir / "cover.png"
                if cover_webp.exists():
                    self.convert_image(cover_webp, cover_jpg)
                    image_paths.append(cover_jpg)
                elif cover_png.exists():
                    self.convert_image(cover_png, cover_jpg)
                    image_paths.append(cover_jpg)
                else:
                    fallback_png = tmp_dir / f"{meta['filename']}-0.png"
                    if fallback_png.exists():
                        self.convert_image(fallback_png, cover_jpg)
                        image_paths.append(cover_jpg)

            # Compute phash for cover to avoid duplicates
            phasher = PerceptualHash()
            phash_cover = phasher._phash(Image.open(cover_jpg))

            back_jpg = tmp_dir / "back.jpg"
            image_jpg = tmp_dir / "image.jpg"
            if back_jpg.exists():
                phash_back = phasher._phash(Image.open(back_jpg))
                if phash_back != phash_cover:
                    image_paths.append(back_jpg)
            elif image_jpg.exists():
                phash_image = phasher._phash(Image.open(image_jpg))
                if phash_image != phash_cover:
                    image_paths.append(image_jpg)

            actual_length = 0.0
            req = self.get_contact_sheet_requirement()
            if req:
                try:
                    video = meta.get("video") or (meta.get("video_files") or [None])[0]
                    if video and Path(video).is_file():
                        probe = await self.generator._ffprobe(video, "-show_format")
                        actual_length = float(probe["format"]["duration"])
                except Exception:
                    pass
                allowed_cols, allowed_rows = (
                    req(actual_length) if callable(req) else (req["columns"], req["rows"])
                )
            else:
                allowed_cols, allowed_rows = 0, 0

            contact_sheets = []

            for cs_path in sorted(
                tmp_dir.glob("contact_sheet*.png"),
                key=lambda p: (
                    0
                    if p.stem == "contact_sheet"
                    else int(p.stem.split("_")[-1])
                    if p.stem.split("_")[-1].isdigit()
                    else 99999
                ),
            ):
                dim_match = re.search(r"contact_sheet(?:_(\d+)x(\d+))(?:_(\d+))?$", cs_path.stem)
                if dim_match:
                    cols = int(dim_match.group(1))
                    rows = int(dim_match.group(2))
                    if allowed_cols and allowed_rows and (cols, rows) != (allowed_cols, allowed_rows):
                        continue
                contact_sheets.append(cs_path)
            for cs in contact_sheets:
                cs_jpg = cs.with_suffix(".jpg")
                if not cs_jpg.exists():
                    self.convert_image(cs, cs_jpg)
                image_paths.append(cs_jpg)

            if len(image_paths) < 2:
                console.print("[red]BT: At least 2 images required[/red]")
                return False

            if meta.get("debug", False):
                log.debug("[bright_magenta]DATA 2 SEND[/bright_magenta]:")
                console.print(Pretty(data))
                files_info = {"tfile": torrent_path.name}
                for i, img_path in enumerate(image_paths[:20]):
                    files_info[f"image{i}"] = img_path.name
                log.debug("[bright_magenta]FILES 2 SEND[/bright_magenta]:")
                console.print(Pretty(files_info))
                await edit_torrent(
                    meta,
                    None,
                    self.tracker,
                    "DEBUG",
                    "https://UNIT3D.CHANGES/INFOHASH/DEBUG-ONLY",
                    "Uploadrr DEBUG-MODE",
                )
                return False

            # Prepare files
            files = {}
            with contextlib.ExitStack() as stack:
                torrent_file = stack.enter_context(open(torrent_path, "rb"))
                files["tfile"] = (f"{meta['bt_name']}.torrent", torrent_file, "application/x-bittorrent")

                for i, img_path in enumerate(image_paths[:20]):
                    img_file = stack.enter_context(open(img_path, "rb"))
                    files[f"image{i}"] = (f"image{i}.jpg", img_file, "image/jpeg")

                # Submit form
                response = await client.post(self.upload_url, data=data, files=files)
            if response.status_code == 200:
                if "details.php" in str(response.url):
                    url = str(response.url).replace("&uploaded=1", "")
                    comment = f"Made by Uploadrr for: {url}"
                    await edit_torrent(torrent_path=torrent_path, comment=comment)
                    console.print("[bold green]Torrent uploaded successfully!")
                    return True
                else:
                    soup = BeautifulSoup(response.text, "html.parser")
                    error_parts = []
                    h2_tags = soup.find_all("h2")
                    if h2_tags:
                        last_h2 = h2_tags[-1]
                        error_parts.append(last_h2.get_text().strip())
                        for sibling in last_h2.find_next_siblings("p"):
                            error_parts.append(sibling.get_text().strip())
                        table = last_h2.find_next_sibling("table")
                        if table:
                            td = table.find("td", class_="text")
                            if td:
                                error_parts.append(td.get_text().strip())
                    error_msg = " ".join(error_parts) if error_parts else "Unknown error"
                    log.warning(f"[red]BootyTape Upload failed[/red]: {error_msg}")
                    return False
            else:
                log.error(f"[red]BootyTape Upload failed with status {response.status_code}[/red]")
                return False

    async def search_existing(self, meta: dict[str, Any]) -> dict[str, Any]:
        dupes: dict[str, Any] = {}
        base_dir = Path(meta["base_dir"])

        async with niquests.AsyncSession(disable_http3=True) as client:
            if not await self.login(client, base_dir):
                return dupes

            search_url = f"{self.domain}/torrents.php"
            size_tag = self.get_size_tag(meta)
            params = {"search": meta.get("title", ""), "tagsearch": size_tag, "cat": 0, "tags": 1}

            response = await client.get(search_url, params=params)
            if response.status_code != 200:
                console.print(f"[red]BT: Search failed with status {response.status_code}[/red]")
                return dupes

            soup = BeautifulSoup(response.text, "html.parser")
            rows = soup.select("table.red > tbody > tr:not(:has(th))")
            for row in rows:
                title_elem = row.select_one('a[href^="details.php?id="]')
                if title_elem:
                    title = title_elem.text.strip()
                    size_elem = row.select_one("td:nth-child(3)")
                    size_text = size_elem.text.strip() if size_elem else "0"
                    size_bytes = parse_size(size_text)
                    dupes[title] = size_bytes

        return dupes

    async def build_desc(self, meta):
        from src.utils.descgen import DescGenerator

        descgen = DescGenerator(config=self.config, base_dir=meta["base_dir"])
        await descgen.edit_desc(meta, self.tracker)

        # Clean BBCode for BT tracker
        desc_file = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / f"[{self.tracker}]DESCRIPTION.txt"
        with open(desc_file, encoding="utf-8") as f:
            desc = f.read()

        bbcode = BBCODE()

        # Apply conversions
        desc = bbcode.convert_code_to_pre(desc)
        desc = bbcode.remove_spoiler(desc)
        desc = bbcode.convert_to_align(desc)

        # Replace h tags
        desc = re.sub(r"\[h4\]", "[size=4]", desc)
        desc = re.sub(r"\[/h4\]", "[/size]\n", desc)
        desc = re.sub(r"\[h[12356]\]", "[size=5][b]", desc)
        desc = re.sub(r"\[/h[12356]\]", "[/b][/size]\n", desc)

        # Strip img sizes
        desc = re.sub(r"\[img=\d+\]", "[img]", desc)

        # Ensure all tags are closed
        desc = self.close_bbcode_tags(desc)

        uploadrr_sig = (
            f"[align=right][url=https://codeberg.org/CvT/Uploadrr]Uploadrr v{version}[/url][/align]"
        )
        if uploadrr_sig not in desc:
            desc += f"\n{uploadrr_sig}"

        # Write back
        with open(desc_file, "w", encoding="utf-8") as f:
            f.write(desc)

    def get_contact_sheet_requirement(self) -> dict[str, int] | Callable[[float], tuple[int, int]] | None:
        def _bt_col_rows(length: float) -> tuple[int, int]:
            return (3, 8) if length <= 3600 else (3, 21)

        return _bt_col_rows
