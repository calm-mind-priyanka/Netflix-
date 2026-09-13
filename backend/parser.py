import hashlib
import html
import re
from collections import defaultdict

# These patterns intentionally contain single backslashes. Raw strings must
# pass the regex escapes through unchanged.
QUALITY_RE = re.compile(
    r"(?<!\w)(2160p|1440p|1080p|720p|576p|480p|360p|4320p|4k|8k|2k)(?!\w)",
    re.I,
)
SE_RE = re.compile(
    r"(?<!\w)s(?:eason)?\s*0*(\d{1,3})\s*[-_. ]?e(?:p(?:isode)?)?\s*0*(\d{1,4})(?!\w)",
    re.I,
)
SEASON_RE = re.compile(r"(?<!\w)(?:season|s)\s*0*(\d{1,3})(?!\w)", re.I)
EP_RE = re.compile(r"(?<!\w)(?:episode|ep|e)\s*0*(\d{1,4})(?!\w)", re.I)
YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
EXT_RE = re.compile(r"\.(?:mkv|mp4|avi|mov|webm|m4v|ts|mpeg|mpg)$", re.I)

LANGUAGES = [
    "dual audio",
    "multi audio",
    "hindi",
    "english",
    "bengali",
    "bangla",
    "tamil",
    "telugu",
    "malayalam",
    "kannada",
    "marathi",
    "punjabi",
    "gujarati",
    "bhojpuri",
    "korean",
    "spanish",
    "french",
    "german",
    "chinese",
    "japanese",
    "urdu",
]

TECH_RE = re.compile(
    r"(?<!\w)(?:WEB[- .]?DL|WEB[- .]?Rip|WEBRip|BluRay|BRRip|BDRip|HDRip|HDTV|DVDRip|CAMRip|CAM|HDCAM|HEVC|x264|x265|H264|H265|10bit|AAC|DDP?|DD\+|Atmos|ESub|ESubs|NF|AMZN|MAX|DSNP|PROPER|REPACK|UNCUT|REMUX|HQ|HD|FHD|UHD|FULLHD|MKV|MP4|AVI|MOV)(?!\w)",
    re.I,
)

BRACKET_RE = re.compile(r"\[[^\]]*\]|\([^)]*\)")
SEPARATORS_RE = re.compile(r"[._]+")


def _source_text(doc):
    name = str(doc.get("file_name") or "")
    caption = html.unescape(str(doc.get("caption") or ""))
    return f"{name} {caption}".strip()


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
    low = source.casefold()

    language = "Unknown"
    for lang in sorted(LANGUAGES, key=len, reverse=True):
        if re.search(rf"(?<!\w){re.escape(lang)}(?!\w)", low):
            language = lang.title()
            break

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
        "title": clean_title(name),
        "type": "series" if season is not None or episode is not None else "movie",
        "season": season,
        "episode": episode,
        "quality": qm.group(1).upper() if qm else "Auto",
        "language": language,
        "year": int(ym.group(1)) if ym else None,
        "poster": extract_poster(caption),
    }


def clean_title(value):
    """Convert a filename into a readable title without losing title numbers."""
    s = str(value or "").strip()
    s = EXT_RE.sub("", s)
    s = BRACKET_RE.sub(" ", s)
    s = SE_RE.sub(" ", s)
    s = SEASON_RE.sub(" ", s)
    s = EP_RE.sub(" ", s)
    s = QUALITY_RE.sub(" ", s)
    s = TECH_RE.sub(" ", s)
    s = YEAR_RE.sub(" ", s)

    for lang in LANGUAGES:
        s = re.sub(rf"(?<!\w){re.escape(lang)}(?!\w)", " ", s, flags=re.I)

    # Release/channel tags commonly found in filenames.
    s = re.sub(
        r"(?<!\w)(?:480p|576p|720p|1080p|1440p|2160p|4k|8k|hdr|proper|repack|"
        r"uncut|remux|dual|audio|dubbed|subbed|subs|full\s*movie)(?!\w)",
        " ",
        s,
        flags=re.I,
    )
    s = SEPARATORS_RE.sub(" ", s)
    s = re.sub(r"[-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -_.")
    return s or "Untitled"


def extract_poster(caption):
    match = re.search(
        r'https?://[^\s<>"\']+\.(?:jpg|jpeg|png|webp)(?:\?[^\s<>"\']*)?',
        caption or "",
        re.I,
    )
    return match.group(0) if match else None


def stable_id(title, kind):
    return hashlib.sha256(
        f"{kind}:{title.casefold()}".encode("utf-8")
    ).hexdigest()[:20]


def quality_key(value):
    match = re.match(r"(\d+)", str(value or ""))
    return int(match.group(1)) if match else 9999


def normalize(docs):
    titles = {}

    for doc in docs:
        parsed = parse_doc(doc)
        title_id = stable_id(parsed["title"], parsed["type"])

        title = titles.setdefault(
            title_id,
            {
                "id": title_id,
                "title": parsed["title"],
                "type": parsed["type"],
                "year": parsed["year"],
                "poster": parsed["poster"],
                "description": None,
                "genre": None,
                "rating": None,
                "seasons": defaultdict(lambda: defaultdict(list)),
                "variants": [],
            },
        )

        if not title["year"] and parsed["year"]:
            title["year"] = parsed["year"]
        if not title["poster"] and parsed["poster"]:
            title["poster"] = parsed["poster"]

        variant = {
            key: parsed[key]
            for key in (
                "file_id",
                "file_ref",
                "file_name",
                "file_size",
                "file_type",
                "mime_type",
                "quality",
                "language",
                "caption",
            )
        }

        if parsed["type"] == "series" and parsed["season"] is not None:
            title["seasons"][parsed["season"]][parsed["episode"] or 0].append(variant)
        else:
            title["variants"].append(variant)

    result = []
    for title in titles.values():
        title["seasons"] = [
            {
                "season": int(season),
                "episodes": [
                    {
                        "episode": int(episode),
                        "variants": sorted(
                            variants,
                            key=lambda item: (
                                item["language"],
                                quality_key(item["quality"]),
                                item["file_name"].casefold(),
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
            key=lambda item: (
                item["language"],
                quality_key(item["quality"]),
                item["file_name"].casefold(),
            ),
        )
        result.append(title)

    return sorted(result, key=lambda item: item["title"].casefold())
