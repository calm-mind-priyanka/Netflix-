import hashlib
import html
import re
from collections import defaultdict
from collections.abc import Mapping
from difflib import SequenceMatcher

QUALITY_RE = re.compile(
    r"(?<!\w)(2160p?|1440p?|1080p?|720p?|576p?|480p?|360p?|4320p?|8k|4k|2k)(?!\w)",
    re.I,
)
RELEASE_QUALITY_FALLBACK_RE = re.compile(
    r"(?<!\w)(?:\d{3,4})p(?!\w)",
    re.I,
)
SE_RE = re.compile(
    r"(?<!\w)s(?:eason)?\s*0*(\d{1,3})\s*[-_. ]?e(?:p(?:isode)?)?\s*0*(\d{1,4})(?!\w)",
    re.I,
)
ONE_X_ONE_RE = re.compile(
    r"(?<!\w)(\d{1,3})\s*x\s*(\d{1,4})(?!\w)",
    re.I,
)
SEASON_RE = re.compile(r"(?<!\w)(?:season|s)\s*0*(\d{1,3})(?!\w)", re.I)
EP_RE = re.compile(r"(?<!\w)(?:episode|ep)\s*0*(\d{1,4})(?!\w)", re.I)
YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
EXT_RE = re.compile(r"\.(?:mkv|mp4|avi|mov|webm|m4v|ts|mpeg|mpg)$", re.I)

LANGUAGES = [
    "dual audio", "multi audio", "hindi", "english", "bengali", "bangla",
    "tamil", "telugu", "malayalam", "kannada", "marathi", "punjabi",
    "gujarati", "bhojpuri", "korean", "spanish", "french", "german",
    "chinese", "japanese", "urdu",
]
LANGUAGE_CODES = {
    "hin": "Hindi", "hindi": "Hindi", "eng": "English", "english": "English",
    "tam": "Tamil", "tamil": "Tamil", "tel": "Telugu", "telugu": "Telugu",
    "mal": "Malayalam", "malayalam": "Malayalam", "kan": "Kannada", "kannada": "Kannada",
    "ben": "Bengali", "bengali": "Bengali", "bangla": "Bangla", "mar": "Marathi",
    "marathi": "Marathi", "pun": "Punjabi", "punjabi": "Punjabi", "guj": "Gujarati",
    "gujarati": "Gujarati", "bho": "Bhojpuri", "bhojpuri": "Bhojpuri", "kor": "Korean",
    "korean": "Korean", "spa": "Spanish", "spanish": "Spanish", "fra": "French",
    "french": "French", "ger": "German", "german": "German", "chi": "Chinese",
    "chinese": "Chinese", "jpn": "Japanese", "japanese": "Japanese", "urd": "Urdu", "urdu": "Urdu",
}

# Release/technical tags are metadata, not title words. The patterns deliberately
# cover forms such as AAC2.0 and H.264 so their numeric suffixes cannot leak into titles.
TECH_RE = re.compile(
    r"(?<!\w)(?:WEB[- .]?(?:DL|Rip)|BluRay|BRRip|BDRip|HDRip|HDTV|DVDRip|CAMRip|HDCAM|"
    r"HEVC|AVC|x264|x265|H[ .-]?264|H[ .-]?265|10\s*bit|8\s*bit|"
    r"AAC(?:\s*[0-9]+(?:(?:\s*[.]\s*|\s+)[0-9]+)?)?|AC3|EAC3|DDP?(?:\s*[0-9]+(?:(?:\s*[.]\s*|\s+)[0-9]+)?)?|DD\+|DTS(?:[- .]?HD)?|"
    r"Atmos|HDR10Plus|ESubS?|NF|AMZN|DSNP|MAX|iTunes|PROPER|REPACK|UNCUT|REMUX|WEB|HQ|FHD|UHD|FULLHD|"
    r"AV1|DS4K|HDCMKV|SAONMKV|MKV|MP4|AVI|MOV|TS|10bit|HDR10(?:\+)?|DV|DOLBY(?:\s+VISION)?)(?!\w)",
    re.I,
)
SOURCE_RE = re.compile(
    r"(?<!\w)(WEB[- .]?DL|WEBRip|BluRay|BRRip|BDRip|HDRip|HDTV|DVDRip|HDTC|HDTS|WEB[- .]?CAM|CAMRip|HDCAM|CAM|PreDB|Pre[- .]?DVD|WEB|REMUX)(?!\w)",
    re.I,
)
AUDIO_RE = re.compile(
    r"(?<!\w)(dual\s+audio|multi\s+audio|original\s+audio|AAC(?:\s*[0-9]+(?:(?:\s*[.]\s*|\s+)[0-9]+)?)?|"
    r"AC3|EAC3|DDP?(?:\s*[0-9]+(?:(?:\s*[.]\s*|\s+)[0-9]+)?)?|DD\+|DTS(?:[- .]?HD)?|Atmos)(?!\w)",
    re.I,
)
BRACKET_RE = re.compile(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}")
SEPARATORS_RE = re.compile(r"[._]+")


