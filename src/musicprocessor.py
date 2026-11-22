from __future__ import annotations

import asyncio
import os
import re
from datetime import timedelta
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlparse

import ffmpeg
from tinytag import TinyTag

from src.databases import DeezerHandler, DiscogsHandler, MusicBrainzHandler
from src.mediainfo import MediaInfoHandler
from src.progress import ProgressManager
from src.utils.console import console, log
from src.utils.constants import INVALID_TAG_WORDS, LABEL_WORDS
from src.utils.loads import load_json
from src.utils.music_parser import _split_multi_artists, parse_music_folder


class MusicProcessor:
    """
    Handles music-specific analysis and metadata enrichment.
    Processes tracks, extracts tags/sources, queries APIs (MusicBrainz, Discogs, Deezer).
    """

    _music_services: dict | None = None

    _URL_KEY_MAP: ClassVar[dict[str, str]] = {
        "deezer.com": "deezer_url",
        "musicbrainz.org": "musicbrainz_url",
        "discogs.com": "discogs_url",
        "qobuz.com": "qobuz_url",
        "spotify.com": "spotify_url",
        "tidal.com": "tidal_url",
    }

    @property
    def music_services(self):
        if self._music_services is None:
            try:
                self._music_services = load_json(self.base_dir / "data/music_services.json")
            except Exception as e:
                console.print(f"[red]Error loading music_services.json: {e}[/red]")
                self._music_services = {}  # Fallback: Empty dict to avoid crashes
        return self._music_services

    def __init__(self, mihandle: MediaInfoHandler, base_dir: Path):
        self.mihandle = mihandle
        self.base_dir = base_dir
        self.mb = MusicBrainzHandler("Art2Album / 1.0")
        self.dc = DiscogsHandler("Art2Album / 1.0")
        self.dz = DeezerHandler("Art2Album / 1.0")

    def _apply_scene_name_parsing(self, meta: dict) -> None:
        if meta.get("manual_ids", {}).get("mbid"):
            return

        raw = meta.get("uuid", "")
        if not raw:
            return

        parsed = parse_music_folder(raw)

        _, all_artists = _split_multi_artists(parsed.get("artist", ""))
        album_has_multi_artist = len(all_artists) > 1

        # For artist/album: prefer MediaInfo tags if scene values look potentially swapped
        for field in ("artist", "album", "year"):
            scene_value = parsed.get(field)
            if not scene_value or not scene_value.strip():
                continue

            # If album has multi-artist delimiters, the scene parser likely got it wrong
            # Don't override existing meta (likely from MediaInfo or previous enrichment)
            if field in ("artist", "album") and album_has_multi_artist:
                if meta.get(field):
                    meta[f"_scene_{field}"] = scene_value
                    continue

            if meta.get(field) and meta.get(field) != scene_value:
                log.debug(f"Scene parsing overriding previous {field}: {meta[field]} -> {scene_value}")
            meta[field] = scene_value
            meta[f"_scene_{field}"] = scene_value

        if parsed.get("release_group"):
            existing_keywords = meta.get("keywords", [])
            if isinstance(existing_keywords, list):
                if parsed["release_group"] not in existing_keywords:
                    existing_keywords.append(parsed["release_group"])
                    meta["keywords"] = existing_keywords
            if parsed["release_group"] not in meta:
                meta["release_group"] = parsed["release_group"]

        for field in ("source", "catalog_number", "barcode", "format", "language", "edition"):
            if not meta.get(field) and parsed.get(field):
                meta[field] = parsed[field]

        for field in ("bit_depth", "bitrate", "sampling_rate"):
            if not meta.get(field) and parsed.get(field):
                meta[field] = parsed[field]

    async def process_music(self, meta: dict) -> dict:
        """Music processing: Sorts files, extracts metadata, queries APIs."""
        pm = ProgressManager.get_instance()
        if pm:
            pm.update("Processing music metadata...")

        nfo_meta = None
        nfo_file = meta.get("nfo_file")
        if nfo_file:
            nfo_path = Path(nfo_file)
            if nfo_path.is_file():
                nfo_meta = await self._parse_nfo(nfo_path)
                if nfo_meta:
                    meta["nfo_parsed"] = nfo_meta
                    if nfo_source_url := nfo_meta.get("source_url"):
                        host = urlparse(nfo_source_url).hostname or ""
                        for domain in self._URL_KEY_MAP:
                            if host == domain or host.endswith("." + domain):
                                meta[self._URL_KEY_MAP[domain]] = nfo_source_url
                                break
                    if nfo_notes := nfo_meta.get("notes"):
                        meta["nfo_notes"] = nfo_notes

        musicloc = Path(meta["path"])
        tracks, cover, back_cover, proof = self._sort_music(meta, musicloc)
        meta.update(
            track_count=len(tracks),
            user_cover=str(cover) if cover else None,
            back_cover=str(back_cover) if back_cover else None,
            user_proof=str(proof) if proof else None,
        )

        self._apply_scene_name_parsing(meta)

        if not self._has_sufficient_music_metadata(meta) or meta.get("manual_ids", {}).get("mbid"):
            await self._enrich_music_metadata(meta, tracks)

        await self._consolidate_metadata(meta, tracks)

        if pm:
            pm.update("Music processing complete")

        return meta

    def _sort_music(
        self, meta: dict, musicloc: Path
    ) -> tuple[list[tuple[str, str, Path]], Path | None, Path | None, Path | None]:
        """Sort tracks, detect covers/proof. Helper for process_music."""
        cover, back_cover, proof = None, None, None
        tracks = []

        music_filelist = [Path(p) for p in meta.get("music_files", [])]
        has_disc_folders = any(
            any(folder.lower() in p.parent.name.lower() for folder in ["disc", "cd", "record"])
            for p in music_filelist
        )

        for file_path in music_filelist:
            if not file_path.is_file() or file_path.name.startswith("._"):
                continue

            relative_path = file_path.relative_to(musicloc)
            file_name = file_path.name
            bare_fn = str(Path(file_name).stem.lower())

            if not (bare_fn.endswith("sample") or bare_fn.startswith("!sample")):
                disc = "Disc 1"
                if has_disc_folders:
                    for part in relative_path.parts[:-1]:
                        if any(folder.lower() in part.lower() for folder in ["disc", "cd", "record"]):
                            disc = part.title()
                            break

                track_match = re.match(r"([A-Z]|\d+)(?:[\s.-])?(.*)", file_name, re.IGNORECASE)
                if not track_match:
                    continue

                track_num = track_match.group(1)
                rest_of_file = track_match.group(2).strip()

                if len(track_num) == 3 and track_num.isdigit():
                    disc = f"Disc {int(track_num[0])}"
                    track_num = track_num[1:]
                    file_name = f"{track_num}-{rest_of_file}"

                tracks.append((disc, file_name, file_path))

        has_alpha_tracks = any(re.match(r"^[A-Z]\d*$", t[1].split()[0], re.IGNORECASE) for t in tracks)
        if has_alpha_tracks:
            folder_name_upper = musicloc.name.upper()
            has_cd_indicator = bool(
                re.search(r"(?:^|[-_ ])(?:\d+CD|CDP|CDS|CD)(?:[-_ ]|$)", folder_name_upper)
                or re.search(r"\b\d+CD\b", folder_name_upper)
            )
            if not has_cd_indicator:
                meta["source"] = "VINYL"
                tracks = [(t[0].replace("Disc 1", "Record"), t[1], t[2]) for t in tracks]

        tracks.sort(key=self._track_sort_key)

        # Handle images
        front_cover_variants = ["front", "cover", "album", "disc1", "cd1"]
        back_cover_variants = ["back_cover", "rear", "backside", "back"]

        # We consider jpg and png. In rare cases we fall back to a remote cover (Discogs/Deezer).
        candidate_exts = {".jpg", ".jpeg", ".png"}
        # All viable images from the tree.
        candidate_images: list[tuple[Path, str, int]] = []  # (path, sort_key, priority)
        for img_path_str in meta["user_images"]:
            img_path = Path(img_path_str)
            if img_path.suffix.lower() not in candidate_exts:
                continue

            file_name_lower = img_path.name.lower().replace(" ", "")

            if "proof" in file_name_lower:
                proof = img_path
                continue
            if any(v in file_name_lower for v in back_cover_variants):
                back_cover = img_path
                continue

            # Priority heuristic: prefer front/cover/album naming *and* images in a /covers/ folder.
            # Lower number = higher priority.
            in_covers_dir = any(
                part.lower() == "covers" for part in img_path.relative_to(musicloc).parts[:-1]
            )
            name_score = 0 if any(v in file_name_lower for v in front_cover_variants) else 1
            folder_score = 0 if in_covers_dir else 2
            priority = (name_score, folder_score)

            candidate_images.append((img_path, img_path.name.lower(), priority))

        if candidate_images:
            candidate_images.sort(key=lambda x: (x[2], x[1]))
            cover = candidate_images[0][0]

        return tracks, cover, back_cover, proof

    def _track_sort_key(self, track: tuple[str, str, Path]) -> tuple[int, int]:
        """Helper sort key for tracks."""
        disc_num_match = re.search(r"\d+", track[0].split()[-1])
        disc_number = int(disc_num_match.group()) if disc_num_match else 1

        track_part = track[1].split()[0]
        match = re.match(r"^([A-Z])?(\d+)(?:[\s\.\-_\,](\d+))?", track_part, re.IGNORECASE)
        if match:
            letter, number, sub_number = match.groups()
            primary = (ord(letter.upper()) - ord("A") + 1) * 1000 if letter else 0
            secondary = int(number or "0") * 10
            tertiary = int(sub_number or "0")
            return (disc_number, primary + secondary + tertiary)
        elif track_part.isdigit():
            return (disc_number, int(track_part))
        return (disc_number, 999)

    async def _enrich_music_metadata(self, meta: dict, tracks: list[tuple[str, str, Path]]) -> None:
        """Enrich meta with MediaInfo, tags, APIs. Async for I/O."""
        if not tracks:
            return

        first_audio_file = tracks[0][2]
        mi = await self.mihandle.exportInfo(
            media_path=first_audio_file, is_dir=meta.get("is_dir", False), uuid=meta["uuid"], export_text=True
        )
        meta.update(
            is_disc=False,
            screens=0,
            edition="",
            bdinfo=None,
            tmdb=0,
            imdb=0,
            tvdb_id=0,
            mal_id=0,
            sd=0,
            keywords="",
            category="MUSIC",
            mediainfo=mi,
        )

        album_fname = meta["uuid"].replace("&", "AANDD")
        normalized_af = re.sub(r"[^a-zA-Z0-9\sAANDD]", " ", album_fname).replace("AANDD", "&")
        meta["source"] = (
            meta.get("manual_source") or meta.get("source") or self._get_music_source(normalized_af)
        )
        meta["service"] = meta.get("service") or self.get_service(meta)
        meta["tag"] = meta.get("tag") or self._extract_tag(album_fname)

        track = next((t for t in mi["media"]["track"] if t["@type"] == "General"), {})
        extra = track.get("extra", {})

        tag_artist = track.get("Album_Performer", track.get("Performer")) or extra.get(
            "ARTISTS", extra.get("Performer")
        )
        meta["_tag_artist"] = tag_artist or ""

        tag_album = track.get("Album") or extra.get("Album")
        meta["_tag_album"] = tag_album or ""

        general_fmt = track.get("Format", "") or ""
        audio_track = next((t for t in mi.get("media", {}).get("track", []) if t.get("@type") == "Audio"), {})
        compression_mode = audio_track.get("Compression_Mode", "").lower()
        meta["is_lossy"] = compression_mode == "lossy"

        if "MPEG-4" in general_fmt:
            result = self.mihandle.get_audio(meta["uuid"], meta)
            audio_str = result[0]
            codec = (
                audio_str.rsplit(" ", 1)[0]
                if audio_str
                else general_fmt.replace("MPEG Audio", "MP3").replace("-", "").upper()
            )
            meta["type"] = codec
        else:
            meta["type"] = general_fmt.replace("MPEG Audio", "MP3").replace("-", "").upper()

        recorded_date = track.get(
            "Recorded_Date", track.get("Released_Date", track.get("Year"))
        ) or extra.get("Year", extra.get("Recorded_Date", extra.get("Released_Date")))
        tag_year = (
            recorded_date.split("-")[0] if recorded_date and "-" in recorded_date else (recorded_date or "")
        )
        meta["_tag_year"] = tag_year
        if not meta.get("year") and recorded_date:
            if "-" in recorded_date:
                meta["year"] = recorded_date.split("-")[0]
                meta["release_date"] = recorded_date
            else:
                meta["year"] = recorded_date
                meta["release_date"] = ""
        elif meta.get("year") and tag_year and meta["year"] != tag_year:
            meta["original_year"] = tag_year

        meta["barcode"] = next(
            (
                v
                for k, v in {**track, **extra}.items()
                if k.lower() in ["barcode", "upc", "scanid", "scan_id"]
            ),
            None,
        )
        meta["catalog_number"] = self._extract_catalog_number(meta["uuid"], extra)

        audio_track = next((t for t in mi["media"]["track"] if t["@type"] == "Audio"), {})
        meta["bit_depth"] = audio_track.get("BitDepth", "")
        meta["bitrate"] = audio_track.get("BitRate_String", "").replace("/", "").replace(" ", "")
        sampling_rate = audio_track.get("SamplingRate_String", "")
        meta["sampling_rate"] = re.sub(r"\.0(?=\s+kHz$)", "", sampling_rate)

        temp_artist = meta.get("artist", "")
        temp_album = meta.get("album", "")
        temp_barcode = meta.get("barcode", "")
        temp_catalog_number = meta.get("catalog_number", "")

        # "Various" is a placeholder; prefer a specific tag artist if available
        if temp_artist and temp_artist.lower().strip() == "various":
            tag_artist = meta.get("_tag_artist", "")
            if tag_artist and tag_artist.lower().strip() != "various":
                log.debug(
                    f"Replacing 'Various' artist '{temp_artist}' with tag artist '{tag_artist}' for searches"
                )
                temp_artist = tag_artist

        # API queries
        mbid = meta.get("manual_ids", {}).get("mbid") or meta.get("mbid")
        if mbid:
            # Sync manual MBID to canonical key and clear stale derived IDs
            # so that a corrected MBID discovers fresh Discogs/Deezer relations.
            if meta.get("manual_ids", {}).get("mbid"):
                meta["mbid"] = meta["manual_ids"]["mbid"]
                meta.pop("discogs_id", None)
                meta.pop("discogs_master", None)
                meta.pop("deezer", None)

            release = await self.mb.get_release(
                mbid, includes=["recordings", "genres", "labels", "url-rels", "release-groups"]
            )
            if release:
                rg = None
                rg_id = release.get("release-group", {}).get("id")
                if rg_id:
                    rg = await self.mb.get_release_group(rg_id)

                meta["mb_info"] = self.mb.normalize(release, rg)
                self._process_mb_relations(meta["mb_info"]["relations"], meta)

                if not meta.get("barcode"):
                    meta["barcode"] = meta["mb_info"].get("barcode") or ""
            else:
                log.warning(f"MusicBrainz failed for MBID {mbid}, proceeding without MB metadata")
        else:
            methods = [
                ("barcode", lambda m: self._search_by_barcode(m, temp_barcode)),
                ("catalog_number", lambda m: self._search_by_catalog_number(m, temp_catalog_number)),
                (
                    "title_artist",
                    lambda m: self._search_by_title_artist(m, temp_album, temp_artist, len(tracks)),
                ),
            ]
            for _method_name, search_func in methods:
                if await self._try_search_method(meta, search_func):
                    break
            else:
                console.print("[red]WARN[/red]: No MusicBrainz data found using any method.")

        await asyncio.gather(
            self._process_with_discogs(meta, temp_artist, temp_album, temp_barcode, len(tracks)),
            self._process_with_deezer(meta, temp_artist, temp_album, temp_barcode, len(tracks)),
        )

        if not meta.get("discogs_info") and meta.get("_tag_artist") and meta.get("_tag_album"):
            if temp_artist != meta["_tag_artist"] or temp_album != meta["_tag_album"]:
                log.debug("Scene search failed, falling back to MediaInfo tags for Discogs")
                await self._process_with_discogs(
                    meta, meta["_tag_artist"], meta["_tag_album"], temp_barcode, len(tracks)
                )

        if not meta.get("deezer_info") and meta.get("_tag_artist") and meta.get("_tag_album"):
            if temp_artist != meta["_tag_artist"] or temp_album != meta["_tag_album"]:
                log.debug("Scene search failed, falling back to MediaInfo tags for Deezer")
                await self._process_with_deezer(
                    meta, meta["_tag_artist"], meta["_tag_album"], temp_barcode, len(tracks)
                )

        # Set album_cover from Discogs/Deezer first (they provide covers directly)
        if not meta.get("album_cover"):
            if meta.get("discogs_info") and meta["discogs_info"].get("images"):
                meta["album_cover"] = meta["discogs_info"]["images"][0].get("resource_url")
            elif meta.get("deezer_info") and meta["deezer_info"].get("cover_big"):
                meta["album_cover"] = meta["deezer_info"].get("cover_big")

        # Fetch cover art from MusicBrainz only if needed for back cover or still no cover
        if meta.get("mbid") and (not meta.get("album_cover") or not meta.get("album_back")):
            cover_list = await self.mb.get_cover_art(meta["mbid"])
            if cover_list and "images" in cover_list:
                for image in cover_list["images"]:
                    if image.get("approved"):
                        if not meta.get("album_cover") and "Front" in image.get("types", []):
                            meta["album_cover"] = image["thumbnails"].get("500") or image["thumbnails"].get(
                                "large"
                            )
                        if not meta.get("album_back") and "Back" in image.get("types", []):
                            meta["album_back"] = image["thumbnails"].get("500") or image["thumbnails"].get(
                                "large"
                            )

        # If no MB info but Discogs or Deezer has barcode, try MB search
        if not meta.get("mb_info"):
            barcode = None
            if meta.get("discogs_info"):
                barcode = self.dc.extract_barcode(meta["discogs_info"])
            elif meta.get("deezer_info"):
                barcode = meta["deezer_info"].get("upc")

            if barcode and barcode != temp_barcode:
                meta["barcode"] = barcode
                await self._search_by_barcode(meta, barcode)

    async def _try_search_method(self, meta: dict, search_func) -> bool:
        """Helper: Try a search method, return True on success."""
        try:
            await search_func(meta)
            if meta.get("mb_info"):
                return True
        except Exception as e:
            log.error(f"Error in search method: {e}")
        return False

    async def _search_mb_release(self, meta: dict, query_type: str, query_value: str) -> None:
        """Shared helper for barcode and catalog number MB searches."""
        if not query_value:
            return
        if query_type == "barcode":
            releases = await self.mb.search_by_barcode(query_value)
        else:
            releases = await self.mb.search("release", {"query": f"catno:{query_value}", "fmt": "json"})
        if not releases or not releases.get("releases"):
            return
        best = releases["releases"][0]
        mbid = best.get("id")
        if not mbid:
            return
        meta["mbid"] = mbid
        release = await self.mb.get_release(
            mbid, includes=["recordings", "genres", "labels", "url-rels", "release-groups"]
        )
        if release:
            rg = None
            rg_id = release.get("release-group", {}).get("id")
            if rg_id:
                rg = await self.mb.get_release_group(rg_id)
            meta["mb_info"] = self.mb.normalize(release, rg)

    async def _search_by_barcode(self, meta: dict, barcode: str) -> None:
        await self._search_mb_release(meta, "barcode", barcode)

    async def _search_by_catalog_number(self, meta: dict, catalog_number: str) -> None:
        await self._search_mb_release(meta, "catalog", catalog_number)

    async def _search_by_title_artist(
        self, meta: dict, release_title: str, artist_name: str, track_count: int
    ) -> None:
        if release_title and artist_name:
            search_artist, _ = _split_multi_artists(artist_name)
            if search_artist != artist_name:
                log.debug(f"MusicBrainz multi-artist split: '{artist_name}' -> '{search_artist}'")
            best = await self.mb.search_releases_filtered(release_title, search_artist, track_count)
            if best:
                meta["mbid"] = best.get("id")
                release = await self.mb.get_release(
                    meta["mbid"], includes=["recordings", "genres", "labels", "url-rels", "release-groups"]
                )
                if release:
                    rg = None
                    rg_id = release.get("release-group", {}).get("id")
                    if rg_id:
                        rg = await self.mb.get_release_group(rg_id)
                    meta["mb_info"] = self.mb.normalize(release, rg)

    @staticmethod
    def _fuzzy_match(a: str, b: str) -> bool:
        """True if a and b match after lowercasing, stripping, and removing leading articles."""
        if not a or not b:
            return False
        a_norm = a.strip().lower()
        b_norm = b.strip().lower()
        for article in ("the ", "a ", "an "):
            if a_norm.startswith(article):
                a_norm = a_norm[len(article) :]
            if b_norm.startswith(article):
                b_norm = b_norm[len(article) :]
        return a_norm == b_norm or a_norm in b_norm or b_norm in a_norm

    @staticmethod
    def _normalize_key(key: str) -> str:
        return key.lower().replace(" ", "_").replace("-", "_")

    @staticmethod
    def _format_artists(artists: list[str]) -> str:
        if not artists:
            return ""
        if len(artists) == 1:
            return artists[0]
        if len(artists) == 2:
            return f"{artists[0]} & {artists[1]}"
        return ", ".join(artists[:-1]) + f" & {artists[-1]}"

    def _validate_discogs_result(
        self,
        release_data: dict,
        temp_artist: str,
        temp_album: str,
        temp_track_count: int,
        temp_year: str | None,
    ) -> bool:
        """Return True if the Discogs result plausibly matches our known artist/album/track count/year."""
        if not temp_artist and not temp_album:
            return True  # can't validate, accept

        result_artists = [a.get("name", "").strip().lower() for a in release_data.get("artists", [])]
        result_title = (release_data.get("title") or "").strip().lower()
        result_track_count = len(release_data.get("tracklist", []))
        result_year = str(release_data.get("year", ""))

        artist_ok = (
            any(self._fuzzy_match(a, temp_artist) for a in result_artists) if result_artists else False
        )
        album_ok = self._fuzzy_match(result_title, temp_album) if result_title else False

        if artist_ok and album_ok:
            return True  # strong match

        if not (artist_ok or album_ok):
            return False  # completely wrong

        if temp_track_count and result_track_count:
            if abs(result_track_count - temp_track_count) > 2:
                log.debug(
                    f"Discogs result rejected: track_count mismatch temp={temp_track_count} result={result_track_count} "
                    f"| result={result_title!r} by {result_artists!r}"
                )
                return False

        if temp_year and result_year and result_year != temp_year:
            log.debug(
                f"Discogs result rejected: year mismatch temp={temp_year} result={result_year} "
                f"| result={result_title!r} by {result_artists!r}"
            )
            return False

        return True

    def _validate_deezer_result(
        self, data: dict, temp_artist: str, temp_album: str, temp_track_count: int, temp_year: str | None
    ) -> bool:
        """Return True if the Deezer result plausibly matches our known artist/album/track count/year."""
        if not temp_artist and not temp_album:
            return True

        result_artist = (data.get("artist", {}) or {}).get("name", "").strip().lower()
        result_title = (data.get("title") or "").strip().lower()
        result_tracks = data.get("nb_tracks")
        result_release_date = data.get("release_date", "")
        result_year = result_release_date.split("-")[0] if result_release_date else ""

        artist_ok = self._fuzzy_match(result_artist, temp_artist)
        album_ok = self._fuzzy_match(result_title, temp_album)

        if artist_ok and album_ok:
            return True  # strong match

        if not (artist_ok or album_ok):
            return False

        if temp_track_count and result_tracks:
            if abs(int(result_tracks) - temp_track_count) > 2:
                log.debug(
                    f"Deezer result rejected: track_count mismatch temp={temp_track_count} result={result_tracks} "
                    f"| result={result_title!r} by {result_artist!r}"
                )
                return False

        if temp_year and result_year and result_year != temp_year:
            log.debug(
                f"Deezer result rejected: year mismatch temp={temp_year} result={result_year} "
                f"| result={result_title!r} by {result_artist!r}"
            )
            return False

        return True

    async def _process_with_deezer(
        self, meta: dict, temp_artist: str, temp_album: str, temp_barcode: str, track_count: int
    ) -> None:
        """Query Deezer API asynchronously."""
        data = None
        search_artist = temp_artist
        if temp_artist:
            # Split multi-artist names for API search - use primary artist only
            primary_artist, _ = _split_multi_artists(temp_artist)
            if primary_artist != temp_artist:
                log.debug(f"Multi-artist split: '{temp_artist}' -> '{primary_artist}'")
                search_artist = primary_artist
        if meta.get("manual_ids", {}).get("deezer"):
            data = await self.dz.fetch_album(meta["manual_ids"]["deezer"])
        elif temp_barcode:
            data = await self.dz.fetch_album_by_upc(temp_barcode)
        elif search_artist and temp_album:
            query = f'artist:"{search_artist}" album:"{temp_album}"'
            data = await self.dz.search_albums_filtered(query, track_count)
        temp_year = meta.get("year") or ""
        if data and self._validate_deezer_result(data, temp_artist, temp_album, track_count, temp_year):
            filtered = {
                "title": data.get("title"),
                "upc": data.get("upc"),
                "link": data.get("link"),
                "cover": data.get("cover"),
                "cover_big": data.get("cover_big"),
                "label": data.get("label"),
                "nb_tracks": data.get("nb_tracks"),
                "duration": data.get("duration"),
                "release_date": data.get("release_date"),
                "tracklist": data.get("tracklist"),
                "explicit_lyrics": data.get("explicit_lyrics"),
                "type": data.get("type"),
                "genres": [g["name"] for g in data.get("genres", {}).get("data", [])],
                "artist": {
                    "name": data.get("artist", {}).get("name"),
                    "picture": data.get("artist", {}).get("picture"),
                    "picture_big": data.get("artist", {}).get("picture_big"),
                },
                "tracks": {
                    "data": [
                        {
                            "title": t.get("title"),
                            "title_short": t.get("title_short"),
                            "link": t.get("link"),
                            "duration": t.get("duration"),
                            "explicit_lyrics": t.get("explicit_lyrics"),
                            "disk_number": t.get("disk_number", 1),
                            "track_position": t.get("track_position"),
                        }
                        for t in data.get("tracks", {}).get("data", [])
                    ]
                },
            }
            meta["deezer_info"] = filtered

    async def _process_with_discogs(
        self, meta: dict, temp_artist: str, temp_album: str, temp_barcode: str, track_count: int
    ) -> None:
        """Query Discogs API asynchronously."""
        mb_discogs = meta.get("mb_info", {}).get("relations", {}).get("discogs", [])
        discogs_release_id = None
        discogs_master = None

        for url in mb_discogs:
            if "release" in url:
                match = re.search(r"discogs\.com/release/(\d+)", url)
                if match:
                    discogs_release_id = match.group(1)
            elif "master" in url:
                match = re.search(r"discogs\.com/master/(\d+)", url)
                if match:
                    discogs_master = match.group(1)

        discogs_id = meta.get("manual_ids", {}).get("discogs") or meta.get("discogs_id") or discogs_release_id
        discogs_master = meta.get("discogs_master") or discogs_master

        if not discogs_id and discogs_master:
            master_data = await self.dc.fetch_master_data(discogs_master)
            if master_data:
                main_release_url = master_data.get("main_release_url", "")
                id_match = re.search(r"releases/(\d+)", main_release_url)
                if id_match:
                    discogs_id = id_match.group(1)
                    meta["discogs_id"] = discogs_id

        release_data = None
        search_artist = temp_artist
        if temp_artist:
            primary_artist, _ = _split_multi_artists(temp_artist)
            if primary_artist != temp_artist:
                log.debug(f"Multi-artist split for Discogs: '{temp_artist}' -> '{primary_artist}'")
                search_artist = primary_artist
        if discogs_id:
            release_data = await self.dc.fetch_discogs_release(discogs_id)
        elif search_artist and temp_album:
            query = f'artist:"{search_artist}" album:"{temp_album}"'
            release_data = await self.dc.search_releases_filtered(query, track_count)

        temp_year = meta.get("year") or ""
        if release_data and self._validate_discogs_result(
            release_data, temp_artist, temp_album, track_count, temp_year
        ):
            filtered = {
                "year": release_data.get("year"),
                "resource_url": release_data.get("resource_url"),
                "uri": release_data.get("uri"),
                "artists": [
                    {"name": a.get("name"), "thumbnail_url": a.get("thumbnail_url")}
                    for a in release_data.get("artists", [])
                ],
                "artists_sort": release_data.get("artists_sort"),
                "master_url": release_data.get("master_url"),
                "title": release_data.get("title"),
                "released": release_data.get("released"),
                "identifiers": [
                    i for i in release_data.get("identifiers", []) if i.get("type") in ["Barcode", "ISRC"]
                ],
                "genres": release_data.get("genres"),
                "styles": release_data.get("styles"),
                "tracklist": release_data.get("tracklist"),
                "format": release_data.get("formats", [{}])[0].get("name", "")
                if release_data.get("formats")
                else "",
                "images": [img for img in release_data.get("images", []) if img.get("type") == "primary"],
                "thumb": release_data.get("thumb"),
                "is_offensive": release_data.get("is_offensive"),
            }
            meta["discogs_info"] = filtered
            barcode = self.dc.extract_barcode(release_data)
            if barcode and not meta.get("barcode"):
                meta["barcode"] = barcode
            if barcode and not meta.get("mbid"):
                await self._search_by_barcode(meta, barcode)

    @staticmethod
    def _normalize_track_number(value: str | int | None, fallback: int) -> int:
        if value is None:
            return fallback
        if isinstance(value, int):
            return value
        stripped = str(value).strip()
        if not stripped:
            return fallback
        digits = re.sub(r"\D", "", stripped)
        return int(digits) if digits else fallback

    async def _consolidate_metadata(self, meta: dict, tracks: list[tuple[str, str, Path]]) -> None:
        """
        Consolidate metadata from various sources;
        Preferring Discogs > Deezer > MusicBrainz > tags.
        tags last due to many scene uploads promo through bad tags
        """
        self._consolidate_artist(meta)
        self._consolidate_album(meta)
        self._consolidate_year(meta)
        self._consolidate_edition(meta)
        self._consolidate_source(meta)
        self._consolidate_keywords(meta)
        self._consolidate_catalog_number(meta)
        meta["tracklist"] = await self._resolve_tracklist(meta, tracks)
        self._finalize_artist_formatting(meta)
        self._append_identifiers_to_keywords(meta)

    def _consolidate_artist(self, meta: dict) -> None:
        """Resolve artist from APIs, tags, scene parsing."""
        current_artist = meta.get("artist", "")
        current_album = meta.get("album", "")
        album_has_multi_artist = (
            any(d in current_album for d in [",", "&", "+", "x"]) if current_album else False
        )
        tag_artist = meta.get("_tag_artist", "")
        tag_album = meta.get("_tag_album", "")
        scene_artist = meta.get("_scene_artist", "")
        scene_album = meta.get("_scene_album", "")

        # Check for swapped artist/album: if scene artist matches tag album, swap them back
        if tag_artist and tag_album and scene_artist and scene_album:
            if self._fuzzy_match(scene_artist, tag_album) and self._fuzzy_match(scene_album, tag_artist):
                meta["artist"] = tag_artist
                meta["album"] = tag_album

        if not meta.get("artist") or (meta.get("artist", "").lower().strip() == "various"):
            # "Various" is a placeholder; only use tag artist if it's more specific
            tag_artist_val = meta.get("_tag_artist", "")
            if tag_artist_val and tag_artist_val.lower().strip() != "various":
                meta["artist"] = tag_artist_val
            elif "discogs_info" in meta:
                artists = meta["discogs_info"].get("artists", [])
                if artists:
                    meta["artist"] = self._format_artists([a.get("name", "") for a in artists])
            elif "deezer_info" in meta:
                artist = meta["deezer_info"].get("artist", {})
                if artist:
                    meta["artist"] = artist.get("name", "")
            elif "mb_info" in meta:
                ac = meta["mb_info"].get("artist-credit", [])
                if ac:
                    meta["artist"] = self._format_artists([a.get("name", "") for a in ac])
            elif album_has_multi_artist and tag_artist:
                meta["artist"] = tag_artist
        elif album_has_multi_artist and tag_artist and tag_artist != current_artist:
            meta["artist"] = tag_artist

    def _consolidate_album(self, meta: dict) -> None:
        """Resolve album title — APIs are authoritative. Extract edition if present in title."""
        current_album = meta.get("album", "")
        tag_album = meta.get("_tag_album", "")
        album_has_multi_artist = (
            any(d in current_album for d in [",", "&", "+", "x"]) if current_album else False
        )

        if "discogs_info" in meta:
            title = meta["discogs_info"].get("title")
            if title:
                if not meta.get("album"):
                    meta["album"] = title
                    self._extract_edition_from_title(title, meta)
                elif meta["album"] != title.strip():
                    log.debug(f"Discogs album differs: api={title!r} current={meta.get('album')!r}")
                    meta["album"] = title
                    self._extract_edition_from_title(title, meta)
        elif "deezer_info" in meta:
            title = meta["deezer_info"].get("title")
            if title:
                if not meta.get("album"):
                    meta["album"] = title
                    self._extract_edition_from_title(title, meta)
                elif meta["album"] != title.strip():
                    log.debug(f"deezer_info album differs: api={title!r} current={meta.get('album')!r}")
                    meta["album"] = title
                    self._extract_edition_from_title(title, meta)
        elif "mb_info" in meta:
            title = meta["mb_info"].get("title")
            if title:
                if not meta.get("album"):
                    meta["album"] = title
                    self._extract_edition_from_title(title, meta)
                elif meta["album"] != title.strip():
                    log.debug(f"MB album differs: api={title!r} current={meta.get('album')!r}")
                    meta["album"] = title
                    self._extract_edition_from_title(title, meta)
        elif album_has_multi_artist and meta.get("_tag_album") and meta["_tag_album"] != current_album:
            meta["album"] = meta["_tag_album"]
        elif tag_album and tag_album != current_album and not meta.get("album"):
            meta["album"] = tag_album

    def _extract_edition_from_title(self, title: str, meta: dict) -> None:
        """Extract edition info from album title if present (e.g., 'Album (Deluxe Edition)')."""
        if not title:
            return

        # Match patterns like "(Deluxe)", "(Deluxe Edition)", "(Special Edition)", etc.
        # Edition values match EDITION_CODES format: "DELUXE" or "SPECIAL EDITION"
        patterns = [
            (r"\((Deluxe Edition)\)", "DELUXE EDITION"),
            (r"\((Deluxe)\)", "DELUXE"),
            (r"\((Special Edition)\)", "SPECIAL EDITION"),
            (r"\((Special)\)", "SPECIAL EDITION"),
            (r"\((Anniversary Edition)\)", "ANNIVERSARY"),
            (r"\((Anniversary)\)", "ANNIVERSARY"),
            (r"\((BOX SET)\)", "BOX SET"),
        ]
        for pattern, edition in patterns:
            m = re.search(pattern, title, re.IGNORECASE)
            if m:
                if not meta.get("edition"):
                    meta["edition"] = edition
                # Strip edition from album title to avoid duplication
                meta["album"] = re.sub(pattern, "", title, flags=re.IGNORECASE).strip()
                break

    def _consolidate_year(self, meta: dict) -> None:
        """Resolve year and release_date from APIs. API data takes precedence over scene parsing."""
        # Prefer API year over scene-parsed year when verified data exists
        api_year = None
        api_date = None

        if "discogs_info" in meta:
            api_year = meta["discogs_info"].get("year")
            if api_year:
                api_date = str(api_year)
        elif "deezer_info" in meta:
            api_date = meta["deezer_info"].get("release_date", "")
            if api_date:
                api_year = api_date.split("-")[0]
        elif "mb_info" in meta:
            api_date = meta["mb_info"].get("date", "")
            if api_date:
                api_year = api_date.split("-")[0]

        if api_year:
            meta["year"] = str(api_year)
        if api_date:
            meta["release_date"] = api_date

    def _consolidate_edition(self, meta: dict) -> None:
        """Resolve edition. manual > local parser > MusicBrainz disambiguation."""
        if meta.get("manual_edition"):
            meta["edition"] = meta["manual_edition"]
            return
        if meta.get("edition"):
            return
        if "mb_info" not in meta:
            return

        edition = meta["mb_info"].get("disambiguation", "").strip().title()
        corrections = {"itunes": "iTunes", " for ": " for ", "Cd ": "CD ", "Th ": "th "}
        for original, correction in corrections.items():
            edition = re.sub(r"\b" + re.escape(original) + r"\b", correction, edition, flags=re.IGNORECASE)
        edition = re.sub(r"(\d)Th", r"\1th", edition)
        low_ed = edition.lower()
        if "single" in low_ed and not self._is_single_release(meta):
            edition = ""
        if edition:
            meta["edition"] = edition

    def _consolidate_source(self, meta: dict) -> None:
        """Resolve physical/digital source. Physical evidence always wins over WEB."""
        physical_source = None
        if (
            meta.get("user_proof")
            or meta.get("type") in {"DSF", "DFF"}
            or meta.get("log_files")
            or meta.get("foo_dr")
            or meta.get("aucdtect")
            or any(Path(f).suffix.lower() == ".cue" for f in meta.get("filelist", []))
            or (meta.get("catalog_number") and meta["catalog_number"].startswith("CD"))
        ):
            physical_source = "CD"

        if meta.get("manual_source"):
            return

        if physical_source:
            meta["source"] = physical_source
            meta["service"] = ""
            return

        nfo_source = (meta.get("nfo_parsed", {}).get("source") or "").upper()
        if nfo_source == "CDDA":
            nfo_source = "CD"

        if nfo_source:
            meta["source"] = nfo_source
            if nfo_source == "WEB":
                meta["service"] = ""
            return

        has_digital_service = meta.get("service", "").upper() in self.music_services

        if has_digital_service:
            meta["source"] = "WEB"
        elif "mb_info" in meta:
            media = meta["mb_info"].get("media", [])
            if media:
                fmt = media[0].get("format", "")
                if fmt:
                    meta["source"] = fmt.replace("Digital Media", "WEB")
        elif "discogs_info" in meta:
            fmt = meta["discogs_info"].get("format", "")
            if fmt:
                fmt_upper = fmt.upper()
                if fmt_upper == "DIGITAL":
                    fmt_upper = "WEB"
                meta["source"] = fmt_upper

    def _consolidate_keywords(self, meta: dict) -> None:
        """Build keywords set from all sources."""
        keywords = set((meta.get("keywords") or "").split(", "))
        if "mb_info" in meta:
            status = meta["mb_info"].get("status", "").lower()
            if status in ["bootleg", "unofficial", "promotion", "withdrawn"]:
                keywords.add(status)
            rg = meta["mb_info"].get("release_group", {})
            ptype = rg.get("primary-type", "").lower()
            if ptype in ["compilation", "live"]:
                keywords.add(ptype)
            if ptype == "single" and self._is_single_release(meta):
                keywords.add("single")
            for st in rg.get("secondary-types", []):
                stl = st.lower()
                if stl in ["compilation", "live"]:
                    keywords.add(stl)
                if stl == "single" and self._is_single_release(meta):
                    keywords.add("single")
            keywords.update(meta["mb_info"].get("genres", []))
        if "discogs_info" in meta:
            genres = [g.lower() for g in meta["discogs_info"].get("genres", [])]
            styles = [s.lower() for s in meta["discogs_info"].get("styles", [])]
            keywords.update(genres + styles)
        if "deezer_info" in meta:
            genres = [g.lower() for g in meta["deezer_info"].get("genres", [])]
            keywords.update(genres)
        if nfo_notes := meta.get("nfo_notes"):
            keywords.update(line.strip() for line in nfo_notes.splitlines() if line.strip())
        if len(meta.get("music_files", [])) == 1:
            keywords.add("single")
        meta["keywords"] = ", ".join(sorted(k for k in keywords if k)).strip(", ")

    def _consolidate_catalog_number(self, meta: dict) -> None:
        """Prefer MB catalog number."""
        if "mb_info" in meta:
            cat = meta["mb_info"].get("catalog_number")
            if cat:
                meta["catalog_number"] = cat

    def _finalize_artist_formatting(self, meta: dict) -> None:
        """Normalize artist separators to ' & '."""
        if meta.get("artist"):
            names = [a.strip() for a in meta["artist"].split(",") if a.strip()]
            if len(names) > 1:
                meta["artist"] = self._format_artists(names)

    def _append_identifiers_to_keywords(self, meta: dict) -> None:
        """Append discogs_id, catalog_number, barcode to keywords."""
        for key, value in [
            ("discogs_id", "Discogs: "),
            ("discogs_master", "Discogs Master: "),
            ("catalog_number", "CATNO: "),
            ("barcode", "UPC: "),
        ]:
            if meta.get(key):
                meta["keywords"] += f", {value}{meta[key]}" if meta["keywords"] else f"{value}{meta[key]}"

    async def _resolve_tracklist(
        self, meta: dict, tracks: list[tuple[str, str, Path]]
    ) -> dict[str, dict[str, str]]:
        entries = []

        mb_tracks = meta.get("mb_info", {}).get("media", [])
        deezer_tracks = meta.get("deezer_info", {}).get("tracks", {}).get("data", [])
        discogs_tracks = meta.get("discogs_info", {}).get("tracklist", [])

        if deezer_tracks:
            for idx, t in enumerate(deezer_tracks):
                track_pos = t.get("track_position")
                track_num = int(track_pos) if track_pos is not None else (idx + 1)
                entries.append(
                    {
                        "disc": t.get("disk_number", 1),
                        "track": track_num,
                        "title": t.get("title", "Unknown Track"),
                        "duration": self._seconds_to_duration(t.get("duration")),
                    }
                )

        elif discogs_tracks:
            for idx, t in enumerate(meta["discogs_info"].get("tracklist", []), start=1):
                pos = t.get("position", "").strip()

                disc = 1
                track = idx

                if "-" in pos:
                    d, tr = pos.split("-", 1)

                    if d.isdigit():
                        disc = int(d)

                    tr_digits = re.sub(r"\D", "", tr)

                    if tr_digits:
                        track = int(tr_digits)

                elif pos.isdigit():
                    track = int(pos)

                entries.append(
                    {
                        "disc": disc,
                        "track": track,
                        "title": t.get("title", "Unknown Track"),
                        "duration": self._normalize_duration(t.get("duration")),
                    }
                )

        elif mb_tracks:
            for medium in meta["mb_info"].get("media", []):
                disc = int(medium.get("disc", 1))

                for idx, t in enumerate(medium.get("tracks", []), start=1):
                    track = self._normalize_track_number(t.get("number"), idx)

                    entries.append(
                        {
                            "disc": disc,
                            "track": track,
                            "title": t.get("title", "Unknown Track"),
                            "duration": self._milliseconds_to_duration(t.get("duration_ms")),
                        }
                    )

        if not entries and meta.get("mb_info"):
            rg = meta["mb_info"].get("release_group", {})
            has_single_type = rg.get("primary-type", "").lower() == "single" or any(
                st.lower() == "single" for st in rg.get("secondary-types", [])
            )
            if has_single_type and not self._is_single_release(meta):
                log.debug("Ignoring MB single release tracklist due to multi-track local files")
                entries = []

        if not entries and meta.get("discogs_info"):
            fmt = meta.get("discogs_info", {}).get("format", "")
            if fmt and "single" in fmt.lower() and not self._is_single_release(meta):
                log.debug("Ignoring Discogs single release tracklist due to multi-track local files")
                entries = []

        if not entries and meta.get("deezer_info"):
            album_type = (meta.get("deezer_info") or {}).get("type", "")
            if album_type and "single" in album_type.lower() and not self._is_single_release(meta):
                log.debug("Ignoring Deezer single release tracklist due to multi-track local files")
                entries = []

        elif meta.get("nfo_parsed", {}).get("tracklist"):
            nfo_tracklist = meta["nfo_parsed"]["tracklist"]
            for track_num, title, disc_num in nfo_tracklist:
                entries.append(
                    {
                        "disc": disc_num if disc_num else 1,
                        "track": track_num,
                        "title": title,
                        "duration": None,
                    }
                )

        elif not entries:
            for idx, (disc_name, file_name, _) in enumerate(tracks, start=1):
                disc_match = re.search(r"\d+", disc_name)
                disc = int(disc_match.group()) if disc_match else 1

                clean_name = os.path.splitext(file_name)[0]
                track_match = re.match(r"(\d+)[\s.\-_]+(.+)", clean_name)

                if track_match:
                    track = int(track_match.group(1))
                    title = track_match.group(2).strip()
                else:
                    track = idx
                    title = clean_name

                entries.append({"disc": disc, "track": track, "title": title, "duration": None})

        mb_lookup = {}
        if meta.get("mb_info"):
            for medium in meta["mb_info"].get("media", []):
                disc = int(medium.get("disc", 1))

                for idx, t in enumerate(medium.get("tracks", []), start=1):
                    track = self._normalize_track_number(t.get("number"), idx)

                    duration = self._milliseconds_to_duration(t.get("duration_ms"))

                    if duration:
                        mb_lookup[(disc, track)] = duration

        deezer_lookup = {}

        if deezer_tracks:
            for idx, t in enumerate(deezer_tracks):
                disc = int(t.get("disk_number", 1))
                track_pos = t.get("track_position")
                track = int(track_pos) if track_pos is not None else (idx + 1)
                duration = self._seconds_to_duration(t.get("duration"))
                if duration:
                    deezer_lookup[(disc, track)] = duration

        local_lookup = {}

        duration_tasks = []

        for disc_name, file_name, path in tracks:
            disc_match = re.search(r"\d+", disc_name)
            disc = int(disc_match.group()) if disc_match else 1
            clean_name = os.path.splitext(file_name)[0]
            track_match = re.match(r"(\d+)", clean_name)

            track = int(track_match.group(1)) if track_match else 1

            duration_tasks.append((disc, track, path))

        async def get_local_duration(disc, track, path):
            loop = asyncio.get_running_loop()
            duration = None

            try:
                tag = await loop.run_in_executor(None, TinyTag.get, path)
                if tag and tag.duration:
                    duration = tag.duration
            except Exception as e:
                log.warning(f"TinyTag failed for {path}: {e}")

            if duration is None:
                try:
                    probe = await loop.run_in_executor(None, ffmpeg.probe, str(path))
                    duration = float(probe["format"]["duration"])
                except Exception as e:
                    log.warning(f"ffprobe failed for {path}: {e}")

            if duration is None and meta.get("mb_info"):
                for medium in meta["mb_info"].get("media", []):
                    if int(medium.get("disc", 1)) == disc:
                        tracks_mb = medium.get("tracks", [])
                        if track <= len(tracks_mb):
                            ms = tracks_mb[track - 1].get("duration_ms", 0)
                            if ms > 0:
                                dur = timedelta(milliseconds=ms)
                                m, s = divmod(dur.seconds, 60)
                                return disc, track, f"{m:02}:{s:02}"

            if duration is not None:
                seconds = int(duration)
                m, s = divmod(seconds, 60)
                return disc, track, f"{m:02}:{s:02}"

            return disc, track, None

        results = await asyncio.gather(*[get_local_duration(d, t, p) for d, t, p in duration_tasks])

        for disc, track, duration in results:
            if duration:
                local_lookup[(disc, track)] = duration

        for entry in entries:
            if entry["duration"]:
                continue

            key = (entry["disc"], entry["track"])

            entry["duration"] = (
                deezer_lookup.get(key) or mb_lookup.get(key) or local_lookup.get(key) or "00:00"
            )

        tracklist = {}
        disc_totals = {}

        for entry in entries:
            disc_totals.setdefault(entry["disc"], 0)
            disc_totals[entry["disc"]] += 1

        for entry in sorted(entries, key=lambda e: (e["disc"], e["track"])):
            disc_name = f"Disc {entry['disc']}"

            if disc_name not in tracklist:
                tracklist[disc_name] = {}

            pad = len(str(disc_totals[entry["disc"]]))

            key = f"{str(entry['track']).zfill(pad)}. {entry['title']}"

            tracklist[disc_name][key] = entry["duration"]

        return tracklist

    def _milliseconds_to_duration(self, ms) -> str | None:
        if not ms:
            return None

        try:
            seconds = int(ms) // 1000

            if seconds <= 0:
                return None

            m, s = divmod(seconds, 60)

            return f"{m:02}:{s:02}"

        except Exception:
            return None

    def _seconds_to_duration(self, seconds) -> str | None:
        if not seconds:
            return None

        try:
            seconds = int(seconds)

            if seconds <= 0:
                return None

            m, s = divmod(seconds, 60)

            return f"{m:02}:{s:02}"

        except Exception:
            return None

    def _normalize_duration(self, value) -> str | None:
        if not value:
            return None

        if isinstance(value, str):
            value = value.strip()

            if not value:
                return None

            if ":" in value:
                return value

            if value.isdigit():
                return self._seconds_to_duration(int(value))

        return None

    def _likely_physical_source(self, meta: dict) -> str | None:
        if meta.get("user_proof"):
            return "CD"
        if meta.get("type") in {"DSF", "DFF"}:
            return "CD"
        return None

    def _get_music_source(self, normalized_af: str) -> str:
        nafu = normalized_af.upper()
        nafu_collapsed = re.sub(r"[^A-Z0-9]", " ", nafu)
        if " MCD " in f" {nafu_collapsed} ":
            return "CD"
        if " CD " in f" {nafu_collapsed} ":
            return "CD"
        if " VINYL " in f" {nafu_collapsed} ":
            return "VINYL"
        if " WEB " in f" {nafu_collapsed} ":
            return "WEB"
        return ""

    def _extract_tag(self, album_fname: str) -> str:
        album_fname = re.sub(r"hi-res", "", album_fname, flags=re.IGNORECASE)
        last_hyphen_index = album_fname.rfind("-")

        tag_match = ""

        if last_hyphen_index != -1:
            after_last_hyphen = album_fname[last_hyphen_index + 1 :].strip()
            outside_parentheses = re.sub(r"\(.*?\)", "", after_last_hyphen).strip()

            if outside_parentheses:
                bracket_tag_match = re.search(r"\[([^\[\]]+)\]$", outside_parentheses)
                if bracket_tag_match:
                    tag = bracket_tag_match.group(1).strip()

                    if self.is_valid_tag(tag):
                        tag_match = f"-{tag}"

                else:
                    tag = re.search(r"^[^-\[\]]+$", outside_parentheses)
                    if tag:
                        tag = tag.group(0).strip()

                        if self.is_valid_tag(tag):
                            tag_match = f"-{tag}"

        return tag_match if tag_match else ""

    def is_valid_tag(self, tag: str) -> bool:
        """Check if a tag is valid for music releases."""
        if not tag or not isinstance(tag, str):
            return False

        tag_lower = tag.lower()
        BARCODE_PATTERN = re.compile(r"\d{2,}")
        CAT_PATTERN = re.compile(r"^[a-zA-Z0-9&]+$")

        if any(term in tag_lower for term in INVALID_TAG_WORDS):
            return False
        if " " in tag:
            return False
        if BARCODE_PATTERN.search(tag):
            return False
        if not CAT_PATTERN.match(tag):
            return False
        if len(tag) > 15:
            return False
        return not tag.isdigit()

    def _extract_catalog_number(self, album_fname: str, extra: dict) -> str | None:
        """Extract CATALOG_NUMBER from album_fname or MediaInfo."""
        log.debug(f"Extracting catalog number from: {album_fname}")

        # Try structured metadata first (fast path)
        for key in ("CATALOG_NUMBER", "CATALOGNUMBER", "CATNO", "LABELNO"):
            if catalog := extra.get(key):
                cleaned = re.sub(r"[ -]", "", catalog.strip())
                log.debug(f"Found catalog number in extra[{key}]: {cleaned}")
                return cleaned

        # Try filename patterns: {catalog} or [catalog, label, year, ...]
        patterns = [(r"\{([^}]+)\}", "curly braces"), (r"\[([A-Za-z0-9\s,]+)\]", "square brackets")]

        for pattern, _source in patterns:
            if match := re.search(pattern, album_fname):
                candidate_str = match.group(1).strip()
                parts = (part.strip() for part in candidate_str.split(",") if part.strip())
                for part in parts:
                    lower_part = part.lower()

                    # Skip if it looks like a year
                    if re.fullmatch(r"\d{4}", part):
                        continue

                    # Skip if it contains a label-related word
                    if any(word in lower_part for word in LABEL_WORDS):
                        log.debug(f"Skipped part (label word): {part}")
                        continue

                    # Must contain at least one alphanumeric char (avoid empty/symbols)
                    if not re.search(r"[A-Za-z0-9]", part):
                        continue

                    # Found a plausible candidate
                    log.debug(f"Selected catalog number: {part}")
                    catalog_number = part
                    break
                else:
                    continue  # no valid part in this candidate
                break  # valid catalog_number found
        else:
            catalog_number = None

        if catalog_number:
            if any(excl in catalog_number.lower() for excl in INVALID_TAG_WORDS):
                log.debug(f"Excluded due to IGNORE_TERM: {catalog_number}")
                return None
            return re.sub(r"[ -]", "", catalog_number.strip())

        log.debug("No catalog number found.")
        return None

    def _has_sufficient_music_metadata(self, meta: dict) -> bool:
        required = [
            "artist",
            "album",
            "year",
            "source",
            "type",
            "bitrate",
            "duration",
            "tracklist",
            "album_cover",
        ]
        return all(meta.get(k) for k in required)

    def _is_single_release(self, meta: dict) -> bool:
        """Heuristic: return True if release is plausibly a single."""
        music_files = meta.get("music_files", [])
        if len(music_files) == 1:
            return True
        uuid = meta.get("uuid", "")
        if re.search(r"(^|[-_])(?:SINGLE)(?:[-_]|$)", uuid, re.I):
            return True
        mb_rg = meta.get("mb_info", {}).get("release_group", {})
        if "single" in [mb_rg.get("primary_type", "").lower()] + [
            s.lower() for s in mb_rg.get("secondary_types", [])
        ]:
            return True
        discogs_fmt = meta.get("discogs_info", {}).get("format", "")
        return bool(discogs_fmt and "single" in discogs_fmt.lower())

    def get_service(self, meta: dict) -> str:
        """Determine the music service from metadata."""
        if meta.get("service"):
            return meta["service"]

        # Check mediainfo for SOURCE in extra
        mi = meta.get("mediainfo", {})
        if mi and "media" in mi and "track" in mi["media"]:
            for track in mi["media"]["track"]:
                if track.get("@type") == "General":
                    extra = track.get("extra", {})
                    for key, value in extra.items():
                        if key.upper() == "SOURCE":
                            service = self._validate_service(value)
                            if service:
                                return service
                    break

        # Fallback to uuid
        uuid = meta.get("uuid", "")
        service = self._extract_service_from_uuid(uuid)
        if service:
            service = self._validate_service(service)
            if service:
                return service

        return ""

    def _validate_service(self, service_name: str) -> str:
        """Validate and return the service code if found in music_services."""
        if not service_name:
            return ""
        service_name_upper = service_name.upper()
        for code, aliases in self.music_services.items():
            for alias in aliases:
                if alias.upper() == service_name_upper:
                    return code
        return ""

    def _extract_service_from_uuid(self, uuid: str) -> str:
        """Extract potential service from uuid as last resort."""
        if not uuid:
            return ""
        uuid_upper = uuid.upper()
        for _code, aliases in self.music_services.items():
            for alias in aliases:
                if alias.upper() in uuid_upper:
                    return alias
        return ""

    @staticmethod
    def _process_mb_relations(relations: dict[str, list[str]], meta: dict) -> None:
        """Populate meta from cleaned relations (now dict of lists)."""
        for url in relations.get("discogs", []):
            if match := re.search(r"discogs\.com/release/(\d+)", url):
                meta["discogs_id"] = match.group(1)
                meta.pop("discogs_master", None)
                break
            elif match := re.search(r"discogs\.com/master/(\d+)", url):
                meta["discogs_master"] = match.group(1)

        # Single-value
        for key, meta_key in [("allmusic", "allmusic"), ("wikidata", "wikidata")]:
            if urls := relations.get(key):
                meta[meta_key] = urls[0]

        # Genius
        for url in relations.get("genius", []) + relations.get("lyrics", []):
            if "genius.com" in url:
                meta["genius"] = url
                break

    async def _parse_nfo(self, nfo_path: Path) -> dict[str, Any] | None:
        text = await asyncio.to_thread(nfo_path.read_text, encoding="utf-8", errors="replace")
        text = re.sub(r"\[(?:img|url)(?:=[^\]]*)?\]", " ", text)
        text = re.sub(r"\[\/(?:img|url)\]", " ", text)

        result: dict[str, Any] = {}

        lines = text.splitlines()
        tracklist_active = False
        notes_active = False
        notes_lines: list[str] = []
        tracklist_pat = re.compile(r"^\s*(\d+)[\.\s]+(.+?)\s{2,}(\d+:\d{2}(?::\d{2})?)\s*$")
        disc_pat = re.compile(r"^disc\s+(\d+)(?:/\d+)?$", re.IGNORECASE)
        current_disc = 0

        known_keys = {
            "artist",
            "title",
            "label",
            "genre",
            "year",
            "duration",
            "size",
            "source",
            "encoder",
            "bitrate",
            "source_url",
            "url",
            "notes",
        }

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue

            if disc_pat.match(line):
                current_disc = int(disc_pat.match(line).group(1))
                continue

            key: str | None = None
            value: str = line
            for sep in (":", "=", "\t", "|"):
                if sep in line:
                    idx = line.index(sep)
                    key = line[:idx].strip()
                    value = line[idx + 1 :].strip()
                    break

            if key is None:
                low = line.lower()
                if tracklist_pat.match(line):
                    m = tracklist_pat.match(line)
                    title = " ".join(m.group(2).split())
                    result.setdefault("tracklist", []).append(
                        (int(m.group(1)), title, current_disc if current_disc else 0)
                    )
                elif current_disc and tracklist_active:
                    stripped = line.strip()
                    if stripped:
                        result.setdefault("tracklist", []).append(
                            (len(result.get("tracklist", [])) + 1, stripped, current_disc)
                        )
                elif low in {"tracks", "tracklist"}:
                    tracklist_active = True
                continue

            if key.lower() not in known_keys:
                low = self._normalize_key(key)
                if low in {"tracklist", "tracks"}:
                    tracklist_active = True
                elif low == "notes":
                    notes_active = True
                continue

            norm = self._normalize_key(key)
            if norm == "notes":
                notes_active = True
            elif norm == "source_url":
                value = value.strip() or None
                if value and not re.search(r"^https?://", value, re.IGNORECASE):
                    value = None
            elif norm in {"artist", "title", "label", "genre", "size", "encoder", "bitrate"}:
                value = value.strip()
            elif norm == "year":
                value = re.search(r"\d{4}", value)
                value = value.group(0) if value else value.strip()
            elif norm == "duration":
                value = value.strip()
            elif norm == "source":
                value = re.sub(r"\s+", " ", value.strip()).upper()

            if notes_active and norm != "notes":
                if notes_lines:
                    result.setdefault("notes", "\n".join(notes_lines))
                notes_active = False

            if value or value == "":
                result[norm] = value

        return result
