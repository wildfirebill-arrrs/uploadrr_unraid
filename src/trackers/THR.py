import asyncio
import glob
import json
import os
import re

import niquests
from rich.prompt import Confirm, Prompt
from unidecode import unidecode

from src.utils.console import console
from src.utils.torrentman import edit_torrent
from src.version import user_agent


class THR:
    def __init__(self, config):
        self.config = config
        self.type = "other"  # Custom Nexus?
        self.source = "[https://www.torrenthr.org] TorrentHR.org"
        pid = config["TRACKERS"][self.tracker].get("PID", "PASSKEY_HERE")
        self.announce_url = f"http://www.torrenthr.org/announce.php?passkey={pid}"
        self.username = config["TRACKERS"]["THR"].get("username")
        self.password = config["TRACKERS"]["THR"].get("password")
        self.banned_groups = [""]
        pass

    async def upload(self, session, meta):
        await edit_torrent(meta, self.tracker, self.source_flag, self.announce_url)
        cat_id = await self.get_cat_id(meta)
        subs = self.get_subtitles(meta)
        pronfo = await self.edit_desc(meta)  # noqa: F841
        thr_name = unidecode(meta["name"].replace("DD+", "DDP"))

        # Confirm the correct naming order for FL
        console.print(f"THR name: {thr_name}")
        if not meta.get("unattended", False):
            thr_confirm = Prompt.ask("Correct?", choices=["yes", "no"], default="no")
            if thr_confirm != "yes":
                thr_name_manually = Prompt.ask("Please enter a proper name", default="")
                if thr_name_manually == "":
                    console.print("No proper name given", style="bold red")
                    console.print("Aborting...")
                    return
                else:
                    thr_name = thr_name_manually

        torrent_name = re.sub(r"[^0-9a-zA-Z. '\-\[\]]+", " ", thr_name)

        if meta.get("is_disc", "") == "BDMV":
            mi_file = None
            # bd_file = f"{meta['base_dir']}/tmp/{meta['uuid']}/BD_SUMMARY_00.txt", 'r', encoding='utf-8'
        else:
            mi_file = os.path.abspath(f"{meta['base_dir']}/tmp/{meta['uuid']}/MEDIAINFO.txt")
            if (
                not mi_file.exists()
            ):  # backward support first incase people saved /tmp TODO establish a -clean-mi to avoid this
                mi_file = os.path.abspath(f"{meta['base_dir']}/tmp/{meta['uuid']}/MEDIAINFO.txt")
            with open(mi_file) as f:
                mi_file = f.read()
                f.close()
            # bd_file = None

        with open(f"{meta['base_dir']}/tmp/{meta['uuid']}/[THR]DESCRIPTION.txt", encoding="utf-8") as f:
            desc = f.read()
            f.close()

        torrent_path = os.path.abspath(
            f"{meta['base_dir']}/tmp/{meta['uuid']}/[THR]{meta['clean_name']}.torrent"
        )
        with open(torrent_path, "rb") as f:
            tfile = f.read()
            f.close()

        # Upload Form
        url = "https://www.torrenthr.org/takeupload.php"
        files = {"tfile": (f"{torrent_name}.torrent", tfile)}
        payload = {
            "name": thr_name,
            "descr": desc,
            "type": cat_id,
            "url": f"https://www.imdb.com/title/tt{meta.get('imdb_id').replace('tt', '')}/",
            "tube": meta.get("youtube", ""),
        }
        headers = {"User-Agent": user_agent()}
        # If pronfo fails, put mediainfo into THR parser
        if meta.get("is_disc", "") != "BDMV":
            files["nfo"] = ("MEDIAINFO.txt", mi_file)
        if subs != []:
            payload["subs[]"] = tuple(subs)

        if not meta["debug"]:
            thr_upload_prompt = True
        else:
            thr_upload_prompt = Confirm.ask("Send to takeupload.php?", default=False)

        if thr_upload_prompt:
            await asyncio.sleep(0.5)
            response = session.post(url=url, files=files, data=payload, headers=headers)
            try:
                if meta["debug"]:
                    console.print(response.text)
                if response.url.endswith("uploaded=1"):
                    console.print(f"[green]Successfully Uploaded at: {response.url}")
                # Check if actually uploaded
            except Exception:
                if meta["debug"]:
                    console.print(response.text)
                console.print("It may have uploaded, go check")
                return
        else:
            console.print("[cyan]Request Data:")
            console.print(payload)

    async def get_cat_id(self, meta):
        if meta["category"] == "MOVIE":
            if meta.get("is_disc") == "BMDV":
                cat = "40"
            elif meta.get("is_disc") == "DVD" or meta.get("is_disc") == "HDDVD":
                cat = "14"
            else:
                cat = "4" if meta.get("sd") == 1 else "17"
        elif meta["category"] == "TV":
            cat = "7" if meta.get("sd") == 1 else "34"
        elif meta.get("anime") is not False:
            cat = "31"
        return cat

    def get_subtitles(self, meta):
        subs = []
        sub_langs = []
        if meta.get("is_disc", "") != "BDMV":
            with open(f"{meta.get('base_dir')}/tmp/{meta.get('uuid')}/MediaInfo.json", encoding="utf-8") as f:
                mi = json.load(f)
            for track in mi["media"]["track"]:
                if track["@type"] == "Text":
                    language = track.get("Language")
                    if language in ["hr", "en", "bs", "sr", "sl"]:
                        if language not in sub_langs:
                            sub_langs.append(language)
        else:
            for sub in meta["bdinfo"]["subtitles"]:
                if sub not in sub_langs:
                    sub_langs.append(sub)
        if sub_langs != []:
            subs = []
            sub_lang_map = {
                "hr": 1,
                "en": 2,
                "bs": 3,
                "sr": 4,
                "sl": 5,
                "Croatian": 1,
                "English": 2,
                "Bosnian": 3,
                "Serbian": 4,
                "Slovenian": 5,
            }
            for sub in sub_langs:
                language = sub_lang_map.get(sub)
                if language is not None:
                    subs.append(language)
        return subs

    async def edit_desc(self, meta):
        pronfo = False
        base = open(f"{meta['base_dir']}/tmp/{meta['uuid']}/DESCRIPTION.txt").read()
        with open(
            f"{meta['base_dir']}/tmp/{meta['uuid']}/[THR]DESCRIPTION.txt", "w", encoding="utf-8"
        ) as desc:
            tag = "" if meta["tag"] == "" else f" / {meta['tag'][1:]}"
            res = meta["source"] if meta["is_disc"] == "DVD" else meta["resolution"]
            desc.write("[quote=Info]")
            name_aka = f"{meta['title']} {meta['aka']} {meta['year']}"
            name_aka = unidecode(name_aka)
            # name_aka = re.sub(r"[^0-9a-zA-Z. '\-\[\]]+", " ", name_aka)
            desc.write(f"Name: {' '.join(name_aka.split())}\n\n")
            desc.write(f"Overview: {meta['overview']}\n\n")
            desc.write(f"{res} / {meta['type']}{tag}\n\n")
            desc.write(f"Category: {meta['category']}\n")
            desc.write(f"TMDB: https://www.themoviedb.org/{meta['category'].lower()}/{meta['tmdb']}\n")
            if meta["imdb_id"] != "0":
                desc.write(f"IMDb: https://www.imdb.com/title/tt{meta['imdb_id']}\n")
            if meta["tvdb_id"] != "0":
                desc.write(f"TVDB: https://www.thetvdb.com/?id={meta['tvdb_id']}&tab=series\n")
            desc.write("[/quote]")
            desc.write(base)
            # REHOST IMAGES
            os.chdir(f"{meta['base_dir']}/tmp/{meta['uuid']}")
            image_glob = glob.glob("*.png")
            if "POSTER.png" in image_glob:
                image_glob.remove("POSTER.png")
            image_list = []
            for image in image_glob:
                url = "https://img2.torrenthr.org/api/1/upload"
                data = {
                    "key": self.config["TRACKERS"]["THR"].get("img_api")
                    # 'source' : base64.b64encode(open(image, "rb").read()).decode('utf8')
                }
                with open(image, "rb") as f:
                    files = {"source": f}
                    response = niquests.post(url, data=data, files=files)
                try:
                    response = response.json()
                    # med_url = response['image']['medium']['url']
                    img_url = response["image"]["url"]
                    image_list.append(img_url)
                except json.decoder.JSONDecodeError:
                    console.print("[yellow]Failed to upload image")
                    console.print(response.text)
                except KeyError:
                    console.print("[yellow]Failed to upload image")
                    console.print(response)
                await asyncio.sleep(1)
            desc.write("[align=center]")
            if meta.get("is_disc", "") == "BDMV":
                with open(f"{meta['base_dir']}/tmp/{meta['uuid']}/BD_SUMMARY_00.txt") as bd_file:
                    desc.write(f"[nfo]{bd_file.read()}[/nfo]")
                    bd_file.close()
            else:
                # ProNFO
                pronfo_url = f"https://www.pronfo.com/api/v1/access/upload/{self.config['TRACKERS']['THR'].get('pronfo_api_key', '')}"
                data = {
                    "content": open(f"{meta['base_dir']}/tmp/{meta['uuid']}/MEDIAINFO.txt").read(),
                    "theme": self.config["TRACKERS"]["THR"].get("pronfo_theme", "gray"),
                    "rapi": self.config["TRACKERS"]["THR"].get("pronfo_rapi_id"),
                }
                response = niquests.post(pronfo_url, data=data)
                try:
                    response = response.json()
                    if response.get("error", True) is not False:
                        mi_img = response.get("url")
                        desc.write(f"\n[img]{mi_img}[/img]\n")
                        pronfo = True
                except Exception:
                    console.print("[bold red]Error parsing pronfo response, using THR parser instead")
                    if meta["debug"]:
                        console.print(f"[red]{response}")
                        console.print(response.text)

            for each in image_list[: int(meta["screens"])]:
                desc.write(f"\n[img]{each}[/img]\n")
            # if pronfo:
            #     with open(os.path.abspath(f"{meta['base_dir']}/tmp/{meta['uuid']}/MEDIAINFO.txt"), 'r') as mi_file:
            #         full_mi = mi_file.read()
            #         desc.write(f"[/align]\n[hide=FULL MEDIAINFO]{full_mi}[/hide][align=center]")
            #         mi_file.close()
            desc.write(
                "\n\n[size=2][url=https://www.torrenthr.org/forums.php?action=viewtopic&topicid=8977]Created by L4G's Upload Assistant[/url][/size][/align]"
            )
            desc.close()
        return pronfo

    def search_existing(self, session, imdb_id):
        from selectolax.parser import HTMLParser

        imdb_id = imdb_id.replace("tt", "")
        search_url = f"https://www.torrenthr.org/browse.php?search={imdb_id}&blah=2&incldead=1"
        search = session.get(search_url)
        tree = HTMLParser(search.text)
        dupes = {}
        for link in tree.css("a[href]"):
            href = link.attributes.get("href", "")
            if href.startswith("details.php"):
                onmousemove = link.attributes.get("onmousemove")
                if onmousemove:
                    dupe = onmousemove.split("','/images")
                    dupe = dupe[0].replace("return overlibImage('", "")
                    size = 0
                    dupes[dupe] = size
        return dupes

    def login(self, session):
        url = "https://www.torrenthr.org/takelogin.php"
        payload = {"username": self.username, "password": self.password, "ssl": "yes"}
        headers = {"User-Agent": user_agent()}
        resp = session.post(url, headers=headers, data=payload)
        if resp.url == "https://www.torrenthr.org/index.php":
            console.print("[green]Successfully logged in")
        return session