def _source_text(doc):
    name = str(doc.get("file_name") or "")
    caption = html.unescape(str(doc.get("caption") or ""))
    return f"{name} {caption}".strip()


def _extract_languages(source):
    low = source.casefold()
    found = []
    for key, label in sorted(LANGUAGE_CODES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"(?<!\w){re.escape(key)}(?!\w)", low) and label not in found:
            found.append(label)
    return found


def _extract_audio(source):
    matches = []
    for match in AUDIO_RE.finditer(source or ""):
        value = re.sub(r"\s+", " ", match.group(1).strip()).upper()
        if value in {"DUAL AUDIO", "MULTI AUDIO", "ORIGINAL AUDIO"}:
            value = value.title()
        elif value.startswith("AAC"):
            numeric = re.search(r"AAC\s*(\d+)\s*(?:[.]\s*)?(\d+)?", match.group(1), re.I)
            if numeric and numeric.group(2):
                value = f"AAC{numeric.group(1)}.{numeric.group(2)}"
            else:
                value = re.sub(r"\s+", "", value)
        elif value.startswith("EAC3"):
            value = "E-AC-3"
        if value not in matches:
            matches.append(value)
    return matches or ["Unknown"]


def _extract_codec(source):
    low = source or ""
    if re.search(r"(?<!\w)(?:HEVC|H[ .-]?265|x265)(?!\w)", low, re.I):
        return "H.265/HEVC"
    if re.search(r"(?<!\w)(?:AVC|H[ .-]?264|x264)(?!\w)", low, re.I):
        return "H.264/AVC"
    return "Unknown"


def _extract_dynamic_range(source):
    value = str(source or "")
    if re.search(r"(?<!\w)dolby(?:\s+vision)?(?!\w)|(?<!\w)dv(?!\w)", value, re.I):
        return "Dolby Vision"
    if re.search(r"(?<!\w)hdr10(?:\+)?(?!\w)", value, re.I):
        return "HDR10+" if re.search(r"hdr10\s*\+", value, re.I) else "HDR10"
    if re.search(r"(?<!\w)hdr(?!\w)", value, re.I):
        return "HDR"
    if re.search(r"(?<!\w)sdr(?!\w)", value, re.I):
        return "SDR"
    return "Unknown"


def _extract_audio_codec(audio_values):
    for value in audio_values or []:
        low = str(value).casefold()
        if low.startswith("aac"):
            return value
        if low.startswith(("ac3", "eac3", "e-ac-3", "dd", "ddp", "dd+", "dts", "dts-hd", "atmos")):
            return value
    return "Unknown"


