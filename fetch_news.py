#!/usr/bin/env python3
"""Pulls today's headlines from top tech sites and builds site/index.html.

Run:  python fetch_news.py
Out:  site/news.json  and  site/index.html
"""
import concurrent.futures as cf
import html
import json
import re
import socket
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urljoin

import feedparser

socket.setdefaulttimeout(20)
UA = "Mozilla/5.0 (compatible; TechDailyBot/1.0; +personal news reader)"

# ---- Tunables ---------------------------------------------------------------
WINDOW_HOURS = 24          # "today" = published in the last 24 hours
FALLBACK_HOURS = 48        # widen once if a quiet day leaves fewer than MIN_ITEMS
MIN_ITEMS = 30
PER_SOURCE_CAP = 10        # keeps one busy site from drowning the rest
MAX_ITEMS = 150
OG_IMAGE_LOOKUPS = 60      # extra page fetches to find pictures missing from feeds

# ---- Sources (name, RSS/Atom URL). Add or remove freely. ---------------------
SOURCES = [
    ("TechCrunch", "https://techcrunch.com/feed/"),
    ("The Verge", "https://www.theverge.com/rss/index.xml"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
    ("WIRED", "https://www.wired.com/feed/rss"),
    ("Engadget", "https://www.engadget.com/rss.xml"),
    ("CNET", "https://www.cnet.com/rss/news/"),
    ("MIT Technology Review", "https://www.technologyreview.com/feed/"),
    ("VentureBeat", "https://venturebeat.com/feed/"),
    ("ZDNET", "https://www.zdnet.com/news/rss.xml"),
    ("TechRadar", "https://www.techradar.com/rss"),
    ("Tom's Hardware", "https://www.tomshardware.com/feeds/all"),
    ("The Next Web", "https://thenextweb.com/feed"),
    ("Gizmodo", "https://gizmodo.com/rss"),
    ("9to5Mac", "https://9to5mac.com/feed/"),
    ("9to5Google", "https://9to5google.com/feed/"),
    ("Android Authority", "https://www.androidauthority.com/feed/"),
    ("BleepingComputer", "https://www.bleepingcomputer.com/feed/"),
    ("The Register", "https://www.theregister.com/headlines.atom"),
    ("IEEE Spectrum", "https://spectrum.ieee.org/feeds/feed.rss"),
    ("Hacker News", "https://hnrss.org/frontpage"),
]

CATEGORIES = [
    ("AI", r"\b(ai|a\.i\.|artificial intelligence|openai|anthropic|claude|chatgpt|gpt-?\d|gemini|llm|copilot|deepmind|machine learning|chatbot|agentic|agents?|nvidia ai|generative)\b"),
    ("Security", r"\b(security|hack(?:ed|er|ers|ing)?|breach|ransomware|malware|vulnerabilit\w+|zero-day|cyber\w*|phishing|exploit\w*|data leak|cve-\d+|privacy)\b"),
    ("Chips & Hardware", r"\b(chip\w*|semiconductor\w*|gpu|cpu|nvidia|amd|intel|tsmc|qualcomm|processor\w*|ram|ssd|laptop\w*|pc|motherboard|quantum|arm)\b"),
    ("Phones & Gadgets", r"\b(iphone|ipad|android|pixel|galaxy|samsung|apple|smartwatch|wearable\w*|earbuds|headphones?|tablet|camera|smart home|gadget\w*|vision pro|quest)\b"),
    ("Gaming", r"\b(game\w*|gaming|playstation|ps5|xbox|nintendo|switch 2|steam|valve|esports)\b"),
    ("Space & Science", r"\b(nasa|spacex|rocket\w*|satellite\w*|mars|moon|telescope|physics|biotech|climate|energy|battery|fusion|robot\w*|ev|electric vehicle\w*|tesla)\b"),
    ("Startups & Business", r"\b(startup\w*|raises?|funding|series [a-d]|valuation|acquir\w+|acquisition|ipo|layoffs?|earnings|revenue|antitrust|regulat\w+|lawsuit|ftc|eu|doj)\b"),
    ("Software & Dev", r"\b(software|app|apps|update|windows|linux|macos|ios|open source|github|developer\w*|programming|browser|chrome|firefox|api|cloud|aws|azure|rust|python)\b"),
]


def clean_text(raw: str, limit: int = 230) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",;:.") + "…"
    return text


def categorize(title: str, summary: str) -> str:
    hay = f"{title} {summary}".lower()
    t = title.lower()
    for name, pat in CATEGORIES:       # title matches win
        if re.search(pat, t):
            return name
    for name, pat in CATEGORIES:
        if re.search(pat, hay):
            return name
    return "General"


def looks_like_image(url: str, mime: str = "") -> bool:
    if mime and mime.startswith("image"):
        return True
    path = urlsplit(url).path.lower()
    return path.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"))


def usable_image(url: str) -> bool:
    low = url.lower()
    return not any(bad in low for bad in ("pixel", "tracker", "1x1", "spacer", "feedburner", "doubleclick", "/ads/"))


