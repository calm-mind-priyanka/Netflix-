import re, html, hashlib
from collections import defaultdict

QUALITY_RE = re.compile(r"(?<!\\d)(2160p|1440p|1080p|720p|576p|480p|360p|4k|2k)(?!\\w)", re.I)
SE_RE = re.compile(r"(?:s(?:eason)?\\s*0*(\\d{1,3})\\s*[-_. ]?e(?:p(?:isode)?)?\\s*0*(\\d{1,4}))", re.I)
SEASON_RE = re.compile(r"(?:season|s)\\s*0*(\\d{1,3})", re.I)
EP_RE = re.compile(r"(?:episode|ep|e)\\s*0*(\\d{1,4})", re.I)
YEAR_RE = re.compile(r"\\b(19\\d{2}|20\\d{2})\\b")
LANGUAGES = ["dual audio","multi audio","hindi","english","bengali","bangla","tamil","telugu","malayalam","kannada","marathi","punjabi","gujarati","bhojpuri","korean","spanish","french","german","chinese","japanese","urdu"]
TECH_RE = re.compile(r"\\b(?:WEB[- .]?DL|WEBRip|WEB[- .]?Rip|BluRay|BRRip|HDRip|HDTV|DVDRip|CAM|HDCAM|HEVC|x264|x265|10bit|AAC|DDP?|DD\\+|Atmos|ESub|ESubs|NF|AMZN|MAX|DSNP|PROPER|REPACK|UNCUT|REMUX|HQ|HD|FHD|UHD|MKV|MP4|AVI|MOV)\\b", re.I)

def parse_doc(doc):
    name = str(doc.get("file_name") or "")
    caption = str(doc.get("caption") or "")
    source = f"{name} {html.unescape(caption)}"
    se = SE_RE.search(source)
    season = int(se.group(1)) if se else None
    episode = int(se.group(2)) if se else None
    if season is None:
        m = SEASON_RE.search(source)
        season = int(m.group(1)) if m else None
    if episode is None:
        m = EP_RE.search(source)
        episode = int(m.group(1)) if m else None
    q = QUALITY_RE.search(source)
    ym = YEAR_RE.search(source)
    low = source.casefold()
    lang = next((x.title() for x in sorted(LANGUAGES, key=len, reverse=True)
                 if re.search(rf"(?<!\\w){re.escape(x)}(?!\\w)", low)), "Unknown")
    title = clean_title(name)
    kind = "series" if season is not None or episode is not None else "movie"
    # Auto Filter Bot stores its canonical Telegram media id in MongoDB _id.
    file_id = doc.get("file_id") or doc.get("_id") or ""
    return {
        "file_id": str(file_id), "file_ref": str(doc.get("file_ref") or ""),
        "file_name": name, "caption": caption,
        "file_size": int(doc.get("file_size") or 0),
        "mime_type": doc.get("mime_type"), "title": title, "type": kind,
        "season": season, "episode": episode,
        "quality": q.group(1).upper() if q else "Auto",
        "language": lang, "year": int(ym.group(1)) if ym else None,
        "poster": extract_poster(caption)
    }

def clean_title(value):
    s = str(value or "")
    s = re.sub(r"\\[[^\\]]*\\]|\\([^)]*\\)", " ", s)
    s = QUALITY_RE.sub(" ", s); s = TECH_RE.sub(" ", s)
    s = SE_RE.sub(" ", s); s = SEASON_RE.sub(" ", s); s = EP_RE.sub(" ", s); s = YEAR_RE.sub(" ", s)
    for x in LANGUAGES:
        s = re.sub(rf"(?<!\\w){re.escape(x)}(?!\\w)", " ", s, flags=re.I)
    s = re.sub(r"[_\\.]+", " ", s); s = re.sub(r"\\s+", " ", s).strip(" -_.")
    return s or "Untitled"

def extract_poster(caption):
    m = re.search(r"https?://[^\\s<>\"']+\\.(?:jpg|jpeg|png|webp)(?:\\?[^\\s<>\"']*)?", caption or "", re.I)
    return m.group(0) if m else None

def stable_id(title, kind):
    return hashlib.sha256(f"{kind}:{title.casefold()}".encode()).hexdigest()[:20]

def normalize(docs):
    titles = {}
    for doc in docs:
        x = parse_doc(doc)
        tid = stable_id(x["title"], x["type"])
        t = titles.setdefault(tid, {
            "id": tid, "title": x["title"], "type": x["type"], "year": x["year"],
            "poster": x["poster"], "description": None, "genre": None, "rating": None,
            "seasons": defaultdict(lambda: defaultdict(list)), "variants": []
        })
        t["year"] = t["year"] or x["year"]; t["poster"] = t["poster"] or x["poster"]
        v = {k: x[k] for k in ("file_id","file_name","file_size","mime_type","quality","language","caption")}
        if x["type"] == "series" and x["season"] is not None:
            t["seasons"][x["season"]][x["episode"] or 0].append(v)
        else:
            t["variants"].append(v)
    out = []
    for t in titles.values():
        t["seasons"] = [
            {"season": int(s), "episodes": [
                {"episode": int(e), "variants": sorted(v, key=lambda z: (z["language"], quality_key(z["quality"])))}
                for e, v in sorted(eps.items())
            ]} for s, eps in sorted(t["seasons"].items())
        ]
        t["variants"] = sorted(t["variants"], key=lambda z: (z["language"], quality_key(z["quality"])))
        out.append(t)
    return sorted(out, key=lambda x: x["title"].casefold())

def quality_key(q):
    m = re.match(r"(\\d+)", q or "")
    return int(m.group(1)) if m else 9999