def _is_generic_filename(name):
    value = re.sub(r"\s+", " ", str(name or "").strip().casefold())
    value = EXT_RE.sub("", value)
    return (
        not value
        or bool(re.fullmatch(r"(?:file|video|movie|document|media)[ _.-]*\d*", value))
        or bool(re.fullmatch(r"(?:vid|file|document)[_-]?[a-z0-9]{4,}", value))
        or bool(re.fullmatch(r"[a-f0-9]{8,}", value))
        or bool(re.fullmatch(r"\d{5,}", value))
        or len(value) <= 3
    )


def _first_season_episode(source):
    """Return the first real season/episode pair from release text."""
    se = SE_RE.search(source or "")
    if se:
        return int(se.group(1)), int(se.group(2))
    one_x_one = ONE_X_ONE_RE.search(source or "")
    if one_x_one:
        return int(one_x_one.group(1)), int(one_x_one.group(2))
    return None, None


def _canonical_audio_type(source):
    value = str(source or "")
    if re.search(r"(?<!\w)dual\s+audio(?!\w)", value, re.I):
        return "Dual Audio"
    if re.search(r"(?<!\w)multi\s+audio(?!\w)", value, re.I):
        return "Multi Audio"
    if re.search(r"(?<!\w)original\s+audio(?!\w)", value, re.I):
        return "Original Audio"
    return "Normal"


def parse_doc(doc):
    """Decode one real Auto Filter record without ever mutating it.

    Filename and caption are both metadata sources. A generic filename is
    replaced by the caption for title decoding, while the combined text is
    still used to find technical tags, languages, season/episode and poster.
    A malformed document returns a safe, non-playable-free record or is later
    skipped by the catalog builder; it must never crash a whole search.
    """
    try:
        if not isinstance(doc, Mapping):
            return {"file_id": "", "_parse_error": "invalid document type"}
        name = str(doc.get("file_name") or "").strip()
        caption = html.unescape(str(doc.get("caption") or ""))
        source = _source_text({"file_name": name, "caption": caption})

        season, episode = _first_season_episode(source)
        if season is None:
            sm = SEASON_RE.search(source)
            season = int(sm.group(1)) if sm else None
        if episode is None:
            em = EP_RE.search(source)
            episode = int(em.group(1)) if em else None

        qm = QUALITY_RE.search(source) or RELEASE_QUALITY_FALLBACK_RE.search(source)
        year_matches = list(YEAR_RE.finditer(source))
        ym = year_matches[-1] if year_matches else None
        languages = _extract_languages(source)
        audio = _extract_audio(source)
        source_match = SOURCE_RE.search(source)
        source_name = re.sub(r"[-. ]+", "-", source_match.group(1).strip()).upper() if source_match else "Unknown"
        source_name = {
            "WEB-DL": "WEB-DL", "WEBRIP": "WEBRip", "BLURAY": "BluRay",
            "BRRIP": "BRRip", "BDRIP": "BDRip", "HDRIP": "HDRip",
            "HDTV": "HDTV", "DVDRIP": "DVDRip", "HDTC": "HDTC",
            "HDTS": "HDTS", "WEB-CAM": "WEB-CAM", "CAMRIP": "CAMRip",
            "HDCAM": "HDCAM", "CAM": "CAM", "PREDB": "PreDB",
            "PRE-DVD": "Pre-DVD", "WEB": "WEB", "REMUX": "REMUX",
        }.get(source_name, source_name)

        subtitle_marked = bool(
            re.search(r"(?<!\w)(?:sub|subs|subbed|esub|subtitle|subtitles)(?!\w)", source, re.I)
        )
        audio_languages = list(languages) if not subtitle_marked else []
        subtitle_languages = list(languages) if subtitle_marked else []
        language = " + ".join(languages) if languages else "Unknown"

        title_source = caption if _is_generic_filename(name) and caption.strip() else name or caption
        file_id = doc.get("_id")
        if file_id is None:
            file_id = doc.get("file_id", "")

        quality = "Auto"
        if qm:
            q = qm.group(1).upper()
            quality = q if q.endswith("P") or q.lower() in {"8k", "4k", "2k"} else q + "P"

        return {
            "file_id": str(file_id),
            "file_ref": str(doc.get("file_ref") or ""),
            "file_name": name,
            "caption": caption,
            "file_size": int(doc.get("file_size") or 0),
            "file_type": doc.get("file_type"),
            "mime_type": doc.get("mime_type"),
            "title": clean_title(title_source),
            "type": "series" if season is not None or episode is not None else "movie",
            "season": season,
            "episode": episode,
            "quality": quality,
            "source": source_name,
            "language": language,
            "languages": languages or ["Unknown"],
            "audio_languages": audio_languages,
            "subtitle_languages": subtitle_languages,
            "audio": audio,
            "audio_type": _canonical_audio_type(source),
            "audio_codec": _extract_audio_codec(audio),
            "codec": _extract_codec(source),
            "dynamic_range": _extract_dynamic_range(source),
            "year": int(ym.group(1)) if ym else None,
            "poster": extract_poster(caption),
            "tmdb_id": doc.get("tmdb_id") or doc.get("tmdbId") or doc.get("tmdb"),
        }
    except Exception as exc:
        # The catalog builder logs/skips this record. Never let one malformed
        # Mongo document abort the complete search request.
        return {
            "file_id": "",
            "file_ref": "",
            "file_name": str(doc.get("file_name") or ""),
            "caption": str(doc.get("caption") or ""),
            "title": "Untitled",
            "type": "movie",
            "season": None,
            "episode": None,
            "quality": "Auto",
            "source": "Unknown",
            "language": "Unknown",
            "languages": ["Unknown"],
            "audio_languages": [],
            "subtitle_languages": [],
            "audio": ["Unknown"],
            "audio_type": "Normal",
            "audio_codec": "Unknown",
            "codec": "Unknown",
            "dynamic_range": "Unknown",
            "year": None,
            "poster": None,
            "_parse_error": f"{type(exc).__name__}: {exc}",
        }

