import os
import urllib.parse
from typing import Optional

import niquests

from src.utils.console import console, log


async def is_scene(video: str, imdb: Optional[str] = None) -> tuple[str, bool, Optional[str]]:
    """Check if a video is a scene release using SRRDB."""
    scene = False
    base = urllib.parse.quote(os.path.splitext(os.path.basename(video))[0])
    search_url = f"https://api.srrdb.com/v1/search/r:{base}"

    async with niquests.AsyncSession(disable_http3=True) as client:
        try:
            response = await client.get(search_url)
            response.raise_for_status()
            data = response.json()
            if int(data.get("resultsCount", 0)) > 0:
                video = f"{data['results'][0]['release']}.mkv"
                scene = True
                console.print(f"[green]SRRDB: Matched to {data['results'][0]['release']}")

                if not imdb:
                    release_name = urllib.parse.quote(data["results"][0]["release"])
                    r = await client.get(f"https://api.srrdb.com/v1/imdb/{release_name}")
                    r.raise_for_status()
                    imdb_data = r.json()
                    imdb = imdb_data["releases"][0].get("imdb", imdb) if imdb_data.get("releases") else imdb
        except niquests.HTTPError as e:
            console.print("[yellow]SRRDB: No match found or request failed")
            log.debug(f"Exception occurred: {e}")
        except niquests.RequestException as e:
            console.print("[yellow]SRRDB: Network error — could not reach api.srrdb.com")
            log.warning(f"SRRDB lookup failed: {e}")
    return video, scene, imdb
