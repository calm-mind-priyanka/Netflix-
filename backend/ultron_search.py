"""Ultron-style search engine for the OTT website.

Design goals taken from the supplied AutoFilter/Ultron source:
- normal bounded Mongo search first;
- local fuzzy correction second;
- one short IMDb/Cinemagoer correction only when normal/local search fails;
- never invent a result: an external candidate is accepted only after the
  real AutoFilter Mongo collection contains matching media;
- season/episode tokens are navigation context, not catalog identity;
- one logical movie/series result groups its real assets by exact file_id;
- specific season/episode/language/quality searches are verified against real
  stored media before a result card is returned;
- single-flight + bounded concurrency + short TTL cache for Koyeb Free.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import OrderedDict
from difflib import SequenceMatcher

from .database import (
    search_media,
    fuzzy_search_media,
    search_media_with_filters,
)
from .parser import (
    _CatalogBuilder,
    normalize_query,
    normalize_for_search,
    parse_doc,
    search_title_score,
)
from .web_store import save_catalog_identity

LOGGER = logging.getLogger("vyra.ultron_search")

IGNORE_WORDS = [
    "movies", "movie", "episode", "episodes", "south indian", "south indian movie",
    "south movie", "web-series", "web series", "webseries", "hindi me bhejo", "ful",
    "kro", "jaldi", "audio", "language", "mkv", "mp4", "web", "series", "hollywood",
    "all", "bollywood", "south", "hd", "karo", "upload", "bhejo", "fullepisode",
    "please", "plz", "send", "link", "dabbed", "dubbed", "season",
]


class UltronSearchEngine:
    def __init__(self):
        self.cache = OrderedDict()
        self.inflight = {}
        self.semaphore = asyncio.Semaphore(3)
        self.imdb = None
        self.imdb_lock = asyncio.Lock()

    @staticmethod
    def prepare_query(query: str) -> str:
        value = str(query or "").strip().lower()

        words = sorted(
            (w for w in IGNORE_WORDS if w),
            key=len,
            reverse=True,
        )

        if words:
            value = re.sub(
                r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b",
                " ",
                value,
                flags=re.I,
            )

        value = value.replace("-", " ").replace(":", "").replace("'", "")

        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _key(query):
        return re.sub(
            r"\s+",
            " ",
            str(query or "").strip(),
        ).casefold()

    async def _normal_candidates(self, query, limit):
        prepared = self.prepare_query(query)

        if not prepared:
            return []

        parsed = normalize_query(prepared)
        title = parsed.get("title") or prepared
        year = parsed.get("year")

        base = title + (f" {year}" if year else "")

        async with self.semaphore:
            docs = await search_media(
                base,
                limit=limit,
            )

            if not docs and base.casefold() != title.casefold():
                docs = await search_media(
                    title,
                    limit=limit,
                )

        return docs

    async def _local_correction(self, query, limit):
        prepared = self.prepare_query(query)
        parsed = normalize_query(prepared)
        title = parsed.get("title") or prepared

        try:
            async with self.semaphore:
                docs = await asyncio.wait_for(
                    fuzzy_search_media(
                        title,
                        limit=min(40, limit),
                    ),
                    timeout=0.8,
                )

            if docs:
                parsed_docs = [parse_doc(x) for x in docs]

                parsed_docs = [
                    x
                    for x in parsed_docs
                    if x.get("file_id") and not x.get("_parse_error")
                ]

                if not parsed_docs:
                    return [], None

                best = max(
                    parsed_docs,
                    key=lambda x: search_title_score(
                        x.get("title", ""),
                        title,
                    ),
                )

                score = search_title_score(
                    best.get("title", ""),
                    title,
                )

                if score >= (
                    0.72
                    if len(normalize_for_search(title)) > 4
                    else 0.90
                ):
                    return docs, best.get("title")

        except Exception as exc:
            LOGGER.debug(
                "Local Ultron fuzzy fallback skipped: %s",
                exc,
            )

        return [], None

    async def _imdb_correct(self, query, limit):
        """One bounded IMDb/Cinemagoer lookup, only after local search fails."""
        try:
            async with self.imdb_lock:
                if self.imdb is None:
                    try:
                        from imdb import Cinemagoer

                        self.imdb = Cinemagoer()

                    except Exception:
                        try:
                            import imdb as imdb_module

                            self.imdb = imdb_module.IMDb()

                        except Exception as exc:
                            LOGGER.debug(
                                "Cinemagoer/IMDb unavailable: %s",
                                exc,
                            )
                            return [], None

            prepared = self.prepare_query(query)
            parsed = normalize_query(prepared)

            q = parsed.get("title") or prepared

            if not q:
                return [], None

            results = await asyncio.wait_for(
                asyncio.to_thread(
                    self.imdb.search_movie,
                    q,
                ),
                timeout=1.5,
            )

            candidates = list(results or [])[:20]

            if not candidates:
                return [], None

            target = normalize_for_search(q)
            scored = []

            for movie in candidates:
                title = str(
                    getattr(
                        movie,
                        "get",
                        lambda *_: "",
                    )("title")
                    or getattr(movie, "title", "")
                    or ""
                )

                if not title:
                    continue

                scored.append(
                    (
                        SequenceMatcher(
                            None,
                            target,
                            normalize_for_search(title),
                        ).ratio(),
                        title,
                    )
                )

            scored.sort(reverse=True)

            for _, title in scored[:8]:
                try:
                    docs = await asyncio.wait_for(
                        search_media(
                            title,
                            limit=limit,
                        ),
                        timeout=0.8,
                    )

                except Exception:
                    docs = []

                if docs:
                    return docs, title

        except Exception as exc:
            LOGGER.debug(
                "IMDb correction skipped/timeout: %s",
                exc,
            )

        return [], None

    async def _has_real_requested_variant(self, item, parsed):
        """Verify a specific search request against real stored media records.

        The initial Ultron candidate list is intentionally bounded and can
        contain a title even when the requested season/episode is absent.

        For specific searches such as:

            Bigg Boss S20E15
            Reacher S04E07
            Reacher S04
            Reacher Hindi 720p

        the requested combination must exist in the real Auto Filter records
        before the logical title is returned as a search result.

        Plain title searches remain unchanged.
        """

        has_specific_filter = any(
            (
                parsed.get("season") is not None,
                parsed.get("episode") is not None,
                parsed.get("language"),
                parsed.get("quality"),
                parsed.get("source"),
            )
        )

        if not has_specific_filter:
            return True

        title = str(
            item.get("title") or ""
        ).strip()

        if not title:
            return False

        try:
            async with self.semaphore:
                matches = await search_media_with_filters(
                    title,
                    season=parsed.get("season"),
                    episode=parsed.get("episode"),
                    language=parsed.get("language"),
                    quality=parsed.get("quality"),
                    limit=1500,
                )

            return bool(matches)

        except Exception as exc:
            # Do not manufacture a "not found" result when Mongo itself
            # temporarily fails. Preserve the existing search behavior and
            # make the verification failure visible in the server logs.
            LOGGER.warning(
                "Specific search verification failed for %r: %s",
                title,
                type(exc).__name__,
            )

            return True

    @staticmethod
    def _build_groups(docs):
        builder = _CatalogBuilder()

        for doc in docs:
            try:
                builder.add(doc)

            except Exception:
                LOGGER.exception(
                    "Catalog parse failure"
                )

        return builder.finish()

    async def search(
        self,
        query,
        *,
        candidate_limit=120,
        max_results=10,
        fuzzy=True,
        external_correction=True,
    ):
        key = self._key(query)
        now = time.time()

        cached = self.cache.get(key)

        if cached and now - cached[0] < 30:
            self.cache.move_to_end(key)
            return cached[1], True

        task = self.inflight.get(key)

        if task is not None:
            return await task, False

        async def work():
            docs = await self._normal_candidates(
                query,
                candidate_limit,
            )

            correction = None

            if not docs and fuzzy:
                docs, correction = await self._local_correction(
                    query,
                    candidate_limit,
                )

            if not docs and external_correction:
                docs, correction = await self._imdb_correct(
                    query,
                    candidate_limit,
                )

            if not docs:
                return []

            groups = self._build_groups(docs)

            prepared = self.prepare_query(query)
            parsed = normalize_query(prepared)

            search_title = (
                parsed.get("title")
                or correction
                or prepared
            )

            wanted_year = parsed.get("year")

            ranked = []

            for item in groups:
                score = search_title_score(
                    item.get("title", ""),
                    search_title,
                )

                if (
                    normalize_for_search(item.get("title"))
                    == normalize_for_search(search_title)
                ):
                    score += 0.25

                if (
                    wanted_year is not None
                    and item.get("year") not in (None, wanted_year)
                ):
                    score -= 0.25

                if parsed.get("season") is not None:
                    has_season = (
                        parsed["season"]
                        in (item.get("available_seasons") or [])
                    )

                    if (
                        item.get("type") == "series"
                        and has_season
                    ):
                        score += 0.25

                    elif item.get("type") == "series":
                        score -= 0.05

                if (
                    parsed.get("episode") is not None
                    and item.get("type") == "series"
                ):
                    episodes = {
                        int(e.get("episode"))
                        for s in item.get("seasons") or []
                        if (
                            parsed.get("season") is None
                            or int(s.get("season"))
                            == int(parsed["season"])
                        )
                        for e in s.get("episodes") or []
                    }

                    if int(parsed["episode"]) in episodes:
                        score += 0.25

                # IMPORTANT:
                #
                # A title match alone is not enough for a specific search.
                #
                # Previously:
                #
                #   Bigg Boss S20E15
                #       -> "Bigg Boss" matched
                #       -> result card was returned
                #       -> filter page showed 0 matching files
                #
                # Now the requested season/episode/language/quality is
                # checked against the actual stored Auto Filter records
                # before the result is returned.
                if not await self._has_real_requested_variant(
                    item,
                    parsed,
                ):
                    continue

                item["search_season"] = parsed.get("season")
                item["search_episode"] = parsed.get("episode")
                item["search_language"] = parsed.get("language")
                item["search_quality"] = parsed.get("quality")

                ranked.append(
                    (
                        score,
                        item,
                    )
                )

            ranked.sort(
                key=lambda pair: (
                    -pair[0],
                    pair[1].get(
                        "title",
                        "",
                    ).casefold(),
                )
            )

            result = []
            seen_ids = set()

            for score, item in ranked:
                if score < 0.55:
                    continue

                item = dict(item)

                # Search responses are intentionally lightweight.
                # Do NOT send every season/episode/file variant to the
                # browser. The full logical title is loaded only after
                # the user opens a result.

                item.pop("seasons", None)
                item.pop("variants", None)
                item.pop("assets", None)
                item.pop("episodes", None)
                item.pop("media", None)

                key_id = item.get("id")

                if key_id in seen_ids:
                    continue

                seen_ids.add(key_id)

                try:
                    await save_catalog_identity(item)

                except Exception:
                    LOGGER.debug(
                        "Could not persist catalog identity",
                        exc_info=True,
                    )

                result.append(item)

                if len(result) >= max_results:
                    break

            return result

        task = asyncio.create_task(work())
        self.inflight[key] = task

        try:
            result = await task

        finally:
            self.inflight.pop(key, None)

        if result:
            self.cache[key] = (
                time.time(),
                result,
            )

            self.cache.move_to_end(key)

            while len(self.cache) > 256:
                self.cache.popitem(last=False)

        return result, False
