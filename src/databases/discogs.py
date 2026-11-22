from __future__ import annotations

from typing import Any

import niquests

from src.utils.console import log


class DiscogsHandler:
    BASE_URL = "https://api.discogs.com"

    def __init__(self, user_agent: str):
        self._client = niquests.AsyncSession(disable_http3=True)
        self._client.headers["User-Agent"] = user_agent

    async def close(self) -> None:
        self._client.close()

    async def fetch_discogs_release(self, discogs_id: str) -> dict[str, Any] | None:
        url = f"{self.BASE_URL}/releases/{discogs_id}"

        try:
            log.debug(f"Fetching release data for ID: {discogs_id}")
            response = await self._client.get(url)
            response.raise_for_status()
            return response.json()
        except niquests.HTTPError as e:
            log.error(f"Discogs API Error: {e.response.status_code if e.response else 'no response'}")
            return None
        except niquests.RequestException as e:
            log.error(f"Network error while fetching release {discogs_id}: {e!s}")
            return None

    def extract_barcode(self, release_data: dict[str, Any]) -> str | None:
        """Extract barcode from Discogs release data."""
        identifiers = release_data.get("identifiers", [])
        log.debug(f"Extracting barcode from {len(identifiers)} identifiers")

        for identifier in identifiers:
            if identifier.get("type") == "Barcode":
                desc = identifier.get("description", "").lower()
                value = identifier.get("value")
                if desc == "scanned":
                    log.debug(f"Found scanned barcode: {value}")
                    return value
                elif desc == "text":
                    cleaned_value = value.replace(" ", "")
                    log.debug(f"Found text barcode: {cleaned_value}")
                    return cleaned_value
        log.debug("No suitable barcode found")
        return None

    async def fetch_master_data(self, master_id: str) -> dict[str, Any] | None:
        url = f"{self.BASE_URL}/masters/{master_id}"

        try:
            log.debug(f"Fetching master data for ID: {master_id}")
            response = await self._client.get(url)
            response.raise_for_status()
            return response.json()
        except niquests.HTTPError as e:
            log.error(
                f"Discogs API Error when fetching master data: {e.response.status_code if e.response else 'no response'}"
            )
            return None
        except niquests.RequestException as e:
            log.error(f"Network error while fetching master {master_id}: {e!s}")
            return None

    async def search_releases(self, query: str, limit: int = 10) -> dict[str, Any]:
        url = f"{self.BASE_URL}/database/search"
        params = {"q": query, "type": "release", "per_page": limit}

        try:
            log.debug(f"Searching releases with query: {query}")
            response = await self._client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except niquests.HTTPError as e:
            log.error(f"Discogs API Error: {e.response.status_code if e.response else 'no response'}")
            return {"results": []}
        except niquests.RequestException as e:
            log.error(f"Network error while searching releases: {e!s}")
            return {"results": []}

    async def search_releases_filtered(
        self, query: str, track_count: int, limit: int = 10
    ) -> dict[str, Any] | None:
        search = await self.search_releases(query, limit)
        candidates = search.get("results", [])
        if not candidates:
            return None

        best = None
        for c in candidates:
            if c.get("tracklist"):
                if len(c["tracklist"]) == track_count:
                    best = c
                    break

        if not best:
            best = candidates[0]

        return await self.fetch_discogs_release(best["id"])
