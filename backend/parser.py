import hashlib
import html
import re
from collections import defaultdict
from difflib import SequenceMatcher

QUALITY_RE = re.compile(
    r"(?<!\w)(2160p?|1440p?|1080p?|720p?|576p?|480p?|360p?|4320p?|8k|4k|2k)(?!\w)",
    re.I,
)
SE_RE = re.compile(
    r"(?<!\w)s(?:eason)?\s*0*(\d{1,3})\s*[-_. ]?e(?:p(?:isode)?)?\s*0*(\d{1,4})(?!\w)",
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
    r"Atmos|ESubS?|NF|AMZN|DSNP|MAX|iTunes|PROPER|REPACK|UNCUT|REMUX|WEB|HQ|FHD|UHD|FULLHD|"
    r"MKV|MP4|AVI|MOV|TS|10bit|HDR10(?:\+)?|DV|DOLBY(?:\s+VISION)?)(?!\w)",
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
BRACKET_RE = re.compile(r"\[[^\]]*\]|\([^)]*\)")
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


def _is_generic_filename(name):
    value = re.sub(r"\s+", " ", str(name or "").strip().casefold())
    value = EXT_RE.sub("", value)
    return (
        not value
        or bool(re.fullmatch(r"(?:file|video|movie|document|media)[ _.-]*\d*", value))
        or bool(re.fullmatch(r"(?:vid|file|document)[_-]?[a-z0-9]{4,}", value))
    )


def parse_doc(doc):
    name = str(doc.get("file_name") or "").strip()
    caption = str(doc.get("caption") or "")
    source = _source_text(doc)

    se = SE_RE.search(source)
    season = int(se.group(1)) if se else None
    episode = int(se.group(2)) if se else None
    if season is None:
        sm = SEASON_RE.search(source)
        season = int(sm.group(1)) if sm else None
    if episode is None:
        em = EP_RE.search(source)
        episode = int(em.group(1)) if em else None

    qm = QUALITY_RE.search(source)
    ym = YEAR_RE.search(source)
    languages = _extract_languages(source)
    audio = _extract_audio(source)
    source_match = SOURCE_RE.search(source)
    source_name = re.sub(r"[-. ]+", "-", source_match.group(1).strip()).upper() if source_match else "Unknown"
    source_name = {"WEB-DL": "WEB-DL", "WEBRIP": "WEBRip", "BLURAY": "BluRay", "BRRIP": "BRRip", "BDRIP": "BDRip", "HDRIP": "HDRip", "HDTV": "HDTV", "DVDRIP": "DVDRip", "HDTC": "HDTC", "HDTS": "HDTS", "WEB-CAM": "WEB-CAM", "CAMRIP": "CAMRip", "HDCAM": "HDCAM", "CAM": "CAM", "PREDB": "PreDB", "PRE-DVD": "Pre-DVD", "WEB": "WEB", "REMUX": "REMUX"}.get(source_name, source_name)
    # Filename/caption language tags are treated as spoken-audio languages by
    # default. Subtitle languages are only inferred when the source explicitly
    # marks them as subtitles (sub/subs/subbed/esub). Actual embedded tracks are
    # discovered lazily by the streaming layer and can override this metadata.
    subtitle_marked = bool(re.search(r"(?<!\w)(?:sub|subs|subbed|esub|subtitle|subtitles)(?!\w)", source, re.I))
    audio_languages = list(languages) if not subtitle_marked else []
    subtitle_languages = list(languages) if subtitle_marked else []
    language = " + ".join(languages) if languages else "Unknown"

    title_source = caption if _is_generic_filename(name) and caption.strip() else name or caption

    file_id = doc.get("_id")
    if file_id is None:
        file_id = doc.get("file_id", "")

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
        "quality": (
            (lambda q: q if q.lower().endswith("p") or q.lower() in {"8k", "4k", "2k"} else q + "P")(qm.group(1).upper())
            if qm else "Auto"
        ),
        "source": source_name,
        "language": language,  # legacy field kept for old clients
        "languages": languages or ["Unknown"],  # legacy field
        "audio_languages": audio_languages,
        "subtitle_languages": subtitle_languages,
        "audio": audio,
        "codec": _extract_codec(source),
        "year": int(ym.group(1)) if ym else None,
        "poster": extract_poster(caption),
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
    """Normalize a filename to a display/search title without destroying title numbers."""
    s = str(value or "").strip()
    s = EXT_RE.sub("", s)
    original = s
    s = _remove_release_brackets(s)
    s = SE_RE.sub(" ", s)
    s = SEASON_RE.sub(" ", s)
    s = EP_RE.sub(" ", s)
    s = QUALITY_RE.sub(" ", s)
    s = TECH_RE.sub(" ", s)
    s = AUDIO_RE.sub(" ", s)

    # Year is release metadata when it is at the end or follows another title token.
    # Keep a numeric-only title such as "1917" intact.
    year_matches = list(YEAR_RE.finditer(s))
    for match in reversed(year_matches):
        if match.end() == len(s.strip()) or match.start() > 0:
            s = s[:match.start()] + " " + s[match.end():]
            break

    for lang in LANGUAGE_CODES:
        s = re.sub(rf"(?<!\w){re.escape(lang)}(?!\w)", " ", s, flags=re.I)

    s = re.sub(r"(?<!\w)(?:dubbed|subbed|subs|full\s*movie)(?!\w)", " ", s, flags=re.I)
    s = SEPARATORS_RE.sub(" ", s)
    s = re.sub(r"[-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -_.")
    if not s:
        s = original
    return s or "Untitled"


def normalize_query(query):
    """Split a human search into independent metadata constraints.

    Search is intentionally decomposed before touching MongoDB: title, year,
    season/episode, language, quality and source are matched independently
    against parsed media records. The original filename/caption wording does
    not need to equal the user's query.
    """
    value = str(query or "").strip()
    se = SE_RE.search(value)
    season = int(se.group(1)) if se else None
    episode = int(se.group(2)) if se else None
    if season is None:
        sm = re.search(r"(?<!\w)(?:season|s)\s*0*(\d{1,3})(?!\w)", value, re.I)
        if sm:
            season = int(sm.group(1))
    if episode is None:
        em = re.search(r"(?<!\w)(?:episode|ep)\s*0*(\d{1,4})(?!\w)", value, re.I)
        if em:
            episode = int(em.group(1))

    year_match = YEAR_RE.search(value)
    year = int(year_match.group(1)) if year_match else None

    quality_match = QUALITY_RE.search(value)
    quality = quality_match.group(1) if quality_match else None

    source = None
    source_match = SOURCE_RE.search(value)
    if source_match:
        source = source_match.group(1)

    languages = []
    language = None
    low = value.casefold()
    for key, label in sorted(LANGUAGE_CODES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"(?<!\w){re.escape(key)}(?!\w)", low) and label not in languages:
            languages.append(label)
    if languages:
        language = languages[0]

    title_text = value
    if se:
        title_text = title_text.replace(se.group(0), " ")
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
    title_text = re.sub(r"\s+", " ", title_text).strip()
    return {
        "title": clean_title(title_text),
        "season": season,
        "episode": episode,
        "year": year,
        "language": language,
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

    def add(self, doc):
        parsed = parse_doc(doc)
        if not parsed["file_id"]:
            return
        # Prefer an exact title+year identity. A yearless file may join an existing
        # title only when that normalized title has exactly one known release year;
        # when multiple years exist, keeping the yearless item separate is safer than
        # silently mixing different movies.
        base = (parsed["type"], normalize_for_search(parsed["title"]))
        if parsed["year"]:
            title_id = stable_id(parsed["title"], parsed["type"], parsed["year"])
        else:
            candidates = [
                tid for tid, item in self.titles.items()
                if (item["type"], normalize_for_search(item["title"])) == base
                and item.get("year")
            ]
            if len(candidates) == 1:
                title_id = candidates[0]
            else:
                title_id = stable_id(parsed["title"], parsed["type"], None)

        if title_id not in self.titles:
            self.titles[title_id] = {
                "id": title_id,
                "title": parsed["title"],
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
        if parsed["year"]:
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
                "file_id", "file_ref", "file_name", "file_size", "file_type", "mime_type",
                "quality", "source", "language", "languages", "audio_languages", "subtitle_languages", "audio", "codec", "caption", "poster", "season", "episode", "year",
            )
        }

        if parsed["type"] == "series" and parsed["season"] is not None:
            title["seasons"][parsed["season"]][parsed["episode"] or 0].append(variant)
        else:
            title["variants"].append(variant)

    def finish(self):
        result = []
        for title_id in self.order:
            title = self.titles[title_id]
            title["years"] = sorted(title["years"])
            title["audio_languages"] = sorted(title["audio_languages"], key=str.casefold)
            title["subtitle_languages"] = sorted(title["subtitle_languages"], key=str.casefold)
            title["seasons"] = [
                {
                    "season": int(season),
                    "episodes": [
                        {
                            "episode": int(episode),
                            "variants": sorted(
                                variants,
                                key=lambda item: (
                                    quality_key(item["quality"]), item["language"].casefold(), item["file_name"].casefold()
                                ),
                            ),
                        }
                        for episode, variants in sorted(episodes.items())
                    ],
                }
                for season, episodes in sorted(title["seasons"].items())
            ]
            title["variants"] = sorted(
                title["variants"],
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