def _remove_release_brackets(text):
    def repl(match):
        value = match.group(0)
        if (
            QUALITY_RE.search(value) or SE_RE.search(value) or SEASON_RE.search(value)
            or YEAR_RE.search(value) or TECH_RE.search(value) or AUDIO_RE.search(value)
            or any(re.search(rf"(?<!\w){re.escape(lang)}(?!\w)", value, re.I) for lang in LANGUAGE_CODES)
        ):
            return " "
        return value
    return BRACKET_RE.sub(repl, text)


def clean_title(value):
    """Normalize Telegram/AutoFilter filenames into a logical title.

    This keeps the existing Netflix metadata extraction but also removes
    Telegram channel/mention noise and normalizes the punctuation used by
    Devil AutoFilter filenames.
    """
    s = str(value or "").strip()
    if not s:
        return "Untitled"

    s = re.sub(r"https?://\S+", " ", s, flags=re.I)
    # @Channel/@username is release metadata. A leading # is a tag marker,
    # so remove the marker but preserve the title text after it.
    s = re.sub(r"(?<!\w)@[A-Za-z0-9_]+", " ", s)
    s = re.sub(r"(?<!\w)#(?=\w)", " ", s)

    s = EXT_RE.sub("", s)
    original = s
    s = _remove_release_brackets(s)
    s = SE_RE.sub(" ", s)
    s = ONE_X_ONE_RE.sub(" ", s)
    s = SEASON_RE.sub(" ", s)
    s = EP_RE.sub(" ", s)
    s = QUALITY_RE.sub(" ", s)
    s = TECH_RE.sub(" ", s)
    s = AUDIO_RE.sub(" ", s)
    s = re.sub(r"(?<!\w)\d+(?:[.]\d+)?\s*(?:ch|channels?)(?!\w)", " ", s, flags=re.I)
    s = re.sub(r"(?<!\w)(?:5[.]1|7[.]1)(?!\w)", " ", s, flags=re.I)
    s = re.sub(r"(?<!\w)[a-z0-9]{2,16}(?:mkv)(?!\w)", " ", s, flags=re.I)

    year_matches = list(YEAR_RE.finditer(s))
    for match in reversed(year_matches):
        if match.end() == len(s.strip()) or match.start() > 0:
            s = s[:match.start()] + " " + s[match.end():]
            break

    for lang in LANGUAGE_CODES:
        s = re.sub(rf"(?<!\w){re.escape(lang)}(?!\w)", " ", s, flags=re.I)

    s = re.sub(r"(?<!\w)(?:dubbed|subbed|subs|full\s*movie)(?!\w)", " ", s, flags=re.I)
    # Devil stores _, -, ., + as spaces. Treat the same separators uniformly
    # and discard remaining punctuation/emoji as title separators.
    s = re.sub(r"[._+]+", " ", s)
    s = re.sub(r"[-]+", " ", s)
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip(" -_.+#@")
    if not s:
        s = original
    return s or "Untitled"


