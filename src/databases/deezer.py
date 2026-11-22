from __future__ import annotations

from typing import Any

import niquests

from src.utils.console import log


class DeezerHandler:
    BASE_URL = "https://api.deezer.com/"

    def __init__(self, user_agent: str):
        self._client = niquests.AsyncSession(disable_http3=True)
        self._client.headers["User-Agent"] = user_agent

    async def close(self) -> None:
        self._client.close()

    async def fetch_album(self, album_id: int) -> dict[str, Any] | None:
        url = f"{self.BASE_URL}album/{album_id}"

        try:
            log.debug(f"Fetching album data for ID: {album_id}")
            response = await self._client.get(url)
            response.raise_for_status()
            return response.json()
        except niquests.HTTPError as e:
            log.error(f"Deezer API Error: {e.response.status_code if e.response else 'no response'}")
            return None
        except niquests.RequestException as e:
            log.error(f"Network error while fetching album {album_id}: {e!s}")
            return None

    async def fetch_album_by_upc(self, upc: str) -> dict[str, Any] | None:
        url = f"{self.BASE_URL}album/upc:{upc}"

        try:
            log.debug(f"Fetching album data for UPC: {upc}")
            response = await self._client.get(url)
            response.raise_for_status()
            return response.json()
        except niquests.HTTPError as e:
            log.error(f"Deezer API Error: {e.response.status_code if e.response else 'no response'}")
            return None
        except niquests.RequestException as e:
            log.error(f"Network error while fetching album by UPC {upc}: {e!s}")
            return None

    async def search_albums(self, query: str, limit: int = 10) -> dict[str, Any]:
        url = f"{self.BASE_URL}search/album"
        params = {"q": query, "limit": limit}

        try:
            log.debug(f"Searching albums with query: {query}")
            response = await self._client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except niquests.HTTPError as e:
            log.error(f"Deezer API Error: {e.response.status_code if e.response else 'no response'}")
            return {"data": []}
        except niquests.RequestException as e:
            log.error(f"Network error while searching albums: {e!s}")
            return {"data": []}

    async def search_albums_filtered(
        self, query: str, track_count: int, limit: int = 10
    ) -> dict[str, Any] | None:
        search = await self.search_albums(query, limit)
        candidates = search.get("data", [])
        if not candidates:
            return None

        best = next((c for c in candidates if c["nb_tracks"] == track_count), candidates[0])
        return await self.fetch_album(best["id"])
