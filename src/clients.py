from __future__ import annotations

import asyncio
import base64
import errno
import json
import shutil
import ssl
import xmlrpc.client
from pathlib import Path

import bencode
import niquests
import qbittorrentapi
from deluge_client import DelugeRPCClient
from torf import Torrent

from src.progress import ProgressManager
from src.utils.console import console, log
from src.utils.parts import Bunch

_REQUIRED_CLIENT_KEYS: dict[str, list[str]] = {
    "qbit": ["qbit_url", "qbit_port", "qbit_user", "qbit_pass"],
    "deluge": ["deluge_url", "deluge_port", "deluge_user", "deluge_pass"],
    "transmission": ["transmission_url"],
    "rtorrent": ["rtorrent_url"],
    "watch": ["watch_folder"],
}


class Clients:
    def __init__(self, config: dict):
        self.config = config
        self.index_dir = Path(__file__).resolve().parent.parent / "data"
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.index_cache: dict[str, dict] = {}
        self._meta_files_logged: set[str] = set()

    def _validate_client_config(self, torrent_client: str, client: dict) -> None:
        client_type = client.get("torrent_client", "").lower()
        if not client_type:
            raise ValueError(
                f"Client '{torrent_client}' is missing 'torrent_client' key. "
                f"Set it to one of: qbit, deluge, transmission, rtorrent, watch."
            )

        required = _REQUIRED_CLIENT_KEYS.get(client_type)
        if not required:
            raise ValueError(
                f"Unknown torrent client type '{client_type}' for '{torrent_client}'. "
                f"Valid types: {sorted(_REQUIRED_CLIENT_KEYS)}."
            )

        missing = [k for k in required if client.get(k) is None]
        if missing:
            raise ValueError(
                f"Client '{torrent_client}' ({client_type}) is missing required "
                f"configuration keys: {missing}. Update your config and try again."
            )

    def validate_requested_client(self, meta: dict) -> str | None:
        """Validate the client named in meta/defaults. Returns client name or None if none/not set."""
        torrent_client = self._get_torrent_client(meta)
        if not torrent_client or torrent_client == "none":
            return None
        self._validate_client_config(torrent_client, self.config["TORRENT_CLIENTS"][torrent_client])
        return torrent_client

    async def find_existing_torrent(self, meta: dict) -> Path | None:
        pm = ProgressManager.get_instance()

        torrent_client = self.validate_requested_client(meta)
        if not torrent_client:
            log.warning(
                "No torrent client specified or invalid client configuration; skipping torrent search."
            )
            return None

        if pm:
            pm.update(f"Searching {torrent_client}...")

        client = self.config["TORRENT_CLIENTS"][torrent_client]
        client_type = client["torrent_client"].lower()

        storage_dir_str = client.get("torrent_storage_dir", "")
        if not storage_dir_str and client_type != "watch":
            console.print("[bold red]Missing torrent_storage_dir in configuration.")
            return None

        torrent_storage_dir = Path(storage_dir_str)

        log.ddebug(
            f"Searching for existing torrent for {meta.get('clean_name', 'unknown')} in {torrent_storage_dir}"
        )

        if client_type != "watch":
            exists = await asyncio.to_thread(torrent_storage_dir.exists)
            if not exists:
                console.print(f"[bold red]Invalid torrent_storage_dir path: {torrent_storage_dir}")
                return None

        torrenthash = await self._get_valid_torrenthash(meta, torrent_storage_dir, client_type, client)
        if not torrenthash:
            log.info("No valid torrent hash found")
            return None

        torrent_path = torrent_storage_dir / f"{torrenthash}.torrent"
        valid, torrent_path = await self.is_valid_torrent(meta, torrent_path, torrenthash, client_type)
        log.debug(f"Torrent validation result: valid={valid}, path={torrent_path}")
        return torrent_path if valid else None

    async def is_valid_torrent(
        self, meta: dict, torrent_path: Path, torrenthash: str, torrent_client: str, print_err: bool = False
    ) -> tuple[bool, Path]:
        log.debug(f"Validating torrent {torrenthash} at {torrent_path}")

        torrenthash = (
            torrenthash.lower().strip()
            if torrent_client in ("qbit", "deluge")
            else torrenthash.upper().strip()
        )
        torrent_path = torrent_path.with_name(f"{torrenthash}.torrent")

        exists = await asyncio.to_thread(torrent_path.exists)
        if not exists:
            console.print(f"[bold yellow]{torrent_path} not found")
            return False, torrent_path

        try:
            torrent = await asyncio.to_thread(Torrent.read, torrent_path)
        except Exception as e:
            log.debug(f"Failed to read torrent file: {e}")
            return False, torrent_path

        valid, wrong_file = await asyncio.to_thread(self._check_torrent_validity, torrent, meta)
        log.ddebug(f"Torrent validity check: valid={valid}, wrong_file={wrong_file}")

        if valid:
            valid, err_print = self._validate_torrent_properties(torrent, torrenthash, wrong_file)
            log.ddebug(f"Torrent properties validation: valid={valid}, message={err_print}")
        else:
            err_print = "[bold yellow]Unwanted Files/Folders Identified"

        if print_err:
            console.print(err_print)
        return valid, torrent_path

    def _replace_path_prefix(self, path_str: str, old_prefix: str, new_prefix: str) -> str:
        if not old_prefix:
            return path_str

        path_posix = Path(path_str).as_posix()
        old_posix = Path(old_prefix).as_posix().rstrip("/")
        new_posix = Path(new_prefix).as_posix().rstrip("/")

        if old_posix and path_posix.lower().startswith(old_posix.lower()):
            if len(path_posix) == len(old_posix) or path_posix[len(old_posix)] == "/":
                return new_posix + path_posix[len(old_posix) :]

        return path_str

    def _normalize_torrent_path(self, torrent: dict, local_path: str, remote_path: str) -> Path:
        save_path = torrent.get("save_path") or torrent.get("downloadDir", "")
        name = torrent.get("name", "")
        if save_path:
            save_path = save_path.rstrip("/")
            raw_path = torrent.get("content_path", f"{save_path}/{name}")
        else:
            raw_path = torrent.get("content_path", name)

        normalized_str = self._replace_path_prefix(raw_path, remote_path, local_path)
        return Path(normalized_str)

    async def _search_torrents(
        self,
        torrents: list,
        meta: dict,
        torrent_storage_dir: Path,
        local_path: str,
        remote_path: str,
        client_type: str,
    ) -> str | None:
        meta_path = Path(meta["path"])
        meta_dir = meta_path.parent
        meta_file = meta_path.name if len(meta.get("filelist", [])) == 1 else None
        index_key = f"{client_type}_index.json"
        index_path = self.index_dir / index_key

        if index_key not in self.index_cache:
            await self._update_index(torrents, index_path, client_type, local_path, remote_path)

        index = self.index_cache.get(index_key, {})

        # Phase 1: Path-based matching (exact → inexact)
        for exact in (True, False):
            for torrent_hash, torrent_data in index.items():
                torrent_path = Path(torrent_data["path"])
                if self._is_torrent_match(torrent_path, meta_path, meta_dir, meta_file, exact):
                    valid, _ = await self.is_valid_torrent(
                        meta, torrent_storage_dir / f"{torrent_hash}.torrent", torrent_hash, client_type
                    )
                    if valid:
                        console.print(
                            f"[green]Found a matching .torrent with hash: [bold yellow]{torrent_hash}"
                        )
                        return torrent_hash

        # Phase 2: Content-based fallback for symlinked / renamed paths
        meta_name = meta_path.name.lower()
        expected_size = meta.get("content_size", 0)

        video_name = None
        video_size = None
        if (
            meta.get("category") == "MOVIE"
            and not meta.get("full_dir")
            and not meta.get("is_disc")
            and meta.get("video")
        ):
            vf = Path(meta["video"])
            video_name = vf.name.lower()
            try:
                video_size = await asyncio.to_thread(vf.stat)
                video_size = video_size.st_size
            except OSError:
                pass

        log.debug(
            f"Fallback filter: meta_name={meta_name!r}, expected_size={expected_size}, "
            f"video_name={video_name!r}, video_size={video_size}"
        )
        candidates = 0

        for torrent_hash, torrent_data in index.items():
            tname = torrent_data.get("name", "").lower()
            tsize = torrent_data.get("size")
            name_match = tname == meta_name
            size_match = expected_size and tsize == expected_size

            if video_name and not name_match:
                name_match = tname == video_name
            if video_size and not size_match:
                size_match = tsize == video_size

            if name_match or size_match:
                candidates += 1
            if not name_match and not size_match:
                continue

            valid, _ = await self.is_valid_torrent(
                meta, torrent_storage_dir / f"{torrent_hash}.torrent", torrent_hash, client_type
            )
            if valid:
                console.print(f"[green]Found a matching .torrent with hash: [bold yellow]{torrent_hash}")
                return torrent_hash

        log.debug(f"Fallback found {candidates} candidates, none valid")
        return None

    async def _update_index(
        self, torrents: list, index_path: Path, client_type: str, local_path: str, remote_path: str
    ) -> None:
        index = {}
        for torrent in torrents:
            torrent_hash = torrent.get("hash", torrent.get("hashString"))
            if not torrent_hash:
                continue
            torrent_path = self._normalize_torrent_path(torrent, local_path, remote_path).as_posix()
            tsize = torrent.get("size") or torrent.get("total_size") or torrent.get("totalSize")
            tname = torrent.get("name")
            index[torrent_hash] = {"path": torrent_path, "name": tname, "size": tsize}

        self.index_cache[f"{client_type}_index.json"] = index
        try:
            await asyncio.to_thread(index_path.write_text, json.dumps(index, indent=2))
        except Exception as e:
            log.debug(f"Failed to write index file {index_path}: {e}")

    async def search_qbit_for_torrent(self, meta: dict, client: dict) -> str | None:
        console.print("[green]Searching qBittorrent for an existing .torrent")
        torrent_storage_dir = Path(client.get("torrent_storage_dir", ""))
        local_path, remote_path = await self.remote_path_map(meta)

        qbt_client = qbittorrentapi.Client(
            host=client["qbit_url"],
            port=client["qbit_port"],
            username=client["qbit_user"],
            password=client["qbit_pass"],
            VERIFY_WEBUI_CERTIFICATE=client.get("VERIFY_WEBUI_CERTIFICATE", True),
        )
        try:
            await asyncio.to_thread(qbt_client.auth_log_in)
        except qbittorrentapi.LoginFailed:
            console.print("[bold red]INCORRECT QBIT LOGIN CREDENTIALS")
            return None

        torrents = await asyncio.to_thread(qbt_client.torrents_info)

        match = await self._search_torrents(
            torrents, meta, torrent_storage_dir, local_path, remote_path, client_type="qbit"
        )
        if match:
            return match

        log.debug("No matching torrent found in qBittorrent")
        return None

    async def search_transmission_for_torrent(self, meta: dict, client: dict) -> str | None:
        console.print("[green]Searching Transmission for an existing .torrent")
        torrent_storage_dir = Path(client.get("torrent_storage_dir", ""))

        async with niquests.AsyncSession(disable_http3=True) as http_client:
            transmission_user = client.get("transmission_user")
            transmission_pass = client.get("transmission_pass")
            if transmission_user and transmission_pass:
                http_client.auth = (transmission_user, transmission_pass)
            torrents_info = await self.transmission_rpc_call(
                http_client,
                "torrent-get",
                {"fields": ["id", "name", "hashString", "downloadDir", "totalSize"]},
            )
            if not torrents_info or "torrents" not in torrents_info["arguments"]:
                log.debug("No torrents found in Transmission")
                return None

            local_path, remote_path = await self.remote_path_map(meta)
            match = await self._search_torrents(
                torrents_info["arguments"]["torrents"],
                meta,
                torrent_storage_dir,
                local_path,
                remote_path,
                client_type="transmission",
            )
            if match:
                return match

        log.debug("No matching torrent found in Transmission")
        return None

    def _check_torrent_validity(self, torrent: Torrent, meta: dict) -> tuple[bool, bool]:
        if meta.get("full_dir", False):
            check_sets = [[Path(f).name for f in meta["filelist"]]]
        elif meta.get("is_disc"):
            meta_path = Path(meta["path"])
            torrent_common_path = Path(torrent.files[0]).parent if torrent.files else None
            if torrent_common_path and meta_path.name.lower() in torrent_common_path.as_posix().lower():
                return True, False
            return False, False
        elif meta.get("is_music"):
            if not meta.get("filelist"):
                log.debug("No filelist in meta, cannot validate torrent")
                return False, False
            check_sets = [[Path(f).name for f in meta["filelist"]]]
        else:
            if meta.get("category") == "MOVIE" and not meta.get("is_disc") and meta.get("video"):
                check_sets = [[Path(meta["video"]).name]]
            elif not meta.get("video_files"):
                log.debug("No video_files in meta, cannot validate torrent")
                return False, False
            else:
                check_sets = [
                    [Path(f).name for f in meta["video_files"] if "sample" not in Path(f).name.lower()],
                ]

        torrent_files = [Path(f).name for f in torrent.files]

        log.ddebug(f"Torrent files: {torrent_files}")
        meta_path_key = meta.get("path", "")
        if meta_path_key not in self._meta_files_logged:
            log.debug(f"Meta files: {check_sets[0]}")
            self._meta_files_logged.add(meta_path_key)

        torrent_base_dirs = {Path(f).parts[0] for f in torrent.files if len(Path(f).parts) > 1}
        if len(torrent_base_dirs) == 1:
            parent_dir = next(iter(torrent_base_dirs))
            expected_dir = Path(meta["path"]).name
            if parent_dir.lower() != expected_dir.lower():
                log.debug(f"Torrent folder '{parent_dir}' does not match expected directory '{expected_dir}'")

        for meta_files in check_sets:
            if len(torrent_files) == len(meta_files):
                if sorted(torrent_files) == sorted(meta_files):
                    return True, False
                return False, True

        return False, False

    def _is_torrent_match(
        self, torrent_path: Path, meta_path: Path, meta_dir: Path, meta_file: str | None, exact: bool
    ) -> bool:
        torrent_dir = torrent_path.parent
        torrent_file = torrent_path.name

        if exact:
            if torrent_path.as_posix().lower() == meta_path.as_posix().lower():
                log.ddebug("Exact path match found")
                return True
            if meta_file and (
                torrent_path.as_posix().lower() == Path(meta_dir / meta_file).as_posix().lower()
            ):
                log.ddebug("Exact file path match found")
                return True
        else:
            if torrent_dir.as_posix().lower() == meta_dir.as_posix().lower():
                log.ddebug("Directory match found")
                return True
            if meta_file and torrent_file.lower() == meta_file.lower():
                log.ddebug("File name match found")
                return True

        return False

    async def add_to_client(self, meta: dict, tracker: str) -> bool:
        torrent_path = (
            Path(meta["base_dir"]) / "tmp" / meta["uuid"] / f"[{tracker}]{meta['clean_name']}.torrent"
        )
        log.debug(f"Attempting to add torrent at {torrent_path} to client")

        if meta.get("no_seed", False):
            console.print("[bold red]--no_seed passed; torrent not added to client")
            console.print("[yellow]Add torrent manually to the client[/yellow]")
            return False

        exists = await asyncio.to_thread(torrent_path.exists)
        if not exists:
            log.debug(f"Torrent file not found at {torrent_path}")
            return False

        torrent = await asyncio.to_thread(Torrent.read, torrent_path)
        torrent_client = self.validate_requested_client(meta)
        if not torrent_client or torrent_client == "none":
            log.debug("No valid torrent client specified; skipping addition")
            return False

        client = self.config["TORRENT_CLIENTS"][torrent_client]

        client_type = client["torrent_client"].lower()
        local_path, remote_path = await self.remote_path_map(meta)

        log.debug(f"Adding torrent to {client_type}")
        console.print(f"[bold green]Adding to {client_type}")

        path = Path(meta["path"])

        client_methods = {
            "rtorrent": self._add_to_rtorrent,
            "qbit": self._add_to_qbittorrent,
            "deluge": self._add_to_deluge,
            "transmission": self._add_to_transmission,
            "watch": self._add_to_watch_folder,
        }

        client_method = client_methods.get(client_type)
        if client_method:
            try:
                result = await client_method(
                    path, torrent_path, torrent, meta, local_path, remote_path, client, tracker
                )
                return result if result is not None else True
            except (ConnectionError, qbittorrentapi.APIConnectionError) as exc:
                log.warning(
                    f"Client {client_type} connection failed for {meta.get('clean_name')}: "
                    f"{type(exc).__name__}: {exc}"
                )
                console.print(f"[bold red]Failed to add torrent to {client_type}: {type(exc).__name__}")
                console.print(f"[yellow]Check your {torrent_client} configuration and try again.[/yellow]")
                return False
        else:
            console.print(f"[bold red]Unsupported torrent client: {client_type}")
            return False

    async def remote_path_map(self, meta: dict) -> tuple[str, str]:
        torrent_client = self._get_torrent_client(meta)
        client = self.config["TORRENT_CLIENTS"][torrent_client]

        local_paths = client.get("local_path", "/LocalPath")
        remote_paths = client.get("remote_path", "/RemotePath")

        if not isinstance(local_paths, list):
            local_paths = [local_paths]
        if not isinstance(remote_paths, list):
            remote_paths = [remote_paths]

        if len(local_paths) != len(remote_paths):
            raise ValueError("local_path and remote_path must have the same number of entries")

        meta_path = Path(meta["path"]).as_posix()

        for local, remote in zip(local_paths, remote_paths):
            local_norm = Path(local).as_posix().rstrip("/")
            if meta_path == local_norm or meta_path.startswith(f"{local_norm}/"):
                return Path(local).as_posix(), Path(remote).as_posix()

        return Path(local_paths[0]).as_posix(), Path(remote_paths[0]).as_posix()

    async def _add_to_rtorrent(
        self,
        save_path: Path,
        torrent_path: Path,
        torrent: Torrent,
        meta: dict,
        local_path: str,
        remote_path: str,
        client: dict,
        tracker,
    ) -> None:
        rtorrent = xmlrpc.client.ServerProxy(client["rtorrent_url"], context=ssl.create_default_context())
        metainfo = await asyncio.to_thread(bencode.bread, torrent_path)

        try:
            fast_resume = await asyncio.to_thread(self.add_fast_resume, metainfo, str(save_path), torrent)
        except OSError as exc:
            console.print(f"[red]Error making fast-resume data ({exc})")
            raise

        encoded_fast_resume = await asyncio.to_thread(bencode.bencode, fast_resume)
        if encoded_fast_resume != metainfo:
            fr_file = torrent_path.with_suffix("-resume.torrent")
            console.print("Creating fast resume")
            await asyncio.to_thread(bencode.bwrite, fast_resume, fr_file)
        else:
            fr_file = torrent_path

        path_str = self._adjust_path(save_path, local_path, remote_path, torrent, meta["is_dir"])

        console.print("[bold yellow]Adding and starting torrent")
        await asyncio.to_thread(
            rtorrent.load.start_verbose, "", str(fr_file), f"d.directory_base.set={path_str}"
        )
        await asyncio.sleep(1)

        for label in (client.get("rtorrent_label"), meta.get("rtorrent_label")):
            if label:
                await asyncio.to_thread(rtorrent.d.custom1.set, torrent.infohash, label)

        if meta.get("debug"):
            console.print(f"[cyan]Path: {path_str}")

    async def _add_to_qbittorrent(
        self,
        save_path: Path,
        torrent_path: Path,
        torrent: Torrent,
        meta: dict,
        local_path: str,
        remote_path: str,
        client: dict,
        tracker: str,
    ) -> None:
        path_str = self._adjust_path(save_path, local_path, remote_path, torrent, meta["is_dir"])
        if not path_str.endswith("/"):
            path_str += "/"

        qbt_client = qbittorrentapi.Client(
            host=client["qbit_url"],
            port=client["qbit_port"],
            username=client["qbit_user"],
            password=client["qbit_pass"],
            VERIFY_WEBUI_CERTIFICATE=client.get("VERIFY_WEBUI_CERTIFICATE", True),
        )

        console.print("[bold yellow]Adding and rechecking torrent")
        try:
            await asyncio.to_thread(qbt_client.auth_log_in)
        except qbittorrentapi.LoginFailed:
            console.print("[bold red]INCORRECT QBIT LOGIN CREDENTIALS")
            return

        auto_management = self._check_auto_management(path_str, client.get("automatic_management_paths", ""))
        try:
            await asyncio.to_thread(
                qbt_client.torrents_add,
                torrent_files=torrent.dump(),
                save_path=path_str,
                use_auto_torrent_management=auto_management,
                is_skip_checking=True,
                content_layout=client.get("content_layout", "Original"),
                category=meta.get("qbit_cat", client.get("qbit_cat")),
            )
        except qbittorrentapi.Conflict409Error:
            log.warning(f"Torrent already exists in qBittorrent (infohash: {torrent.infohash}). Skipping.")
            return

        await self._wait_for_torrent(qbt_client, torrent.infohash)
        await self._apply_qbit_tags(qbt_client, torrent.infohash, client, meta, tracker)
        console.print(f"Added to: {path_str}")

    async def _add_to_deluge(
        self,
        save_path: Path,
        torrent_path: Path,
        torrent: Torrent,
        meta: dict,
        local_path: str,
        remote_path: str,
        client: dict,
        tracker: str,
    ) -> None:
        path_str = self._adjust_path(save_path, local_path, remote_path, torrent, meta["is_dir"])
        client_obj = DelugeRPCClient(
            client["deluge_url"], int(client["deluge_port"]), client["deluge_user"], client["deluge_pass"]
        )
        await asyncio.to_thread(client_obj.connect)

        if client_obj.connected:
            try:
                console.print("Connected to Deluge")
                await asyncio.to_thread(
                    client_obj.call,
                    "core.add_torrent_file",
                    str(torrent_path),
                    base64.b64encode(torrent.dump()).decode("ascii"),
                    {"download_location": path_str, "seed_mode": True},
                )
                if meta.get("debug"):
                    console.print(f"[cyan]Path: {path_str}")
            finally:
                await asyncio.to_thread(client_obj.disconnect)
        else:
            console.print("[bold red]Unable to connect to Deluge")

    async def _add_to_transmission(
        self,
        save_path: Path,
        torrent_path: Path,
        torrent: Torrent,
        meta: dict,
        local_path: str,
        remote_path: str,
        client: dict,
        tracker: str,
    ) -> None:
        path_str = self._adjust_path(save_path, local_path, remote_path, torrent, meta["is_dir"])

        async with niquests.AsyncSession(disable_http3=True) as http_client:
            transmission_user = client.get("transmission_user")
            transmission_pass = client.get("transmission_pass")
            if transmission_user and transmission_pass:
                http_client.auth = (transmission_user, transmission_pass)

            torrent_data = await asyncio.to_thread(torrent_path.read_bytes)
            if not torrent_data:
                console.print("[bold red]Torrent data is empty or unreadable")
                return

            data = {"metainfo": base64.b64encode(torrent_data).decode("ascii"), "download-dir": path_str}
            result = await self.transmission_rpc_call(http_client, "torrent-add", data)
            console.print(
                "[bold green]Torrent added to Transmission"
                if result and result["result"] == "success"
                else f"[bold red]Error from Transmission: {result}"
            )

    async def _add_to_watch_folder(
        self,
        save_path: Path,
        torrent_path: Path,
        torrent: Torrent,
        meta: dict,
        local_path: str,
        remote_path: str,
        client: dict,
        tracker: str,
    ) -> None:
        base_folder = Path(client["watch_folder"])
        # Route the .torrent into a category subfolder when a {category} token
        # or a category_map is configured. This keeps qui/qBittorrent category
        # routing working without manual selection.
        category = (meta.get("category") or "").upper()
        category_map = client.get("category_map") or {}
        mapped = category_map.get(category)
        if mapped is None:
            # Default: lowercase the uploadrr category (MOVIE -> movie, TV -> tv, ...)
            mapped = category.lower() if category else ""
        if mapped:
            target_dir = base_folder / mapped
            await asyncio.to_thread(target_dir.mkdir, parents=True, exist_ok=True)
            dest = target_dir / torrent_path.name
        else:
            dest = base_folder / torrent_path.name
        await asyncio.to_thread(shutil.copy, torrent_path, dest)
        log.info(f"[cyan]Torrent copied to watch folder: {dest}[/cyan]")

    def add_fast_resume(self, metainfo: dict, datapath: str, torrent: Torrent) -> dict:
        files = metainfo["info"].get("files")
        single = files is None
        if single:
            datapath = str(Path(datapath) / metainfo["info"]["name"]) if Path(datapath).is_dir() else datapath
            files = [Bunch(path=[datapath], length=metainfo["info"]["length"])]

        resume = metainfo.setdefault("libtorrent_resume", {})
        resume["bitfield"] = len(metainfo["info"]["pieces"]) // 20
        resume["files"] = []
        piece_length = metainfo["info"]["piece length"]
        offset = 0

        for fileinfo in files:
            filepath = Path(datapath) / Path(*fileinfo["path"]) if not single else Path(fileinfo["path"][0])

            stat = filepath.stat()
            if stat.st_size != fileinfo["length"]:
                raise OSError(
                    errno.EINVAL,
                    f"File size mismatch for {filepath} [is {stat.st_size}, expected {fileinfo['length']}]",
                )

            resume["files"].append(
                {
                    "priority": 1,
                    "mtime": int(stat.st_mtime),
                    "completed": (offset + fileinfo["length"] + piece_length - 1) // piece_length
                    - offset // piece_length,
                }
            )
            offset += fileinfo["length"]
        return metainfo

    async def transmission_rpc_call(
        self, client: niquests.AsyncSession, method: str, arguments: dict | None = None
    ) -> dict | None:
        url = f"{self.config['TORRENT_CLIENTS']['transmission']['transmission_url']}/transmission/rpc"
        data = {"method": method, "arguments": arguments or {}}

        try:
            response = await client.post(url, json=data)
            if response.status_code == 409:
                session_id = response.headers.get("X-Transmission-Session-Id")
                if session_id:
                    headers = {"X-Transmission-Session-Id": session_id}
                    response = await client.post(url, json=data, headers=headers)
                    return response.json()
            elif response.status_code == 200:
                return response.json()
            else:
                console.print(f"Transmission RPC error: HTTP {response.status_code}")
        except Exception as e:
            base_url = url.split("/transmission")[0]
            console.print(f"[bold red]Could not connect to Transmission at {base_url}")
            console.print("[bold yellow]Verify Transmission is running, URL and credentials are correct.")
            log.error(f"[bold dim]{type(e).__name__}: {e}")
        return None

    def _get_torrent_client(self, meta: dict) -> str | None:
        return meta.get("client") or self.config["DEFAULT"]["default_torrent_client"]

    async def _get_valid_torrenthash(
        self, meta: dict, torrent_storage_dir: Path, client_type: str, client: dict
    ) -> str | None:
        for key in ("torrenthash", "ext_torrenthash"):
            if meta.get(key):
                valid, _ = await self.is_valid_torrent(
                    meta, torrent_storage_dir / f"{meta[key]}.torrent", meta[key], client_type, print_err=True
                )
                if valid:
                    log.debug(f"Valid torrent hash found: {meta[key]}")
                    return meta[key]

        if client_type == "qbit" and client.get("enable_search"):
            return await self.search_qbit_for_torrent(meta, client)
        if client_type == "transmission" and client.get("enable_search"):
            return await self.search_transmission_for_torrent(meta, client)
        console.print("[bold yellow]No valid .torrent found")
        return None

    def _validate_torrent_properties(
        self, torrent: Torrent, torrenthash: str, wrong_file: bool
    ) -> tuple[bool, str]:
        log.debug(f"Validating torrent properties: pieces={torrent.pieces}, piece_size={torrent.piece_size}")
        if wrong_file:
            return False, "[bold red]Provided .torrent has unexpected files"
        if (torrent.pieces >= 7000 and torrent.piece_size < 8388608) or (
            torrent.pieces >= 4000 and torrent.piece_size < 4194304
        ):
            return False, "[bold yellow]Too many pieces exist in current hash. REHASHING"
        if torrent.piece_size < 32768:
            return False, "[bold yellow]Piece size too small to reuse"
        return True, f"[bold green]REUSING .torrent with infohash: [bold yellow]{torrenthash}"

    def _adjust_path(
        self, path: Path, local_path: str, remote_path: str, torrent: Torrent, is_dir: bool
    ) -> str:
        path_str = path.as_posix() if torrent.mode == "singlefile" and is_dir else path.parent.as_posix()

        return self._replace_path_prefix(path_str, local_path, remote_path)

    def _check_auto_management(self, path: str, am_config: str) -> bool:
        if isinstance(am_config, list):
            return any(Path(each).as_posix().lower() in path.lower() for each in am_config)
        return bool(am_config.strip() and Path(am_config).as_posix().lower() in path.lower())

    async def _wait_for_torrent(self, qbt_client: qbittorrentapi.Client, infohash: str) -> None:
        for _ in range(30):
            info = await asyncio.to_thread(qbt_client.torrents_info, torrent_hashes=infohash)
            if info:
                await asyncio.to_thread(qbt_client.torrents_resume, infohash)
                return
            await asyncio.sleep(1)
        console.print(f"[bold red]Warning: Torrent with hash {infohash} not found after adding.")

    async def _apply_qbit_tags(
        self, qbt_client: qbittorrentapi.Client, infohash: str, client: dict, meta: dict, tracker: str
    ) -> None:
        tags = set()
        if client.get("qbit_tag"):
            tags.add(client["qbit_tag"])
        if meta.get("qbit_tag"):
            tags.add(meta["qbit_tag"])
        tracker_tag = self.config["TRACKERS"].get(tracker, {}).get("qbit_tag")
        if tracker_tag:
            tags.add(tracker_tag)

        if tags:
            await asyncio.to_thread(qbt_client.torrents_add_tags, tags=list(tags), torrent_hashes=infohash)