def normalize_query(query):
    """Parse search context without turning it into a new catalog identity."""
    value = str(query or "").strip()
    se = SE_RE.search(value)
    season = int(se.group(1)) if se else None
    episode = int(se.group(2)) if se else None
    one_x_one = ONE_X_ONE_RE.search(value)
    if se is None and one_x_one:
        season = int(one_x_one.group(1))
        episode = int(one_x_one.group(2))
    if season is None:
        sm = SEASON_RE.search(value)
        if sm:
            season = int(sm.group(1))
    if episode is None:
        em = EP_RE.search(value)
        if em:
            episode = int(em.group(1))

    year_match = YEAR_RE.search(value)
    year = int(year_match.group(1)) if year_match else None
    quality_match = QUALITY_RE.search(value) or RELEASE_QUALITY_FALLBACK_RE.search(value)
    quality = quality_match.group(1) if quality_match else None
    source_match = SOURCE_RE.search(value)
    source = source_match.group(1) if source_match else None

    languages = []
    low = value.casefold()
    for key, label in sorted(LANGUAGE_CODES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"(?<!\w){re.escape(key)}(?!\w)", low) and label not in languages:
            languages.append(label)

    title_text = value
    for match in (se, one_x_one):
        if match:
            title_text = title_text.replace(match.group(0), " ")
    title_text = re.sub(r"(?<!\w)season\s*0*\d{1,3}(?!\w)", " ", title_text, flags=re.I)
    title_text = re.sub(r"(?<!\w)(?:episode|ep)\s*0*\d{1,4}(?!\w)", " ", title_text, flags=re.I)
    if year_match:
        title_text = title_text.replace(year_match.group(0), " ")
    if quality_match:
        title_text = title_text.replace(quality_match.group(0), " ")
    if source_match:
        title_text = title_text.replace(source_match.group(0), " ")
    for key in LANGUAGE_CODES:
        title_text = re.sub(rf"(?<!\w){re.escape(key)}(?!\w)", " ", title_text, flags=re.I)
    title_text = re.sub(r"(?<!\w)(?:dual|multi)\s+audio(?!\w)", " ", title_text, flags=re.I)
    title_text = re.sub(r"\s+", " ", title_text).strip()
    # A numeric four-digit title such as the movie "1917" is also matched by
    # YEAR_RE. Keep it as the title when removing the year would empty the query.
    if not title_text and year_match and value.strip() == year_match.group(0):
        title_text = year_match.group(0)
    return {
        "title": clean_title(title_text),
        "season": season,
        "episode": episode,
        "year": year,
        "language": languages[0] if languages else None,
        "languages": languages,
        "quality": quality,
        "source": source,
    }

def search_title_score(title, query_title):
    a = normalize_for_search(title)
    b = normalize_for_search(query_title)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if b in a or a in b:
        return 0.88
    ratio = SequenceMatcher(None, a, b).ratio()
    a_tokens, b_tokens = a.split(), b.split()
    if not a_tokens or not b_tokens:
        return ratio
    token_score = sum(max(SequenceMatcher(None, bt, at).ratio() for at in a_tokens) for bt in b_tokens) / len(b_tokens)
    return max(ratio, token_score * 0.96)


