import re
import traceback

import niquests

from src.utils.bbcode import BBCODE
from src.utils.console import console


class COMMON:
    def __init__(self, config):
        self.config = config

    async def unit3d_edit_desc(self, meta, tracker, comparison=False, desc_header="", whitelist=None):
        raise NotImplementedError("This method should be called on DescGenerator instance.")

    async def unit3d_torrent_info(self, tracker, torrent_url, id):
        tmdb = imdb = tvdb = description = category = infohash = mal = None
        imagelist = []
        params = {"api_token": self.config["TRACKERS"][tracker].get("api_key", "")}
        url = f"{torrent_url}{id}"
        response = niquests.get(url=url, params=params)
        try:
            response = response.json()
            attributes = response["attributes"]
            category = attributes.get("category")
            description = attributes.get("description")
            tmdb = attributes.get("tmdb_id")
            tvdb = attributes.get("tvdb_id")
            mal = attributes.get("mal_id")
            imdb = attributes.get("imdb_id")
            infohash = attributes.get("info_hash")

            bbcode = BBCODE()  # Instance for BBCode transformations
            description, imagelist = bbcode.clean_unit3d_description(description, torrent_url)
            console.print(f"[green]Successfully grabbed description from {tracker}")
        except Exception:
            console.print(traceback.print_exc())
            console.print(f"[yellow]Invalid Response from {tracker} API.")

        return tmdb, imdb, tvdb, mal, description, category, infohash, imagelist

    async def parseCookieFile(self, cookiefile):
        """Parse a cookies.txt file and return a dictionary of key value pairs
        compatible with niquests."""

        cookies = {}
        with open(cookiefile) as fp:
            for line in fp:
                if not re.match(r"^#", line) and not re.match(r"^\s*$", line):  # Simplified regex check
                    lineFields = re.split(r"\s+", line.strip())  # Use regex for splitting on whitespace
                    if len(lineFields) >= 7:  # Ensure enough fields exist
                        cookies[lineFields[5]] = lineFields[6]
        return cookies
