from __future__ import annotations

import re
from typing import Any

import niquests

from src.utils.console import log


class MusicBrainzHandler:
    BASE_URL = "https://musicbrainz.org/ws/2/"
    TIMEOUT = 10.0

    def __init__(self, user_agent: str):
        self._client = niquests.AsyncSession(timeout=self.TIMEOUT, disable_http3=True)
        self._client.headers["User-Agent"] = user_agent

    async def close(self) -> None:
        self._client.close()

    async def _get(self, endpoint: str, params: dict | None = None) -> dict[str, Any] | None:
        url = self.BASE_URL + endpoint
        try:
            r = await self._client.get(url, params=params or {})
            r.raise_for_status()
            return r.json()
        except (niquests.HTTPError, niquests.Timeout, niquests.ConnectionError) as e:
            log.error(f"MusicBrainz API error ({url}): {e}")
            return None

    async def search(self, entity: str, params: dict) -> dict:
        """Generic MB search passthrough."""
        return await self._get(entity, params) or {}

    async def search_releases(self, title: str, artist: str = "", limit: int = 100) -> dict[str, Any]:
        query = f'release:"{title}"'
        if artist:
            query += f' AND artist:"{artist}"'

        params = {"fmt": "json", "limit": str(limit), "query": query}
        return await self._get("release", params) or {}

    async def search_releases_filtered(
        self, title: str, artist: str, track_count: int, limit: int = 100
    ) -> dict[str, Any] | None:
        """Search for releases and filter by track count, returning the best match."""
        results = await self.search_releases(title, artist, limit)
        releases = results.get("releases", [])
        if not releases:
            return None

        # track-count heuristic
        best = None
        for r in releases:
            if "medium-list" in r:
                for m in r["medium-list"]:
                    if m.get("track-count") == track_count:
                        best = r
                        break
                if best:
                    break

        if not best:
            best = releases[0]

        return best

    async def search_by_barcode(self, barcode: str) -> dict[str, Any]:
        params = {"fmt": "json", "query": f"barcode:{barcode}"}
        return await self._get("release", params) or {}

    async def get_release(self, mbid: str, includes: list[str] | None = None) -> dict[str, Any] | None:
        params = {"fmt": "json"}
        if includes:
            params["inc"] = "+".join(includes)
        return await self._get(f"release/{mbid}", params)

    async def get_release_group(self, mbgid: str) -> dict[str, Any] | None:
        params = {"fmt": "json", "inc": "url-rels"}
        return await self._get(f"release-group/{mbgid}", params)

    async def get_cover_art(self, mbid: str) -> dict[str, Any] | None:
        url = f"https://coverartarchive.org/release/{mbid}"
        try:
            r = await self._client.get(url)
            r.raise_for_status()
            return r.json()
        except (niquests.HTTPError, niquests.Timeout, niquests.ConnectionError) as e:
            log.error(f"Cover Art Archive error ({url}): {e}")
            return None

    @staticmethod
    def normalize(release: dict[str, Any], release_group: dict[str, Any] | None = None) -> dict[str, Any]:
        release_group = release_group or {}

        # Safe nested-get helper
        def get(obj, *path, default=None):
            for key in path:
                if isinstance(obj, dict) and key in obj:
                    obj = obj[key]
                else:
                    return default
            return obj

        def to_int(value, default=0):
            try:
                return int(value)
            except Exception:
                return default

        genres = set()

        for g in get(release, "genres", default=[]):
            name = get(g, "name")
            if isinstance(name, str):
                genres.add(name.strip().lower())

        for medium in get(release, "media", default=[]):
            for track in get(medium, "tracks", default=[]):
                rec = get(track, "recording", default={})
                for g in get(rec, "genres", default=[]):
                    name = get(g, "name")
                    if isinstance(name, str):
                        genres.add(name.strip().lower())

        release_group_info = {
            "primary_type": get(release_group, "primary-type", default="") or "",
            "secondary_types": get(release_group, "secondary-types", default=[]) or [],
        }

        media_list: list[dict[str, Any]] = []

        for idx, medium_raw in enumerate(get(release, "media", default=[]), start=1):
            tracks = []

            for tr in get(medium_raw, "tracks", default=[]):
                number = get(tr, "number") or str(get(tr, "position", default=""))
                title = get(tr, "title", default="Unknown Track")

                dur_raw = get(tr, "length") or get(tr, "recording", "length")
                duration_ms = to_int(dur_raw)

                tracks.append({"number": number, "title": title, "duration_ms": duration_ms})

            media_list.append(
                {"disc": idx, "format": get(medium_raw, "format", default="") or "", "tracks": tracks}
            )

        relations = {
            "discogs": [],
            "allmusic": [],
            "wikidata": [],
            "genius": [],
            "lyrics": [],
            "amazon_asin": [],
            "other_databases": [],
        }

        raw_relations = []
        raw_relations.extend(get(release, "relations", default=[]))
        raw_relations.extend(get(release_group, "relations", default=[]))

        for rel in raw_relations:
            if not isinstance(rel, dict):
                continue

            resource = get(rel, "url", "resource")
            if not isinstance(resource, str) or not resource:
                continue

            rel_type = (rel.get("type") or "").strip().lower()
            key = re.sub(r"[^a-z0-9]+", "_", rel_type)

            if key not in relations:
                key = "other_databases"

            relations[key].append(resource)

        artist_credit = []
        for ac in get(release, "artist-credit", default=[]):
            name = ac.get("name") or get(ac, "artist", "name", default="")
            if name:
                artist_credit.append({"name": name})

        label_info = get(release, "label-info", default=[])
        cat_no = label_info[0].get("catalog-number") if label_info else None

        return {
            "artist_credit": artist_credit,
            "title": get(release, "title", default="") or "",
            "date": MusicBrainzHandler._get_earliest_date(release),
            "disambiguation": (get(release, "disambiguation", default="") or "").strip(),
            "status": (get(release, "status", default="") or "").strip(),
            "release_group": release_group_info,
            "genres": sorted(genres),
            "relations": relations,
            "media": media_list,
            "barcode": get(release, "barcode"),
            "catalog_number": cat_no,
        }

    @staticmethod
    def _get_earliest_date(release: dict[str, Any]) -> str:
        events = release.get("release-events", [])
        dates = [e["date"] for e in events if isinstance(e, dict) and e.get("date")]
        if dates:
            return min(dates)
        return release.get("date", "") or ""
