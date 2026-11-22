import asyncio

import niquests
from rich.pretty import Pretty

from src.utils.console import console, log
from src.utils.descgen import DescGenerator
from src.utils.torrentman import edit_torrent
from src.version import user_agent


class R4E:
    def __init__(self, config):
        self.config = config
        self.type = "unit3d"
        self.tracker = "R4E"
        self.source_flag = "R4E"
        self.domain = "https://racing4everyone.eu"
        self.search_url = f"{self.domain}/api/torrents/filter"
        self.upload_url = f"{self.domain}/api/torrents/upload"
        pid = config["TRACKERS"][self.tracker].get("PID", "PASSKEY_HERE")
        self.announce_url = f"{self.domain}/announce/{pid}"
        self.banned_groups = [""]
        pass

    async def get_cat_id(self, category_name, genres):
        genres = list(map(str.strip, (genres or "").lower().split(",")))
        if category_name == "MOVIE":
            category_id = "66" if "documentary" in genres else "70"  # Motorsports Movie

        elif category_name == "TV":
            category_id = "2" if "documentary" in genres else "79"  # TV Series
        else:
            category_id = "24"
        return category_id

    async def get_type_id(self, type):
        type_id = {
            "8640p": "2160p",
            "4320p": "2160p",
            "2160p": "2160p",
            "1440p": "1080p",
            "1080p": "1080p",
            "1080i": "1080i",
            "720p": "720p",
            "576p": "SD",
            "576i": "SD",
            "480p": "SD",
            "480i": "SD",
        }.get(type, "10")
        return type_id

    ###############################################################
    ######   STOP HERE UNLESS EXTRA MODIFICATION IS NEEDED   ######
    ###############################################################

    async def upload(self, meta):
        descgen = DescGenerator(config=self.config, base_dir=meta["base_dir"])
        await edit_torrent(meta, self.tracker, self.source_flag, self.announce_url)
        cat_id = await self.get_cat_id(meta["category"])
        type_id = await self.get_type_id(meta["type"])
        await descgen.edit_desc(meta, self.tracker)
        name = meta.get(f"{self.tracker}_name") or await self.edit_name(meta)
        manual_name = meta.get("manual_name")
        anon = 1 if meta["anon"] != 0 or self.config["TRACKERS"][self.tracker].get("anon", False) else 0

        if meta["bdinfo"] is not None:
            mi_dump = None
            bd_dump = open(
                f"{meta['base_dir']}/tmp/{meta['uuid']}/BD_SUMMARY_00.txt", encoding="utf-8"
            ).read()
        else:
            mi_dump = open(f"{meta['base_dir']}/tmp/{meta['uuid']}/MEDIAINFO.txt", encoding="utf-8").read()
            bd_dump = None
        desc = open(
            f"{meta['base_dir']}/tmp/{meta['uuid']}/[{self.tracker}]DESCRIPTION.txt", encoding="utf-8"
        ).read()
        torrent_path = f"{meta['base_dir']}/tmp/{meta['uuid']}/[{self.tracker}]{meta['clean_name']}.torrent"
        nfo_file = meta.get("nfo_file", None)
        data = {
            "name": manual_name if manual_name else name,
            "description": desc,
            "mediainfo": mi_dump,
            "bdinfo": bd_dump,
            "category_id": cat_id,
            "type_id": type_id,
            "tmdb": meta["tmdb"],
            "imdb": meta["imdb_id"].replace("tt", ""),
            "tvdb": meta["tvdb_id"],
            "mal": meta["mal_id"],
            "igdb": 0,
            "anonymous": anon,
            "stream": meta["stream"],
            "sd": meta["sd"],
            "keywords": meta["keywords"],
            # 'personal_release' : int(meta.get('personalrelease', False)), # NOT IMPLEMENTED on R4E
            # 'internal' : 0,
            # 'featured' : 0,
            # 'free' : 0,
            # 'doubleup' : 0,
            # 'sticky' : 0,
        }
        # Internal
        # if self.config['TRACKERS'][self.tracker].get('internal', False):
        #     if meta['tag'] != "" and (meta['tag'][1:] in self.config['TRACKERS'][self.tracker].get('internal_groups', [])):
        #         data['internal'] = 1

        if meta.get("category") == "TV":
            data["season_number"] = meta.get("season_int", "0")
            data["episode_number"] = meta.get("episode_int", "0")
        headers = {"User-Agent": user_agent()}
        params = {"api_token": self.config["TRACKERS"][self.tracker]["api_key"].strip()}

        if meta["debug"]:
            log.debug("[blue]DATA 2 SEND[/blue]:")
            console.print(Pretty(data))

        else:
            from contextlib import ExitStack

            with ExitStack() as stack:
                files = {"torrent": stack.enter_context(open(torrent_path, "rb"))}
                if (
                    nfo_file
                    and not meta.get("no_nfo", False)
                    and self.config["TRACKERS"][self.tracker].get("nfo", True)
                ):
                    files["nfo"] = stack.enter_context(open(nfo_file, "rb"))

                success = "Unknown"
                try:
                    response = niquests.post(
                        url=self.upload_url, files=files, data=data, headers=headers, params=params
                    )

                    if response.status_code >= 200 and response.status_code < 300:
                        response_json = response.json()
                        success = response_json.get("success", False)
                        data = response_json.get("data", {})

                        if not success:
                            message = response_json.get("message", "No message provided")
                            console.print(f"[red]Upload failed: {message}[/red]")
                            if data:
                                console.print(f"[cyan]Error details:[/cyan] {data}")

                    else:
                        try:
                            from selectolax.parser import HTMLParser

                            tree = HTMLParser(response.text)
                            error_heading = tree.css_first(".error__heading")
                            error_body = tree.css_first(".error__body")

                            if error_heading and error_body:
                                console.print(f"[red]{error_heading.text().strip()}[/red]")
                                console.print(f"[b][yellow]{error_body.text().strip()}[/yellow][/b]")
                            else:
                                console.print(f"[red]Encountered HTTP Error: {response.status_code}[/red]")
                                console.print(f"[blue]Server Response[/blue]: {response.text}")
                        except Exception as parse_error:
                            console.print(f"[red]Failed to parse error response: {parse_error}[/red]")
                            console.print(f"[blue]Server Response[/blue]: {response.text}")

                        success = False
                        data = {}

                except niquests.exceptions.RequestException as e:
                    console.print(f"[red]Encountered Error: {e}[/red]")
                    data = {}

                if success == "Unknown":
                    console.print("[bold yellow]Status of upload is unknown, please go check..")

                elif success:
                    console.print("[bold green]Torrent uploaded successfully!")
                else:
                    console.print("[bold red]Torrent upload failed.")

            return success

    async def edit_name(self, meta):
        name = meta["name"]
        meta[f"{self.tracker}_name"] = name
        return name

    async def search_existing(self, meta):
        dupes = {}
        console.print("[yellow]Searching for existing torrents on site...")
        params = {
            "api_token": self.config["TRACKERS"][self.tracker]["api_key"].strip(),
            "tmdbId": meta.get("tmdb", 0),
            "categories[]": await self.get_cat_id(meta["category"]),
            "types[]": await self.get_type_id(meta["type"]),
            "name": "",
        }
        if meta["category"] == "TV":
            params["name"] = f"{meta.get('season', '')}{meta.get('episode', '')}"
        if meta.get("edition", "") != "":
            params["name"] = params["name"] + meta["edition"]
        try:
            response = niquests.get(url=self.search_url, params=params)
            response = response.json()
            for each in response["data"]:
                result = each["attributes"]["name"]
                size = each["attributes"]["size"]
                dupes[result] = size
        except Exception:
            console.print(
                "[bold red]Unable to search for existing torrents on site. Either the site is down or your API key is incorrect"
            )
            await asyncio.sleep(5)

        return dupes