def normalize_for_search(value):
    value = re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold())
    return re.sub(r"\s+", " ", value).strip()


def extract_poster(caption):
    match = re.search(r'https?://[^\s<>"\']+\.(?:jpg|jpeg|png|webp)(?:\?[^\s<>"\']*)?', caption or "", re.I)
    return match.group(0) if match else None


def stable_id(title, kind, year=None):
    # Release year is part of the canonical identity. This prevents movies such as
    # Venom (2018) and Venom: Let There Be Carnage (2021), or same-name releases,
    # from being collapsed into one title when their parsed names overlap.
    year_key = str(int(year)) if year else "unknown"
    return hashlib.sha256(
        f"{kind}:{normalize_for_search(title)}:{year_key}".encode("utf-8")
    ).hexdigest()[:20]


def quality_key(value):
    match = re.match(r"(\d+)", str(value or ""))
    return int(match.group(1)) if match else 9999


class _CatalogBuilder:
    def __init__(self):
        self.titles = {}
        self.order = []
        self.seen_files = set()

    def add(self, doc, forced_title=None):
        parsed = parse_doc(doc)
        if forced_title:
            parsed["title"] = str(forced_title).strip() or parsed.get("title")
        if parsed.get("_parse_error"):
            # Keep the bad record from taking down the request, but loggable data
            # is retained by the caller if desired. It has no playable file ID.
            return
        if not parsed["file_id"]:
            return
        if parsed["file_id"] in self.seen_files:
            return
        self.seen_files.add(parsed["file_id"])
        # Prefer an exact title+year identity. A yearless file may join an existing
        # title only when that normalized title has exactly one known release year;
        # when multiple years exist, keeping the yearless item separate is safer than
        # silently mixing different movies.
        base = (parsed.get("type", "movie"), normalize_for_search(parsed.get("title")))
        tmdb_id = str(parsed.get("tmdb_id") or "").strip()
        if tmdb_id:
            title_id = stable_id(f"tmdb:{tmdb_id}", parsed.get("type", "movie"), None)
        elif parsed.get("year"):
            title_id = stable_id(parsed.get("title"), parsed.get("type", "movie"), parsed.get("year"))
        else:
            candidates = [
                tid for tid, item in self.titles.items()
                if (item.get("type", "movie"), normalize_for_search(item.get("title"))) == base
                and item.get("year")
            ]
            if len(candidates) == 1:
                title_id = candidates[0]
            else:
                title_id = stable_id(parsed.get("title"), parsed.get("type", "movie"), None)

        if title_id not in self.titles:
            self.titles[title_id] = {
                "id": title_id,
                "title": parsed["title"],
                "tmdb_id": tmdb_id or None,
                "type": parsed["type"],
                "year": parsed["year"],
                "years": set([parsed["year"]]) if parsed["year"] else set(),
                "poster": parsed["poster"],
                "description": None,
                "genre": None,
                "rating": None,
                "seasons": defaultdict(lambda: defaultdict(list)),
                "variants": [],
                "audio_languages": set(),
                "subtitle_languages": set(),
                "_order": len(self.order),
            }
            self.order.append(title_id)

        title = self.titles[title_id]
        if parsed.get("year"):
            title["years"].add(parsed["year"])
            if not title["year"]:
                title["year"] = parsed["year"]
        if not title["poster"] and parsed["poster"]:
            title["poster"] = parsed["poster"]

        title["audio_languages"].update(parsed.get("audio_languages") or [])
        title["subtitle_languages"].update(parsed.get("subtitle_languages") or [])

        variant = {
            key: parsed[key]
            for key in (
                "file_id", "file_ref", "file_name", "file_size", "file_type", "mime_type", "tmdb_id",
                "quality", "source", "language", "languages", "audio_languages", "subtitle_languages", "audio", "audio_type", "audio_codec", "codec", "dynamic_range", "caption", "poster", "season", "episode", "year",
            )
        }

        if parsed["type"] == "series" and parsed["season"] is not None:
            title["seasons"][parsed["season"]][parsed["episode"] or 0].append(variant)
        else:
            title["variants"].append(variant)

    def finish(self):
        # A yearless release can be safely attached to a same-title release only
        # when exactly one known year exists. This removes order-dependent
        # duplicates without risking a merge of genuinely different movies.
        by_base = defaultdict(list)
        for tid, item in self.titles.items():
            by_base[(item["type"], normalize_for_search(item["title"]))].append(tid)
        aliases = {}
        for base, ids in by_base.items():
            known = [tid for tid in ids if self.titles[tid].get("year")]
            unknown = [tid for tid in ids if not self.titles[tid].get("year")]
            if len(known) == 1 and unknown:
                target = known[0]
                for tid in unknown:
                    aliases[tid] = target
                    source = self.titles[tid]
                    dest = self.titles[target]
                    if not dest.get("poster") and source.get("poster"):
                        dest["poster"] = source["poster"]
                    dest["audio_languages"].update(source.get("audio_languages") or [])
                    dest["subtitle_languages"].update(source.get("subtitle_languages") or [])
                    dest["variants"].extend(source.get("variants") or [])
                    for season, episodes in source.get("seasons", {}).items():
                        for episode, variants in episodes.items():
                            dest["seasons"][season][episode].extend(variants)
        if aliases:
            self.order = [tid for tid in self.order if tid not in aliases]
            for tid in aliases:
                self.titles.pop(tid, None)

        result = []
        for title_id in self.order:
            title = self.titles[title_id]
            title["years"] = sorted(title["years"])
            title["audio_languages"] = sorted(title["audio_languages"], key=str.casefold)
            title["subtitle_languages"] = sorted(title["subtitle_languages"], key=str.casefold)
            seasons_out = []
            for season, episodes in sorted(title["seasons"].items()):
                episode_out = []
                for episode, variants in sorted(episodes.items()):
                    # Episode 0 represents a season-level asset with no episode
                    # number. It is not fabricated into an episode in the UI.
                    if int(episode) == 0:
                        continue
                    unique = {}
                    for variant in variants:
                        unique[variant["file_id"]] = variant
                    episode_out.append({
                        "episode": int(episode),
                        "variants": sorted(
                            unique.values(),
                            key=lambda item: (quality_key(item["quality"]), item["language"].casefold(), item["file_name"].casefold()),
                        ),
                    })
                if episode_out:
                    season_poster = title.get("poster")
                    if not season_poster:
                        for episode_item in episode_out:
                            season_poster = next((v.get("poster") for v in episode_item["variants"] if v.get("poster")), None)
                            if season_poster:
                                break
                    seasons_out.append({
                        "season": int(season),
                        "poster": season_poster,
                        "episodes": episode_out,
                        "episode_numbers": [item["episode"] for item in episode_out],
                    })
            title["seasons"] = seasons_out
            title["available_seasons"] = [item["season"] for item in seasons_out]
            title["variants"] = sorted(
                {v["file_id"]: v for v in title["variants"]}.values(),
                key=lambda item: (quality_key(item["quality"]), item["language"].casefold(), item["file_name"].casefold()),
            )
            title.pop("_order", None)
            result.append(title)
        return result


def normalize(docs):
    """Normalize an in-memory iterable of MongoDB documents."""
    builder = _CatalogBuilder()
    for doc in docs:
        builder.add(doc)
    return builder.finish()


async def normalize_async(docs):
    """Normalize an async MongoDB stream without first materializing all documents."""
    builder = _CatalogBuilder()
    async for doc in docs:
        builder.add(doc)
    return builder.finish()