def image_from_entry(e, base_url: str):
    for key in ("media_content", "media_thumbnail"):
        for m in e.get(key) or []:
            u = m.get("url")
            if u and usable_image(u) and (key == "media_thumbnail" or m.get("medium") == "image" or looks_like_image(u, m.get("type", ""))):
                return u
    for l in e.get("links") or []:
        if l.get("rel") == "enclosure" and str(l.get("type", "")).startswith("image") and l.get("href"):
            return l["href"]
    for enc in e.get("enclosures") or []:
        if str(enc.get("type", "")).startswith("image") and enc.get("href"):
            return enc["href"]
    blobs = [c.get("value", "") for c in (e.get("content") or [])] + [e.get("summary", "")]
    for blob in blobs:
        for m in re.finditer(r"<img\b[^>]*?\bsrc=[\"']([^\"']+)[\"'][^>]*>", blob or "", re.I):
            tag, u = m.group(0), m.group(1)
            w = re.search(r"width=[\"']?(\d+)", tag)
            if w and int(w.group(1)) < 80:
                continue
            if usable_image(u) and not u.startswith("data:"):
                return urljoin(base_url, u)
    return None


def og_image(page_url: str):
    try:
        req = urllib.request.Request(page_url, headers={"User-Agent": UA, "Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=12) as r:
            head = r.read(200_000).decode("utf-8", "ignore")
    except Exception:
        return None
    for pat in (
        r"<meta[^>]+(?:property|name)=[\"']og:image(?::secure_url)?[\"'][^>]*content=[\"']([^\"']+)",
        r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]*(?:property|name)=[\"']og:image",
        r"<meta[^>]+(?:property|name)=[\"']twitter:image[\"'][^>]*content=[\"']([^\"']+)",
    ):
        m = re.search(pat, head, re.I)
        if m:
            return urljoin(page_url, html.unescape(m.group(1)))
    return None


def entry_time(e):
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        t = e.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def items_from_feed(name: str, parsed) -> list:
    out = []
    for e in parsed.entries:
        when, link, title = entry_time(e), e.get("link"), clean_text(e.get("title", ""), 220)
        if not (when and link and title):
            continue
        summary = clean_text(e.get("summary", "") or "")
        if summary.lower().startswith(title.lower()[:40]):
            summary = ""
        out.append({
            "title": title,
            "url": link,
            "source": name,
            "published": when.isoformat(),
            "summary": summary,
            "image": image_from_entry(e, link),
            "category": categorize(title, summary),
        })
    return out


def fetch_source(src):
    name, url = src
    try:
        parsed = feedparser.parse(url, agent=UA)
        if not parsed.entries:
            return name, [], f"no entries (status {getattr(parsed, 'status', '?')})"
        return name, items_from_feed(name, parsed), None
    except Exception as ex:  # one broken feed must never break the site
        return name, [], str(ex)


def norm_key(item) -> tuple:
    link = urlsplit(item["url"])
    return (link.netloc.replace("www.", "") + link.path.rstrip("/"),)


def build(items_by_source: dict, now: datetime):
    all_items = [i for items in items_by_source.values() for i in items]
    for hours in (WINDOW_HOURS, FALLBACK_HOURS):
        cutoff = now - timedelta(hours=hours)
        fresh = [i for i in all_items if datetime.fromisoformat(i["published"]) >= cutoff]
        if len(fresh) >= MIN_ITEMS or hours == FALLBACK_HOURS:
            break
    fresh.sort(key=lambda i: i["published"], reverse=True)

    seen_links, seen_titles, per_source, picked = set(), set(), {}, []
    for i in fresh:
        k = norm_key(i)
        tk = re.sub(r"\W+", "", i["title"].lower())[:60]
        if k in seen_links or tk in seen_titles:
            continue
        if per_source.get(i["source"], 0) >= PER_SOURCE_CAP:
            continue
        seen_links.add(k); seen_titles.add(tk)
        per_source[i["source"]] = per_source.get(i["source"], 0) + 1
        picked.append(i)
        if len(picked) >= MAX_ITEMS:
            break
    return picked, hours


def fill_missing_images(items):
    todo = [i for i in items if not i["image"]][:OG_IMAGE_LOOKUPS]
    with cf.ThreadPoolExecutor(max_workers=12) as pool:
        for item, img in zip(todo, pool.map(lambda i: og_image(i["url"]), todo)):
            if img and usable_image(img):
                item["image"] = img


def main():
    now = datetime.now(timezone.utc)
    items_by_source, ok, failed = {}, [], []
    with cf.ThreadPoolExecutor(max_workers=10) as pool:
        for name, items, err in pool.map(fetch_source, SOURCES):
            if err:
                failed.append({"source": name, "error": err})
                print(f"  ! {name}: {err}", file=sys.stderr)
            else:
                ok.append(name)
                items_by_source[name] = items
                print(f"  ✓ {name}: {len(items)} entries")

    picked, hours = build(items_by_source, now)
    fill_missing_images(picked)

    payload = {
        "generated_at": now.isoformat(),
        "window_hours": hours,
        "sources_ok": ok,
        "sources_failed": failed,
        "items": picked,
    }
    out = Path(__file__).parent / "site"
    out.mkdir(exist_ok=True)
    (out / "news.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    template = (Path(__file__).parent / "template.html").read_text(encoding="utf-8")
    blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    (out / "index.html").write_text(template.replace("/*__DATA__*/null", blob), encoding="utf-8")
    print(f"Wrote {len(picked)} stories from {len(ok)} sources ({len(failed)} failed).")
    if not picked:
        sys.exit("No stories found - check your network or the feed list.")


if __name__ == "__main__":
    main()
