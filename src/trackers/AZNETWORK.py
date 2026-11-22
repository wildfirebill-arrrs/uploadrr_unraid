import asyncio
import json
import re
import time
import uuid as _uuid
from pathlib import Path
from typing import Any, ClassVar, Optional, Union

import niquests
from rich.pretty import Pretty
from selectolax.lexbor import LexborHTMLParser

from src.mediainfo import MediaInfoHandler
from src.progress import ProgressManager
from src.utils.console import console, log
from src.utils.cookieme import CookieMonster
from src.utils.descgen import DescGenerator
from src.utils.torrentman import edit_torrent, get_infohash

__uploadrr__ = Path(__file__).resolve().parent.parent.parent


class AZNetwork:
    type = "azn"
    tracker: str = ""
    source_flag: str = ""
    domain: str = ""
    allowed_imghosts: ClassVar[list[str]] = ["selfhost"]
    # fmt:off
    _PHD_ALLOWED: ClassVar[set[str]] = {
        "AG", "AI", "AU", "BB", "BM", "BS", "BZ", "CA", "CW", "DM",
        "GB", "GD", "IE", "JM", "KN", "KY", "LC", "MS", "NZ", "PR",
        "TC", "TT", "US", "VC", "VG", "VI",
    }

    _AZ_ALLOWED: ClassVar[set[str]] = {
        "BD", "BN", "BT", "CN", "HK", "ID", "IN", "JP", "KH", "KP",
        "KR", "LA", "LK", "MM", "MN", "MO", "MY", "NP", "PH", "PK",
        "SG", "TH", "TL", "TW", "VN",
    }
    # fmt:on

    @staticmethod
    def _azn_load_mediainfo(meta: dict[str, Any]) -> dict[str, Any]:
        handler = MediaInfoHandler._instance
        mi = handler._cache.get(meta["uuid"]) if handler else None
        if mi:
            return mi
        path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / "MediaInfo.json"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return {}

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.timeout = niquests.TimeoutConfiguration(45.0)
        tracker_config = config["TRACKERS"][self.tracker]
        self.username = str(tracker_config.get("username", "")).strip()
        self.password = str(tracker_config.get("password", "")).strip()
        self.pid = str(tracker_config.get("PID", "")).strip()
        self.requests_url = str(tracker_config.get("requests_url", f"{self.domain}/requests")).rstrip("/")
        self.announce_url = f"{self.domain}/announce".replace("//", "//tracker.")
        if (self.username or self.password) == "None":
            log.warning(f"Invalid user/pass for {self.tracker}")
        self.cookie = __uploadrr__ / f"data/cookies/{self.tracker}.pkl"
        self.cookie_monster = CookieMonster(self.cookie)
        self.lang_map: dict[str, str] = {}
        self.secret_token: Optional[str] = None
        self.media_id: Optional[str] = None
        self.jackett_token: Optional[str] = None
        self.session: Optional[niquests.AsyncSession] = None

    async def start_session(self) -> None:
        if self.session is not None:
            log.warning("Previous session was not closed properly. Closing it now.")
            await self.close_session()
        self.session = niquests.AsyncSession(disable_http3=True)

    async def close_session(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None

    @staticmethod
    def _is_authenticated_response(response_text: str) -> bool:
        return "authkey=" in response_text or "logout.php?auth=" in response_text or "Logout" in response_text

    async def _scrape_token(self, url: str) -> Optional[str]:
        if self.session is None:
            return None
        try:
            response = await self.session.get(url)
            log.debug(f"_scrape_token status on {url}: {response.status_code}")
            tree = LexborHTMLParser(response.text)
            for selector in ("meta[name='_token']", "input[name='_token']", "meta[name='csrf-token']"):
                elem = tree.css_first(selector)
                if elem:
                    token = elem.attributes.get("content") or elem.attributes.get("value")
                    if token:
                        self.secret_token = token
                        log.debug("Fresh _token scraped successfully")
                        return token
            log.warning(f"No _token found on {url} (status {response.status_code})")
        except Exception as exc:
            log.debug(f"_scrape_token error: {exc}")
        return self.secret_token

    async def _get_valid_token(self, page_url: str) -> Optional[str]:
        if self.secret_token and getattr(self, "_token_timestamp", 0) > time.time() - 1800:
            return self.secret_token
        token = await self._scrape_token(page_url)
        if token:
            self._token_timestamp = time.time()
        return token

    async def ensure_authenticated(self) -> bool:
        if self.session is None:
            await self.start_session()
        if await self.cookie_monster.load(self.session):
            check_url = f"{self.domain}/upload/movie"
            try:
                resp = await self.session.get(check_url)
                if resp.status_code != 200:
                    log.warning(f"Cookie check returned {resp.status_code} on {check_url}")
                elif "cloudflare" in resp.text.lower():
                    log.warning(f"Cloudflare challenge detected on {check_url}")
                elif not self._is_authenticated_response(resp.text):
                    log.warning(f"Cookies missing auth markers on {check_url} (status {resp.status_code})")
                else:
                    self.secret_token = await self._get_valid_token(check_url) or self.secret_token
                    log.info("Web cookies valid")
                    return True
            except Exception as exc:
                log.warning(f"Cookie validation request failed: {exc}")
            self.session.cookies.clear()
        return False

    async def has_valid_cookies(self) -> bool:
        if self.session is not None:
            check_url = f"{self.domain}/upload/movie"
            try:
                resp = await self.session.get(check_url)
                valid = resp.status_code == 200 and self._is_authenticated_response(resp.text)
                if not valid:
                    log.warning(
                        f"Existing session cookies invalid: status={resp.status_code}, auth markers missing"
                    )
                return valid
            except Exception as exc:
                log.warning(f"Existing session cookie validation failed: {exc}")
                return False

        await self.start_session()
        try:
            loaded = await self.cookie_monster.load(self.session)
            if not loaded:
                log.warning("CookieMonster.load returned False")
                return False
            check_url = f"{self.domain}/upload/movie"
            resp = await self.session.get(check_url)
            valid = resp.status_code == 200 and self._is_authenticated_response(resp.text)
            if not valid:
                log.warning(
                    f"Loaded cookies invalid: status={resp.status_code}, auth markers missing on {check_url}"
                )
            return valid
        except Exception as exc:
            log.warning(f"Loaded cookie validation failed: {exc}")
            return False
        finally:
            await self.close_session()

    async def banned_content(self, meta: dict[str, Any]) -> tuple[bool, bool]:
        return False, False

    # ── Jackett helpers ────────────────────────────────────────────────────────

    async def _renew_jackett_token(self) -> bool:
        if not self.pid:
            log.error(f"{self.tracker} Jackett auth skipped: PID missing in config")
            return False
        if self.session is None:
            await self.start_session()
        payload = {"username": self.username, "password": self.password, "pid": self.pid}
        try:
            response = await self.session.post(
                f"{self.domain}/api/v1/jackett/auth", json=payload, timeout=self.timeout
            )
            response.raise_for_status()
            data = response.json()
            token = data.get("token")
            if token:
                self.jackett_token = token
                log.debug(f"{self.tracker} Jackett token renewed")
                return True
            log.warning(f"{self.tracker} Jackett auth response missing token: {data}")
        except Exception as exc:
            log.error(f"{self.tracker} Jackett token renewal failed: {exc}")
        return False

    async def _jackett_request(self, method: str, url: str, **kwargs: Any) -> Optional[niquests.Response]:
        if not self.jackett_token and not await self._renew_jackett_token():
            return None
        headers = kwargs.pop("headers", {})
        headers["Accept"] = "application/json"
        headers["Authorization"] = f"Bearer {self.jackett_token}"
        try:
            response = await getattr(self.session, method.lower())(
                url, headers=headers, timeout=self.timeout, **kwargs
            )
            if response.status_code in (401, 403):
                log.warning(f"{self.tracker} Jackett {method} got {response.status_code}, renewing token")
                if await self._renew_jackett_token():
                    headers["Authorization"] = f"Bearer {self.jackett_token}"
                    response = await getattr(self.session, method.lower())(
                        url, headers=headers, timeout=self.timeout, **kwargs
                    )
            if not response.ok:
                log.warning(f"{self.tracker} Jackett {method} {url} failed: {response.status_code}")
                return None
            return response
        except Exception as exc:
            log.error(f"{self.tracker} Jackett request error ({method} {url}): {exc}")
        return None

    async def _jackett_search(self, meta: dict[str, Any], limit: int = 50) -> dict[str, Any]:
        if self.session is None:
            await self.start_session()
        cat_type = "1" if meta["category"] == "MOVIE" else "2"
        params: dict[str, Any] = {"type": cat_type, "limit": limit}
        imdb_id = meta.get("imdb_id", "")
        tmdb = meta.get("tmdb", "")
        if imdb_id:
            params["imdb"] = f"tt{imdb_id}"
        elif tmdb:
            params["tmdb"] = str(tmdb)
        else:
            params["search"] = meta.get("title", "")

        all_data: list[dict[str, Any]] = []
        page = 1
        while True:
            if page > 1:
                params["page"] = page
            response = await self._jackett_request(
                "GET", f"{self.domain}/api/v1/jackett/torrents", params=params
            )
            if response is None:
                return {}
            try:
                data = response.json()
            except Exception:
                return {}
            items = data.get("data", []) if isinstance(data, dict) else []
            all_data.extend(items)
            current = data.get("current_page", 1) if isinstance(data, dict) else 1
            last = data.get("last_page", 1) if isinstance(data, dict) else 1
            if current >= last or len(all_data) >= limit:
                break
            page += 1

        return {"data": all_data[:limit]}

    # ── Search / media-id ──────────────────────────────────────────────────────

    async def search_existing(self, meta: dict[str, Any]) -> list[dict[str, Any]]:
        dupes: list[dict[str, Any]] = []
        await self.start_session()
        try:
            result = await self._jackett_search(meta, limit=50)
            items = result.get("data", []) if isinstance(result, dict) else []
            type_id = self.get_type_id(meta)
            allowed_types = ["2", "1", "6"] if type_id == "2" else [type_id]

            for item in items:
                item_rip_id = str(item.get("rip_type_id") or "")
                if item_rip_id and item_rip_id not in allowed_types:
                    continue
                dupes.append(
                    {
                        "name": item.get("file_name", ""),
                        "size": item.get("file_size", 0),
                        "link": item.get("url", ""),
                    }
                )
        finally:
            await self.close_session()
        return dupes

    async def _search_ajax(self, cat: str, term: str, category_lower: str) -> Optional[list[dict[str, Any]]]:
        """Search the AZN ajax endpoint. Returns None on request failure (distinct from empty results)."""
        url = f"{self.domain}/ajax/movies/{cat}"
        params = {"term": term}
        headers = {"X-Requested-With": "XMLHttpRequest", "Referer": f"{self.domain}/upload/{category_lower}"}
        try:
            response = await self.session.get(url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict):
                return data.get("data", []) or []
            if isinstance(data, list):
                return data
            return []
        except niquests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            log.error(f"{self.tracker}: AJAX search for '{term}' returned HTTP {status}")
            return None
        except Exception as exc:
            log.error(f"{self.tracker}: AJAX search for '{term}' failed: {exc}")
            return None

    def _find_authoritative_match(
        self, results: list[dict[str, Any]], imdb_id: str, tmdb_id: str, title: str, year: Any
    ) -> Optional[dict[str, Any]]:
        for item in results:
            if imdb_id and item.get("imdb") == f"tt{imdb_id}":
                return item
            if tmdb_id and str(item.get("tmdb")) == str(tmdb_id):
                return item
        for item in results:
            item_title = item.get("title", "")
            if item_title and item_title.lower() == title.lower():
                if str(item.get("release_year")) == str(year):
                    return item
        return None

    async def get_media_id(self, meta: dict[str, Any]) -> bool:
        cached_key = f"{self.tracker}_id"
        if meta.get(cached_key):
            self.media_id = str(meta[cached_key])
            return True
        if meta["category"] not in ("MOVIE", "TV"):
            return False
        cat = "1" if meta["category"] == "MOVIE" else "2"
        cat_lower = meta["category"].lower()
        imdb_id = meta.get("imdb_id", "")
        tmdb_id = meta.get("tmdb", "")
        title = meta.get("title", "")
        year = meta.get("year", "")

        # Attempt 0: search by IMDB ID then by title
        any_search_succeeded = False
        search_terms = [t for t in (f"tt{imdb_id}" if imdb_id else None, title if title else None) if t]
        for term in search_terms:
            results = await self._search_ajax(cat, term, cat_lower)
            if results is None:
                continue
            any_search_succeeded = True
            if not results:
                continue
            match = self._find_authoritative_match(results, imdb_id, tmdb_id, title, year)
            if match:
                self.media_id = str(match["id"])
                meta[cached_key] = self.media_id
                return True

        # If every search attempt hit server errors, we can't determine if media exists
        if not any_search_succeeded:
            log.error(
                f"{self.tracker}: Cannot verify media — AJAX search returned server errors. Retry later."
            )
            return False

        # Searches succeeded but no authoritative match found — offer to add media to DB then retry
        log.warning(f"{self.tracker}: Media [IMDB:tt{imdb_id}] [TMDB:{tmdb_id}] not found in site database.")

        # Adding to DB requires authenticated session
        if not await self.ensure_authenticated():
            log.error(f"{self.tracker}: Cannot add media to DB — authentication failed")
            return False

        pm = ProgressManager.get_instance()
        with pm.pause_for_interaction():
            if meta.get("unattended", False):
                # Auto-add in unattended mode so uploads don't silently fail
                log.info(f"{self.tracker}: Unattended mode — auto-adding media to database")
            else:
                console.print(
                    f"\n[yellow]{self.tracker}: Media not found on site database. Add it now? (y/n)[/yellow]"
                )
                from rich.prompt import Confirm

                if not Confirm.ask("Add to database?", default=True):
                    return False

        added = await self.add_media_to_db(meta, title, cat_lower, imdb_id, str(tmdb_id))
        if not added:
            log.error(f"{self.tracker}: Failed to add media to database. Aborting.")
            return False

        # Wait for DB propagation, then search again by IMDB ID
        await asyncio.sleep(5)
        log.info(f"{self.tracker}: Re-searching after adding media to database...")
        retry_term = f"tt{imdb_id}" if imdb_id else title
        results = await self._search_ajax(cat, retry_term, cat_lower)
        if results is None:
            log.error(
                f"{self.tracker}: Post-add search returned server error. Media was likely added — retry the upload."
            )
            return False
        if results:
            match = self._find_authoritative_match(results, imdb_id, tmdb_id, title, year)
            if match:
                self.media_id = str(match["id"])
                meta[cached_key] = self.media_id
                log.info(
                    f"{self.tracker}: Found newly added media: {self.domain}/{cat_lower}/{self.media_id}"
                )
                return True
            # DB may assign new IDs — check if IMDB/TMDB now matches any result
            for item in results:
                if imdb_id and item.get("imdb") == f"tt{imdb_id}":
                    self.media_id = str(item["id"])
                    meta[cached_key] = self.media_id
                    return True
                if tmdb_id and str(item.get("tmdb")) == str(tmdb_id):
                    self.media_id = str(item["id"])
                    meta[cached_key] = self.media_id
                    return True

        log.error(f"{self.tracker}: Media still not found after adding to database.")
        return False

    async def add_media_to_db(
        self, meta: dict[str, Any], title: str, cat: str, imdb_id: str, tmdb_id: str
    ) -> bool:
        cat_num = "1" if cat in ("movie", "1") else "2"
        if not self.secret_token:
            self.secret_token = await self._scrape_token(f"{self.domain}/upload/{cat}")
        if not self.secret_token:
            log.error("Cannot add media to DB: no CSRF token")
            return False
        data: dict[str, str] = {
            "_token": self.secret_token,
            "type_id": cat_num,
            "title": title,
            "imdb": f"tt{imdb_id}" if imdb_id else "",
            "tmdb": tmdb_id,
        }
        if cat_num == "2" and meta.get("tvdb_id"):
            data["tvdb_id"] = str(meta["tvdb_id"])
        try:
            response = await self.session.post(f"{self.domain}/add/{cat}", data=data)
            if response.status_code in (302, 303):
                return True
            tmp_dir = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
            debug_path = tmp_dir / f"[{self.tracker}]Failed_AddMedia.html"
            debug_path.write_text(response.text, encoding="utf-8")
            log.error(f"add_media_to_db failed: status {response.status_code}. Saved to {debug_path}")
            return False
        except Exception as exc:
            log.error(f"Failed to add media to DB: {exc}")
            return False

    async def get_file_info(self, meta: dict[str, Any]) -> str:
        tmp_dir = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
        for filename in ("BD_SUMMARY_EXT_00.txt", "BD_SUMMARY_00.txt", "MEDIAINFO.txt"):
            path = tmp_dir / filename
            if path.exists():
                return path.read_text(encoding="utf-8")
        return ""

    def language_map(self) -> dict[str, str]:
        base: dict[str, str] = {
            "Abkhazian": "1",
            "Afar": "2",
            "Afrikaans": "3",
            "Albanian": "5",
            "Amharic": "6",
            "Arabic": "7",
            "Assamese": "10",
            "Basque": "17",
            "Belarusian": "18",
            "Bengali": "19",
            "Bosnian": "23",
            "Bulgarian": "25",
            "Burmese": "26",
            "Cantonese": "27",
            "Catalan": "28",
            "Central Khmer": "29",
            "Chamorro": "30",
            "Chichewa": "32",
            "Chinese": "33",
            "Corsican": "37",
            "Croatian": "39",
            "Czech": "40",
            "Danish": "41",
            "Dutch": "43",
            "Dzongkha": "44",
            "English": "45",
            "Esperanto": "46",
            "Estonian": "47",
            "Fijian": "50",
            "Filipino": "188",
            "Finnish": "51",
            "French": "52",
            "Galician": "55",
            "Georgian": "57",
            "German": "58",
            "Greek": "59",
            "Gujarati": "61",
            "Haitian": "62",
            "Hausa": "63",
            "Hebrew": "64",
            "Hindi": "66",
            "Hungarian": "68",
            "Icelandic": "69",
            "Indonesian": "72",
            "Inuktitut": "75",
            "Irish": "77",
            "Italian": "78",
            "Japanese": "79",
            "Javanese": "80",
            "Kannada": "82",
            "Kashmiri": "84",
            "Kazakh": "85",
            "Kirghiz": "88",
            "Korean": "91",
            "Kurdish": "93",
            "Lao": "94",
            "Latin": "95",
            "Latvian": "96",
            "Lithuanian": "99",
            "Macedonian": "102",
            "Malagasy": "103",
            "Malay": "104",
            "Malayalam": "105",
            "Mandarin": "107",
            "Maori": "109",
            "Marathi": "110",
            "Mongolian": "112",
            "Nepali": "118",
            "Norwegian": "120",
            "Norwegian Bokm\u00e5l": "22",
            "Ojibwa": "123",
            "Oriya": "124",
            "Pali": "127",
            "Panjabi": "128",
            "Persian": "129",
            "Polish": "130",
            "Portuguese": "131",
            "Quechua": "133",
            "Romanian": "134",
            "Romansh": "135",
            "Russian": "137",
            "Sanskrit": "140",
            "Sardinian": "141",
            "Serbian": "142",
            "Sinhala": "146",
            "Slovak": "147",
            "Slovenian": "148",
            "Somali": "149",
            "Spanish": "151",
            "Sundanese": "152",
            "Swahili": "153",
            "Swedish": "155",
            "Tagalog": "156",
            "Tamil": "159",
            "Telugu": "161",
            "Thai": "162",
            "Tibetan": "163",
            "Turkish": "168",
            "Twi": "170",
            "Uighur": "171",
            "Ukrainian": "172",
            "Urdu": "173",
            "Venda": "175",
            "Vietnamese": "176",
            "Volap\u00fck": "177",
            "Welsh": "179",
            "Wolof": "181",
            "Xhosa": "182",
        }
        iso_aliases: dict[str, str] = {
            "eng": "45",
            "en": "45",
            "spa": "151",
            "es": "151",
            "fra": "52",
            "fr": "52",
            "deu": "58",
            "de": "58",
            "ita": "78",
            "it": "78",
            "jpn": "79",
            "ja": "79",
            "kor": "91",
            "ko": "91",
            "zho": "33",
            "zh": "33",
            "cmn": "107",
            "yue": "27",
            "por": "131",
            "pt": "131",
            "rus": "137",
            "ru": "137",
            "nld": "43",
            "nl": "43",
            "pol": "130",
            "pl": "130",
            "ara": "7",
            "ar": "7",
            "hin": "66",
            "hi": "66",
            "tur": "168",
            "tr": "168",
            "tha": "162",
            "th": "162",
            "ces": "40",
            "cs": "40",
            "dan": "41",
            "da": "41",
            "fin": "51",
            "fi": "51",
            "ell": "59",
            "el": "59",
            "heb": "64",
            "he": "64",
            "hun": "68",
            "hu": "68",
            "ind": "72",
            "id": "72",
            "nor": "120",
            "no": "120",
            "ron": "134",
            "ro": "134",
            "slk": "147",
            "sk": "147",
            "swe": "155",
            "sv": "155",
            "ukr": "172",
            "uk": "172",
            "vie": "176",
            "vi": "176",
            "tgl": "156",
            "tl": "156",
            "tam": "159",
            "ta": "159",
            "tel": "161",
            "te": "161",
            "ben": "19",
            "bn": "19",
            "cat": "28",
            "ca": "28",
            "hrv": "39",
            "hr": "39",
            "cze": "40",
            "dut": "43",
            "est": "47",
            "et": "47",
            "glg": "55",
            "gl": "55",
            "kat": "57",
            "ka": "57",
            "lav": "96",
            "lv": "96",
            "lit": "99",
            "lt": "99",
            "msa": "104",
            "ms": "104",
            "mlt": "105",
            "ml": "105",
            "nep": "118",
            "ne": "118",
            "pan": "128",
            "pa": "128",
            "fas": "129",
            "fa": "129",
            "srp": "142",
            "sr": "142",
            "sin": "146",
            "si": "146",
            "slv": "148",
            "sl": "148",
            "sun": "152",
            "su": "152",
            "swa": "153",
            "sw": "153",
            "bod": "163",
            "bo": "163",
            "uig": "171",
            "ug": "171",
            "urd": "173",
            "ur": "173",
            "cym": "179",
            "cy": "179",
            "gle": "77",
            "ga": "77",
        }
        base.update(iso_aliases)
        return base

    async def get_lang(self, meta: dict[str, Any]) -> dict[str, list[str]]:
        if not self.lang_map:
            self.lang_map = self.language_map()
        result: dict[str, list[str]] = {"languages[]": [], "subtitles[]": []}
        audio_langs = meta.get("languages", [])
        if not audio_langs and meta.get("is_disc") == "BDMV":
            bd_audio = meta.get("bdinfo", {}).get("audio", [])
            audio_langs = [track.get("language", "") for track in bd_audio]
        if not audio_langs:
            mi = self._azn_load_mediainfo(meta)
            tracks = mi.get("media", {}).get("track", []) if mi else []
            audio_langs = [
                t.get("Language_String") or t.get("Language_String3") or t.get("Language", "")
                for t in tracks
                if t.get("@type") == "Audio" and "commentary" not in (t.get("Title") or "").lower()
            ]
        for lang in audio_langs:
            lang_norm = lang.strip().lower()
            if lang_norm in self.lang_map:
                result["languages[]"].append(self.lang_map[lang_norm])
            else:
                lang_title = lang.strip().title()
                if lang_title in self.lang_map:
                    result["languages[]"].append(self.lang_map[lang_title])
        sub_langs = meta.get("subtitles", [])
        if not sub_langs and meta.get("is_disc") == "BDMV":
            bd_subs = meta.get("bdinfo", {}).get("subtitles", [])
            sub_langs = bd_subs
        if not sub_langs:
            mi = self._azn_load_mediainfo(meta)
            tracks = mi.get("media", {}).get("track", []) if mi else []
            sub_langs = [
                t.get("Language_String") or t.get("Language_String3") or t.get("Language", "")
                for t in tracks
                if t.get("@type") == "Text"
            ]
        for lang in sub_langs:
            lang_norm = lang.strip().lower() if isinstance(lang, str) else str(lang).strip().lower()
            if lang_norm in self.lang_map:
                result["subtitles[]"].append(self.lang_map[lang_norm])
            else:
                lang_title = lang.strip().title() if isinstance(lang, str) else str(lang).strip().title()
                if lang_title in self.lang_map:
                    result["subtitles[]"].append(self.lang_map[lang_title])
        return result

    @staticmethod
    def delete_desc(desc: str, chars: int = 0) -> str:
        if chars > 0:
            return desc[:-chars]
        return desc

    async def _upload_img(
        self, meta: dict[str, Any], referer: str, image_bytes: bytes, filename: str
    ) -> Optional[str]:
        if not self.secret_token:
            cat = "movie" if meta["category"] == "MOVIE" else "tv"
            self.secret_token = await self._get_valid_token(f"{self.domain}/upload/{cat}")
        if not self.secret_token:
            log.error("Cannot upload image: no CSRF token")
            return None
        qquuid = str(_uuid.uuid4())
        data = {
            "_token": self.secret_token,
            "qquuid": qquuid,
            "qqfilename": filename,
            "qqtotalfilesize": str(len(image_bytes)),
        }
        files = {"qqfile": (filename, image_bytes, "image/png")}
        headers = {"Referer": referer, "X-Requested-With": "XMLHttpRequest"}
        try:
            response = await self.session.post(
                f"{self.domain}/ajax/image/upload", data=data, files=files, headers=headers
            )
            response.raise_for_status()
            result = response.json()
            if result.get("success") and "imageId" in result:
                return str(result["imageId"])
            log.warning(f"Image upload failed: {result}")
        except Exception as exc:
            log.error(f"Image upload error: {exc}")
        return None

    async def _get_screenshots(self, meta: dict[str, Any]) -> list[str]:
        cached = meta.get("tracker_image_ids", {}).get(self.tracker)
        if cached is not None:
            return cached
        requested = int(meta.get("screens", 3))
        limit = max(3, min(requested, 15))
        tmp_dir = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
        cat = "movie" if meta["category"] == "MOVIE" else "tv"
        referer = f"{self.domain}/upload/{cat}"
        image_ids: list[str] = []
        menu_images = meta.get("menu_images", [])
        for item in menu_images[: min(12, limit)]:
            path = Path(item) if isinstance(item, (str, Path)) else None
            if path and path.exists():
                img_id = await self._upload_img(meta, referer, path.read_bytes(), path.name)
                if img_id:
                    image_ids.append(img_id)
                if len(image_ids) >= limit:
                    meta.setdefault("tracker_image_ids", {})[self.tracker] = image_ids
                    return image_ids
        for png in sorted(tmp_dir.glob("*.png")):
            img_id = await self._upload_img(meta, referer, png.read_bytes(), png.name)
            if img_id:
                image_ids.append(img_id)
            if len(image_ids) >= limit:
                meta.setdefault("tracker_image_ids", {})[self.tracker] = image_ids
                return image_ids
        image_list = meta.get("image_list", {})
        if isinstance(image_list, dict):
            remote_images = image_list.get("main", [])
        elif isinstance(image_list, list):
            remote_images = image_list
        else:
            remote_images = []
        for img in remote_images:
            url = img.get("raw_url") or img.get("img_url") or (img if isinstance(img, str) else "")
            if not url:
                continue
            try:
                resp = await self.session.get(url)
                if resp.status_code == 200:
                    filename = Path(url).name or "image.png"
                    img_id = await self._upload_img(meta, referer, resp.content, filename)
                    if img_id:
                        image_ids.append(img_id)
                    if len(image_ids) >= limit:
                        meta.setdefault("tracker_image_ids", {})[self.tracker] = image_ids
                        return image_ids
            except Exception:
                continue
        meta.setdefault("tracker_image_ids", {})[self.tracker] = image_ids
        return image_ids

    async def get_requests(self, meta: dict[str, Any]) -> list[dict[str, Any]]:
        requests: list[dict[str, Any]] = []
        cat = "1" if meta["category"] == "MOVIE" else "2"
        search = meta.get("title", "")
        params = {"type": cat, "search": search}
        try:
            response = await self.session.get(self.requests_url, params=params)
            response.raise_for_status()
            tree = LexborHTMLParser(response.text)
            for row in tree.css("table.table-requests tr"):
                tds = list(row.css("td"))
                if len(tds) < 3:
                    continue
                name_link = tds[0].css_first("a")
                if not name_link:
                    continue
                name = name_link.text(strip=True)
                link = name_link.attributes.get("href", "")
                if link and not link.startswith("http"):
                    link = f"{self.domain}{link}"
                reward = tds[-1].text(strip=True)
                requests.append({"Name": name, "Link": link, "Reward": reward})
        except Exception as exc:
            log.error(f"Failed to get requests: {exc}")
        return requests

    async def fetch_tag_id(self, word: str) -> Optional[int]:
        try:
            headers = {"X-Requested-With": "XMLHttpRequest", "Referer": f"{self.domain}/upload"}
            response = await self.session.get(
                f"{self.domain}/ajax/tags", params={"term": word}, headers=headers
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list) and len(data) > 0:
                return data[0].get("id")
            if isinstance(data, dict) and "id" in data:
                return data["id"]
        except Exception as exc:
            log.warning(f"Failed to fetch tag id for '{word}': {exc}")
        return None

    async def get_tags(self, meta: dict[str, Any]) -> list[int]:
        tags = []
        if meta.get("personalrelease", False) and self.personal_release_tag:
            tags.append(self.personal_release_tag)
        if (
            self.config["TRACKERS"][self.tracker].get("internal", False)
            and meta.get("tag", "")
            and meta["tag"][1:] in self.config["TRACKERS"][self.tracker].get("internal_groups", [])
        ):
            if self.internal_tag:
                tags.append(self.internal_tag)
        genre_tags = meta.get("genres", [])
        if isinstance(genre_tags, str):
            genre_tags = [g.strip() for g in genre_tags.split(",")]
        for genre in genre_tags:
            tag_id = await self.fetch_tag_id(genre.strip())
            if tag_id:
                tags.append(tag_id)
        return tags

    async def edit_desc(self, meta: dict[str, Any]) -> None:
        descgen = DescGenerator(config=self.config, base_dir=meta["base_dir"])
        whitelist = [] if self.allowed_imghosts == ["selfhost"] else self.allowed_imghosts
        await descgen.edit_desc(meta, self.tracker, whitelist=whitelist)

    def get_cat_id(self, category_name: str) -> str:
        return {"MOVIE": "1", "TV": "2"}.get(category_name, "1")

    def get_type_id(self, meta: dict[str, Any]) -> str:
        media_type = meta["type"]
        type_id = {
            "DISC": "3",
            "REMUX": "14",
            "ENCODE": "2",
            "WEBDL": "12",
            "WEBRIP": "13",
            "HDTV": "7",
            "HDDVD": "6",
        }.get(media_type, "14")
        return type_id

    def get_res_id(self, meta: dict[str, Any]) -> str:
        if meta.get("sd", 0) == 1:
            return "1"
        return {"720p": "2", "1080p": "3", "1080i": "3", "2160p": "4", "4320p": "4", "8640p": "4"}.get(
            meta.get("resolution", ""), "1"
        )

    def get_resolution(self, meta: dict[str, Any]) -> str:
        bdinfo = meta.get("bdinfo")
        if bdinfo:
            bd_res = bdinfo.get("resolution", "")
            if bd_res and "x" in bd_res:
                return bd_res
        handler = MediaInfoHandler._instance
        if handler is not None:
            dims = handler.get_resolution_dimensions(meta["uuid"])
            if dims:
                return dims
        return {
            "480p": "720x480",
            "576p": "720x576",
            "720p": "1280x720",
            "1080p": "1920x1080",
            "1080i": "1920x1080",
            "2160p": "3840x2160",
            "4320p": "7680x4320",
            "8640p": "15360x8640",
        }.get(meta.get("resolution", ""), meta.get("resolution", ""))

    async def create_task_id(self, meta: dict[str, Any], torrent_path: Path) -> dict[str, Optional[str]]:
        if not self.media_id:
            log.error("Cannot create task_id: media_id missing")
            return {}
        cat = "movie" if meta["category"] == "MOVIE" else "tv"
        cat_num = "1" if cat == "movie" else "2"
        upload_url = f"{self.domain}/upload/{cat}"
        token = await self._get_valid_token(upload_url)
        if not token:
            log.error("No CSRF _token available for upload")
            return {}
        data = {
            "_token": token,
            "type_id": cat_num,
            "movie_id": self.media_id,
            "media_info": await self.get_file_info(meta),
        }
        try:
            torrent_bytes = torrent_path.read_bytes()
            files = {"torrent_file": (torrent_path.name, torrent_bytes, "application/x-bittorrent")}
            headers = {"Referer": upload_url, "Origin": self.domain, "X-Requested-With": "XMLHttpRequest"}
            response = await self.session.post(upload_url, data=data, files=files, headers=headers)
            final_url = str(response.url)
            match = re.search(r"/upload/torrent/(\d+)", final_url)
            task_id = match.group(1) if match else None
            if not task_id:
                raw_text = response.text.strip()
                log.error(f"{self.tracker} upload: task_id not found in URL: {final_url}")
                if raw_text.startswith(("{", "[")):
                    log.error(f"Response body: {raw_text[:2000]}")
                return {}
            info_hash = get_infohash(torrent_path)
            return {"task_id": task_id, "info_hash": info_hash, "redirect_url": final_url}
        except Exception as exc:
            log.exception(f"Exception in create_task_id: {exc}")
            return {}

    async def fetch_data(self, meta: dict[str, Any]) -> dict[str, Any]:
        cat = "movie" if meta["category"] == "MOVIE" else "tv"
        token = await self._get_valid_token(f"{self.domain}/upload/{cat}")
        name = meta["name"]
        desc_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / f"[{self.tracker}]DESCRIPTION.txt"
        description = desc_path.read_text(encoding="utf-8") if desc_path.exists() else ""
        lang_data = await self.get_lang(meta)
        tags = await self.get_tags(meta)
        file_info = await self.get_file_info(meta)
        anon = (
            "1"
            if (
                meta.get("anon", 0)
                or self.config["TRACKERS"][self.tracker].get("anon", False)
                or self.config["DEFAULT"].get("always_anon", False)
            )
            else ""
        )
        data: dict[str, Any] = {
            "_token": token or "",
            "file_name": name,
            "description": description,
            "media_info": file_info,
            "rip_type_id": self.get_type_id(meta),
            "video_quality_id": self.get_res_id(meta),
            "video_resolution": self.get_resolution(meta),
            "movie_id": self.media_id or "",
            "anon_upload": anon,
            "tags[]": tags,
            "screenshots[]": [],
        }
        for key, values in lang_data.items():
            if values:
                data[key] = values
        if meta["category"] == "TV":
            data.update(
                {
                    "type_id": "2",
                    "tv_collection": "1" if meta.get("tv_pack", 0) == 0 else "2",
                    "tv_season": str(meta.get("season_int", "")),
                    "tv_episode": str(meta.get("episode_int", "")),
                }
            )
        else:
            data["type_id"] = "1"
        return data

    async def _post_draft(self, data: dict[str, Any]) -> bool:
        post_data: list[tuple[str, Any]] = []
        for key, val in data.items():
            if isinstance(val, list):
                for item in val:
                    post_data.append((key, item))
            else:
                post_data.append((key, val))
        files = {"qqfile": ("", b"", "application/octet-stream")}
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.domain}/upload/torrent/{data.get('task_id', '')}",
        }
        try:
            response = await self.session.post(
                f"{self.domain}/ajax/torrent/draft", data=post_data, files=files, headers=headers
            )
            if response.status_code >= 400:
                log.error(f"Draft save failed: {response.status_code} — {response.text[:500]}")
                return False
            body = response.text.strip()
            if "Draft Saved" in body or "draft" in body.lower():
                log.info(f"{self.tracker} Draft saved successfully")
                return True
            log.warning(f"{self.tracker} Draft response unexpected: {body[:300]}")
            return True
        except Exception as exc:
            log.error(f"Draft save error: {exc}")
            return False

    async def _download_and_finalize(self, torrent_path: Path, torrent_url: str, task_id: str) -> bool:
        """Download the torrent from the site (registers in download history) then edit it."""
        download_url = torrent_url.replace("/torrent/", "/download/torrent/")
        try:
            dl_resp = await self.session.get(download_url)
            if dl_resp.status_code == 200 and dl_resp.content:
                torrent_path.write_bytes(dl_resp.content)
                comment = f"Made by Uploadrr for: {torrent_url}"
                await edit_torrent(torrent_path=torrent_path, comment=comment)
                return True
            log.warning(
                f"{self.tracker}: Download returned {dl_resp.status_code} — fallback to editing local torrent"
            )
        except Exception as exc:
            log.warning(
                f"{self.tracker}: Failed to download torrent from site: {exc} — fallback to editing local torrent"
            )
        comment = f"Made by Uploadrr for: {torrent_url}"
        announce = f"{self.domain}/{self.pid}/announce".replace("//", "//tracker.")
        await edit_torrent(torrent_path=torrent_path, announce_url=announce, comment=comment)
        return True

    async def _check_torrent_live(self, info_hash: str) -> Optional[str]:
        """Search for the torrent by info_hash. Returns the torrent page URL if live, None otherwise."""
        await asyncio.sleep(3)
        url = f"{self.domain}/torrents"
        params = {"in": "2", "search": info_hash, "type": "0"}
        try:
            resp = await self.session.get(url, params=params)
            if resp.status_code != 200:
                log.debug(f"{self.tracker} torrent check returned {resp.status_code}")
                return None
            tree = LexborHTMLParser(resp.text)
            block = tree.css_first("div.block div.table-responsive")
            if block is None:
                return None
            row = block.css_first("table tbody tr")
            if row is None:
                return None
            link = row.css_first("a.torrent-filename")
            if link and link.attributes.get("href"):
                href = link.attributes["href"]
                return href if href.startswith("http") else f"{self.domain}{href}"
            return None
        except Exception as exc:
            log.debug(f"{self.tracker} torrent check error: {exc}")
            return None

    async def _try_reuse_task(
        self, meta: dict[str, Any], task_id: str, torrent_path: Path
    ) -> Optional[dict[str, Optional[str]]]:
        upload_url = f"{self.domain}/upload/torrent/{task_id}"
        try:
            response = await self.session.get(upload_url)
            if response.status_code == 200 and self._is_authenticated_response(response.text):
                info_hash = get_infohash(torrent_path)
                log.info(f"{self.tracker} Reusing existing task_id {task_id}")
                return {"task_id": task_id, "info_hash": info_hash, "redirect_url": upload_url}
        except Exception as exc:
            log.debug(f"Reuse task_id {task_id} check failed: {exc}")
        return None

    async def upload(self, meta: dict[str, Any], disctype: Optional[str] = None) -> Union[bool, str]:
        if not await self.ensure_authenticated():
            log.error("Authentication failed")
            return False
        cat = meta["category"].lower()
        token = await self._get_valid_token(f"{self.domain}/upload/{cat}")
        if not token:
            log.error("Failed to get fresh CSRF token")
            return False

        tmp_dir = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
        torrent_path = tmp_dir / f"[{self.tracker}]{meta['clean_name']}.torrent"
        await edit_torrent(meta, None, self.tracker, self.source_flag, self.announce_url)

        if not await self.get_media_id(meta):
            log.error(f"{self.tracker}: Cannot upload — no media_id (add-to-DB also failed or was declined)")
            return False

        task_key = f"{self.tracker}_task_id"
        cached_task_id = meta.get(task_key)
        task_info: dict[str, Optional[str]] = {}
        if cached_task_id:
            reused = await self._try_reuse_task(meta, cached_task_id, torrent_path)
            if reused:
                task_info = reused
            else:
                log.warning(
                    f"{self.tracker} Cached task_id {cached_task_id} no longer valid, creating new one"
                )
        if not task_info:
            task_info = await self.create_task_id(meta, torrent_path)
        task_id = task_info.get("task_id")
        if not task_id:
            log.error("Failed to get task_id from upload step 1")
            return False
        meta[task_key] = task_id
        data = await self.fetch_data(meta)
        data["task_id"] = task_id
        if task_info.get("info_hash"):
            data["info_hash"] = task_info["info_hash"]
        screenshots = await self._get_screenshots(meta)
        data["screenshots[]"] = screenshots

        if meta.get("debug", False):
            log.debug("[bright_magenta]DATA 2 SEND[/bright_magenta]")
            console.print(Pretty(data))
            await self._post_draft(data)
            await edit_torrent(meta, None, self.tracker, "DEBUG", self.announce_url, "Uploadrr DEBUG-MODE")
            return "DEBUG"

        if meta.get("draft", False):
            draft_ok = await self._post_draft(data)
            if draft_ok:
                await edit_torrent(
                    meta, None, self.tracker, "DRAFT", self.announce_url, "Uploadrr DRAFT-MODE"
                )
            return "DRAFT" if draft_ok else False

        redirect_url = task_info.get("redirect_url") or f"{self.domain}/upload/torrent/{task_id}"
        post_data: list[tuple[str, Any]] = []
        for key, val in data.items():
            if isinstance(val, list):
                for item in val:
                    post_data.append((key, item))
            else:
                post_data.append((key, val))
        info_hash = data.get("info_hash", "")
        try:
            response = await self.session.post(redirect_url, data=post_data)
            tree = LexborHTMLParser(response.text)
            error_div = tree.css_first("div.alert.alert-danger")
            if error_div:
                warning_text = error_div.text(strip=True)
                if info_hash:
                    torrent_page_url = await self._check_torrent_live(info_hash)
                    if torrent_page_url:
                        log.warning(f"{self.tracker}: (Upload is Live) Server Warns: {warning_text}")
                        await self._download_and_finalize(torrent_path, torrent_page_url, task_id)
                        return True
                draft_url = f"{self.domain}/upload/torrent/{task_id}"
                log.error(f"{self.tracker}: {warning_text}")
                log.warning(f"{self.tracker}: Upload should be in drafts — check {draft_url}")
                return False
            if response.status_code >= 400:
                log.error(f"Unexpected server response: {response.status_code} — {response.text}")
                return False
            torrent_url = str(response.url)
            await self._download_and_finalize(torrent_path, torrent_url, task_id)
            return True
        except Exception as exc:
            log.error(f"Upload error: {exc}")
            return False
