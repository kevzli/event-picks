#!/usr/bin/env python3
"""Weekly event picker: scans Eventbrite, Luma and Partiful for one city, scores
each event against profile.json, and writes 5-10 picks for the rest of the month
to output/picks.html. Standard library only (Python 3.9+).

Usage:
  python3 picker.py            # fetch, score, write output/picks.html
  python3 picker.py --no-open  # don't open the page afterwards
  python3 picker.py --debug    # also print every scored candidate
"""
import calendar
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
# The Mac app keeps profile, output and cache in Application Support (EVENTPICKS_DATA);
# run from a checkout, everything lives next to this file.
DATA = Path(os.environ.get("EVENTPICKS_DATA") or HERE)
OUT = DATA / "output"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


# ---------------------------------------------------------------- fetching

CACHE = DATA / ".cache"
CACHE_HOURS = 6
_last_hit = {}


def get(url, as_json=False, timeout=25):
    """GET with a 6-hour disk cache, per-host pacing, and backoff on HTTP 429."""
    key = CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".txt")
    if key.exists() and time.time() - key.stat().st_mtime < CACHE_HOURS * 3600:
        body = key.read_text(encoding="utf-8")
        return json.loads(body) if as_json else body
    host = urllib.parse.urlparse(url).netloc
    for attempt in range(4):
        wait = 2.5 - (time.time() - _last_hit.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _last_hit[host] = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", "replace")
            break
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 3:
                raise
            time.sleep(10 * (attempt + 1))
    CACHE.mkdir(exist_ok=True)
    key.write_text(body, encoding="utf-8")
    return json.loads(body) if as_json else body


def next_data(page):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    return json.loads(m.group(1)) if m else {}


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def city(profile, key):
    """City-specific source setting; older profiles kept these at the top level."""
    return (profile.get("city") or {}).get(key) or profile.get(key)


def km(lat1, lng1, lat2, lng2):
    from math import asin, cos, radians, sin, sqrt
    a = sin(radians(lat2 - lat1) / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(radians(lng2 - lng1) / 2) ** 2
    return 12742 * asin(sqrt(a))


def price_from_text(text):
    found = [float(x) for x in re.findall(r"\$\s?(\d{1,4}(?:\.\d{1,2})?)", text or "")]
    return min(found) if found else None


def fetch_eventbrite(profile, start, end, tz):
    events = []
    searches = list(profile.get("eventbrite_searches", []))
    for lane in profile["lanes"].values():
        if lane.get("weight", 0) > 0:
            searches += [q for q in lane.get("search", []) if q not in searches]
    for slug in searches:
        for page in range(1, profile.get("eventbrite_pages_per_search", 1) + 1):
            url = ("https://www.eventbrite.com/d/%s/%s/?start_date=%s&end_date=%s&page=%d"
                   % (city(profile, "eventbrite_slug"), slug, start.date().isoformat(), end.date().isoformat(), page))
            try:
                h = get(url)
                i = h.index("window.__SERVER_DATA__ = ") + len("window.__SERVER_DATA__ = ")
                data, _ = json.JSONDecoder().raw_decode(h[i:])
                results = data["search_data"]["events"]["results"]
            except Exception as e:  # one bad search shouldn't sink the run
                print("  eventbrite %s p%d failed: %s" % (slug, page, e), file=sys.stderr)
                break
            for r in results:
                if r.get("is_online_event") or r.get("is_cancelled") or not r.get("start_date"):
                    continue
                st = datetime.fromisoformat("%sT%s" % (r["start_date"], r.get("start_time") or "12:00"))
                st = st.replace(tzinfo=ZoneInfo(r.get("timezone") or profile["timezone"]))
                venue = (r.get("primary_venue") or {})
                addr = (venue.get("address") or {}).get("localized_address_display", "")
                tags = " ".join(t.get("display_name", "") for t in r.get("tags") or [])
                tix = r.get("ticket_availability") or {}
                price = None
                if tix.get("is_free"):
                    price = 0.0
                elif (tix.get("minimum_ticket_price") or {}).get("major_value"):
                    price = float(tix["minimum_ticket_price"]["major_value"])
                else:
                    price = price_from_text(r.get("name", ""))
                    if price is None and re.search(r"\bfree\b", r.get("name", ""), re.I):
                        price = 0.0
                addr_obj = venue.get("address") or {}
                coord = None
                if addr_obj.get("latitude") and addr_obj.get("longitude"):
                    coord = (float(addr_obj["latitude"]), float(addr_obj["longitude"]))
                org = ""
                for t in r.get("tags") or []:
                    if t.get("prefix") == "OrganizerTag":
                        org += " " + t.get("display_name", "")
                events.append({
                    "source": "Eventbrite",
                    "title": r.get("name", "").strip(),
                    "url": r.get("url", "").split("?")[0],
                    "start": st,
                    "venue": (venue.get("name") or "") + (" · " + addr if addr else ""),
                    "host": "",
                    "org_id": r.get("primary_organizer_id") or "",
                    "coord": coord,
                    "price": price,
                    "text": " ".join([r.get("name", ""), r.get("summary") or "", r.get("full_description") or "", tags, org]),
                    "image": first_url(r.get("image")),
                    "blurb": blurb(r.get("full_description") or r.get("summary") or "", r.get("name", "")),
                    "flags": [],
                })
            if len(results) < 20:
                break
    return events


def luma_entry_to_event(e, profile):
    ev = e.get("event") or {}
    cal = e.get("calendar") or {}
    ti = e.get("ticket_info") or {}
    geo = ev.get("geo_address_info") or {}
    price = None
    if ti.get("is_free"):
        price = 0.0
    elif isinstance(ti.get("price"), dict) and ti["price"].get("cents") is not None:
        price = ti["price"]["cents"] / 100.0
    hosts = ", ".join(h.get("name", "") for h in (e.get("hosts") or [])[:3] if h.get("name"))
    flags = []
    if ti.get("is_sold_out"):
        flags.append("sold out")
    if e.get("waitlist_active") or ev.get("waitlist_status") == "active" and ti.get("is_sold_out"):
        flags.append("waitlist")
    if ti.get("require_approval"):
        flags.append("needs approval")
    if ev.get("location_type") not in (None, "offline"):
        return None
    c = ev.get("coordinate") or {}
    coord = (c["latitude"], c["longitude"]) if c.get("latitude") is not None else None
    return {
        "coord": coord,
        "source": "Luma",
        "title": (ev.get("name") or "").strip(),
        "url": "https://luma.com/" + ev.get("url", ""),
        "api_id": ev.get("api_id"),
        "start": parse_iso(ev["start_at"]),
        "venue": geo.get("address") or geo.get("sublocality") or geo.get("city_state") or "",
        "host": cal.get("name") or hosts,
        "price": price,
        "text": " ".join([ev.get("name") or "", cal.get("name") or "", cal.get("description_short") or "", hosts]),
        "image": ev.get("cover_url"),
        "blurb": "",
        "flags": flags,
    }


def fetch_luma(profile, start, end, tz):
    out = {}
    cursor, pages = None, 0
    while pages < 12:
        url = ("https://api.lu.ma/discover/get-paginated-events?discover_place_api_id=%s&pagination_limit=50"
               % city(profile, "luma_place_id"))
        if cursor:
            url += "&pagination_cursor=" + urllib.parse.quote(cursor)
        try:
            d = get(url, as_json=True)
        except Exception as e:
            print("  luma discover failed: %s" % e, file=sys.stderr)
            break
        last = None
        for e in d.get("entries", []):
            ev = luma_entry_to_event(e, profile)
            if ev:
                out[ev["url"]] = ev
                last = ev["start"]
        pages += 1
        cursor = d.get("next_cursor")
        if not d.get("has_more") or not cursor or (last and last > end):
            break

    for slug in profile.get("luma_calendars", []):
        try:
            page = get("https://luma.com/" + slug)
        except Exception as e:
            print("  luma calendar %s failed: %s" % (slug, e), file=sys.stderr)
            continue
        data = next_data(page).get("props", {}).get("pageProps", {}).get("initialData", {}).get("data", {})
        entries = list((data.get("upcoming") or {}).get("entries") or [])
        cal_id = (data.get("calendar") or {}).get("api_id")
        # the page embeds only the first few; the API has the rest (some calendars refuse it)
        if cal_id and (not entries or (data.get("upcoming") or {}).get("has_more")):
            try:
                d = get("https://api.lu.ma/calendar/get-items?calendar_api_id=%s&period=future&pagination_limit=50"
                        % cal_id, as_json=True)
                entries += d.get("entries", [])
            except Exception:
                pass
        for e in entries:
            ev = luma_entry_to_event(e, profile)
            if ev:
                ev["followed"] = True
                out[ev["url"]] = ev

    # discover listings carry no description; pull it for events in the window
    in_window = [ev for ev in out.values() if start <= ev["start"] <= end and ev.get("api_id")]

    def add_detail(ev):
        try:
            d = get("https://api.lu.ma/event/get?event_api_id=" + ev["api_id"], as_json=True)
            desc = flatten_prosemirror(d.get("description_mirror"))
            ev["text"] += " " + desc[:3000]
            ev["blurb"] = blurb(desc, ev["title"])
            geo = (d.get("event") or {}).get("geo_address_info") or {}
            if geo.get("full_address"):
                ev["venue"] = geo["full_address"]
            if ev["price"] is None:
                ev["price"] = price_from_text(ev["text"])
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(add_detail, in_window))
    return list(out.values())


def first_url(obj):
    """First http(s) URL anywhere inside an image record (sites nest them differently)."""
    if isinstance(obj, str):
        return obj if obj.startswith("http") else None
    if isinstance(obj, dict):
        for k in ("url", "original", "upload", "src"):
            if k in obj and first_url(obj[k]):
                return first_url(obj[k])
        obj = list(obj.values())
    if isinstance(obj, list):
        for v in obj:
            u = first_url(v)
            if u:
                return u
    return None


def partiful_image(img):
    # uploads are private in Firebase storage; Partiful serves them through imgix
    path = ((img or {}).get("upload") or {}).get("path")
    if path:
        return "https://partiful.imgix.net/%s?fit=clip&w=640&auto=format" % urllib.parse.quote(path)
    return first_url(img)


def blurb(text, title, limit=240):
    """A short plain-text description: skip a repeated title, cut at a word boundary."""
    t = re.sub(r"<[^>]+>", " ", text or "")
    t = re.sub(r"\s+", " ", t).strip()
    if t.lower().startswith((title or "").lower().strip()) and len(t) > len(title or "") + 20:
        t = t[len(title):].lstrip(" -–—:|·.!")
    if t.strip().lower() == (title or "").strip().lower():
        return ""
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0].rstrip(",.;:-–— ") + "…"
    return t


def flatten_prosemirror(node):
    if not node:
        return ""
    if isinstance(node, dict):
        return (node.get("text") or "") + " " + " ".join(flatten_prosemirror(c) for c in node.get("content") or [])
    if isinstance(node, list):
        return " ".join(flatten_prosemirror(c) for c in node)
    return ""


def fetch_partiful(profile, start, end, tz):
    page = next_data(get(city(profile, "partiful_explore")))
    found = {}

    def walk(o, section=""):
        if isinstance(o, dict):
            sec = o.get("title") if isinstance(o.get("items"), list) else section
            ev = o.get("event")
            if isinstance(ev, dict) and ev.get("id") and ev.get("startDate") and ev.get("title"):
                found[ev["id"]] = (ev, sec or "")
            for v in o.values():
                walk(v, sec if isinstance(sec, str) else section)
        elif isinstance(o, list):
            for v in o:
                walk(v, section)

    walk(page.get("props", {}).get("pageProps", {}))
    events = []
    for ev, section in found.values():
        loc = ev.get("locationInfo") or {}
        venue = loc.get("displayName") or ", ".join((loc.get("displayAddressLines") or [])[:1]) \
            or (loc.get("mapsInfo") or {}).get("approximateLocation", "")
        if loc.get("neighborhood"):
            venue = (venue + " · " if venue else "") + loc["neighborhood"]
        text = " ".join([ev.get("title", ""), ev.get("description") or "", section,
                         " ".join(str(t) for t in ev.get("descriptionTags") or [])])
        price = price_from_text(ev.get("description") or "")
        if price is None and re.search(r"\bfree\b", text, re.I):
            price = 0.0
        events.append({
            "source": "Partiful",
            "title": ev["title"].strip(),
            "url": "https://partiful.com/e/" + ev["id"],
            "start": parse_iso(ev["startDate"]),
            "venue": venue,
            "host": "",
            "price": price,
            "text": text,
            "image": partiful_image(ev.get("image")),
            "blurb": blurb(ev.get("description") or "", ev["title"]),
            "flags": [],
        })
    return events


# ----------------------------------------------------------------- scoring

def kw_regex(words):
    parts = []
    for w in words:
        w = re.escape(w.lower())
        # CJK keywords have no word boundaries
        parts.append(w if re.search(r"[　-鿿]", w) else r"(?<![a-z0-9])" + w + r"(?![a-z0-9])")
    return re.compile("|".join(parts))


def score_event(ev, profile, rx):
    text = ev["text"].lower()
    title = ev["title"].lower()
    reasons, lane_hits = [], []
    score = 0.0
    for key, lane in profile["lanes"].items():
        hits = sorted(set(rx["lane:" + key].findall(text)))
        if hits and lane.get("weight", 0) <= 0:
            # a slider at 0 means "not for me": push these down instead of ignoring them
            if rx["lane:" + key].search(title):
                score -= 6
                reasons.append("−%s (turned off)" % lane["label"])
        elif hits:
            title_hit = bool(rx["lane:" + key].search(title))
            # a keyword in the title counts fully; description-only mentions count less
            pts = lane["weight"] * (1.0 if title_hit else 0.6) + min(len(hits) - 1, 3) * 0.5
            lane_hits.append((pts, key, hits))
    lane_hits.sort(reverse=True)
    if lane_hits:
        score += lane_hits[0][0]
        for pts, key, hits in lane_hits[1:3]:
            score += pts * 0.3
        for pts, key, hits in lane_hits[:2]:
            reasons.append("%s (%s)" % (profile["lanes"][key]["label"], ", ".join(hits[:3])))
    ev["lane"] = lane_hits[0][1] if lane_hits else None

    for p in profile["penalties"]:
        hits = rx["pen:" + p["label"]].findall(title + " " + text[:600])
        if hits:
            score += p["points"]
            reasons.append("−%s" % p["label"])

    org_text = (ev.get("host", "") + " " + text).lower()
    if ev.get("followed") or any(o.lower() in org_text for o in profile.get("followed_organizers", []) if o.strip()):
        score += profile.get("followed_bonus", 4)
        reasons.append("organizer you follow")

    local = ev["start"].astimezone(ZoneInfo(profile["timezone"]))
    tr = profile["time_rules"]
    if local.hour >= tr["late_start_hour"] or local.hour < 4:
        score += tr["late_penalty"]
        reasons.append("−starts late")
    elif local.hour < tr["early_start_hour"]:
        score += tr["early_penalty"]
        reasons.append("−early start")

    pr = profile["price_rules"]
    if ev["price"] == 0:
        score += pr["free_bonus"]
    elif ev["price"] is not None and ev["price"] > 80:
        score += pr["over_80_penalty"]
        reasons.append("−pricey ($%d)" % ev["price"])
    elif ev["price"] is not None and ev["price"] > 40:
        score += pr["over_40_penalty"]
        reasons.append("−$%d" % ev["price"])

    if "sold out" in ev["flags"]:
        score -= 3
    ev["score"] = round(score, 2)
    ev["reasons"] = reasons
    return ev


def norm_title(t):
    t = re.sub(r"\(.*?\)|\[.*?\]|【.*?】", "", t.lower())
    t = re.sub(r"\b(oct|nov|dec|jan|feb|mar|apr|may|jun|jul|aug|sep|sept)\b\.?\s*\d*|\d+/\d+|\d{4}", "", t)
    return re.sub(r"[^a-z0-9　-鿿]+", " ", t).strip()[:40]


def choose(events, profile):
    cands = sorted(events, key=lambda e: -e["score"])
    picks, per_day, per_src, per_org, per_lane, seen_titles = [], {}, {}, {}, {}, set()
    tz = ZoneInfo(profile["timezone"])
    repeat = profile.get("lane_repeat_penalty", 0)
    picks_max = profile["picks_max"]
    picks_min = min(profile.get("picks_min", 5), picks_max)
    # "something different" comes from outside your three highest-rated interests
    by_weight = sorted(profile["lanes"], key=lambda k: -profile["lanes"][k].get("weight", 0))
    wild_lanes = set(profile.get("wildcard_lanes") or
                     [k for k in by_weight[3:] if profile["lanes"][k].get("weight", 0) > 0])

    def org(ev):
        return (ev.get("org_id") or ev.get("host") or "").lower().strip()

    def ok(ev):
        day = ev["start"].astimezone(tz).date()
        return (ev not in picks
                and norm_title(ev["title"]) not in seen_titles
                and per_day.get(day, 0) < profile["max_per_day"]
                and per_src.get(ev["source"], 0) < profile["max_per_source"]
                and (not org(ev) or per_org.get(org(ev), 0) < profile.get("max_per_organizer", 99)))

    def eff(ev):
        # each pick from the same interest lane makes the next one a little less attractive
        return ev["score"] - repeat * per_lane.get(ev["lane"], 0)

    def take(ev, tag=None):
        day = ev["start"].astimezone(tz).date()
        per_day[day] = per_day.get(day, 0) + 1
        per_src[ev["source"]] = per_src.get(ev["source"], 0) + 1
        per_lane[ev["lane"]] = per_lane.get(ev["lane"], 0) + 1
        if org(ev):
            per_org[org(ev)] = per_org.get(org(ev), 0) + 1
        seen_titles.add(norm_title(ev["title"]))
        ev["tag"] = tag
        picks.append(ev)

    good = [e for e in cands if e["score"] >= 5 and "sold out" not in e["flags"]]
    # 1) one strong pick from each site, if it has one
    for src in ("Eventbrite", "Luma", "Partiful"):
        for ev in good:
            if len(picks) < picks_max and ev["source"] == src and ev["score"] >= 6 and ok(ev):
                take(ev)
                break
    # 2) reserve a slot for something outside the usual mix
    for ev in good:
        if len(picks) < picks_max and ev["lane"] in wild_lanes and ok(ev):
            take(ev, "Something different")
            break
    # 3) fill greedily by diversity-adjusted score: everything strong, then at least picks_min
    while len(picks) < picks_max:
        rest = sorted((e for e in good if ok(e)), key=lambda e: -eff(e))
        if not rest:
            break
        best = rest[0]
        if eff(best) < 8 and len(picks) >= picks_min:
            break
        take(best)
    picks.sort(key=lambda e: e["start"])
    runners = [e for e in good if e not in picks and norm_title(e["title"]) not in seen_titles]
    # runners-up get the same variety rules: one per title and one per organizer
    dedup, rt, ro = [], set(), set(per_org)
    for e in runners:
        k = norm_title(e["title"])
        if k in rt or (org(e) and org(e) in ro):
            continue
        rt.add(k)
        if org(e):
            ro.add(org(e))
        dedup.append(e)
    return picks, dedup[:12]


# ------------------------------------------------------------------ output

def fmt_when(ev, tz):
    d = ev["start"].astimezone(tz)
    return d.strftime("%a %b %-d · %-I:%M %p").replace(":00 ", " ")


def fmt_price(p):
    if p is None:
        return "Price on page"
    return "Free" if p == 0 else "$%g" % p


def short_venue(v):
    """Just the place name: drop street addresses after the first separator."""
    v = (v or "").split(" · ")[0]
    return v.split(",")[0].strip()


def render(picks, runners, meta, profile):
    tz = ZoneInfo(profile["timezone"])

    def card(ev):
        url = html.escape(ev["url"])
        src = ev["source"].lower()
        if ev.get("image"):
            thumb = ('<img src="%s" alt="" loading="lazy" '
                     'onerror="this.parentNode.classList.add(&#39;ph&#39;);this.remove()">' % html.escape(ev["image"]))
        else:
            thumb = ""
        label = ev["source"] + (" · " + ev["tag"] if ev.get("tag") else "")
        meta_line = " · ".join(x for x in (fmt_when(ev, tz), short_venue(ev["venue"]),
                                           fmt_price(ev["price"]) if ev["price"] is not None else "") if x)
        desc = '<p class="desc">%s</p>' % html.escape(ev["blurb"]) if ev.get("blurb") else ""
        return """
<a class="ev" data-src="%s" data-start="%s" href="%s" target="_blank" rel="noopener">
  <div class="thumb%s">%s</div>
  <div class="txt">
    <p class="src"><i class="dot %s"></i>%s</p>
    <h2>%s</h2>
    <p class="meta">%s</p>
    %s
  </div>
</a>""" % (ev["source"], ev["start"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), url, "" if thumb else " ph", thumb, src, html.escape(label),
           html.escape(ev["title"]), html.escape(meta_line), desc)

    top = "\n".join(card(e) for e in picks) or '<p class="empty">No strong matches this week.</p>'
    more = "\n".join(card(e) for e in runners) or '<p class="empty">Nothing else this week.</p>'
    errors = '<p class="err">Some sources had trouble this run: %s</p>' % html.escape(meta["errors"]) if meta["errors"] else ""

    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Event Picks</title>
<style>
:root{--bg:#fbfbfa;--ink:#1c1c1e;--mute:#8a8a8e;--line:#ececec;--seg:#efeff0;--pill:#fff;
--eb:#e8590c;--lu:#6e5cf0;--pf:#d6336c}
@media (prefers-color-scheme: dark){:root{--bg:#161618;--ink:#f2f2f4;--mute:#8e8e93;--line:#2a2a2e;--seg:#232326;--pill:#3a3a3e}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:720px;margin:0 auto;padding:28px 20px 56px}
h1{font-size:22px;font-weight:700;letter-spacing:-.01em;margin:0 0 18px}
.bar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:8px}
.seg{display:inline-flex;background:var(--seg);border-radius:9px;padding:2px}
.seg button{font:inherit;font-size:13px;font-weight:600;border:0;background:none;color:var(--mute);border-radius:7px;padding:5px 12px;cursor:pointer}
.seg button[aria-selected=true]{background:var(--pill);color:var(--ink);box-shadow:0 1px 2px rgba(0,0,0,.12)}
.seg .n{font-weight:500;opacity:.55;margin-left:5px}
.sites{display:flex;gap:2px}
.sites button{font:inherit;font-size:13px;border:0;background:none;color:var(--mute);padding:4px 8px;border-radius:6px;cursor:pointer}
.sites button[aria-pressed=true]{color:var(--ink);font-weight:600}
.ev{display:grid;grid-template-columns:112px minmax(0,1fr);gap:16px;padding:16px 0;border-bottom:1px solid var(--line);color:inherit;text-decoration:none}
.ev[hidden],.panel[hidden]{display:none}
.ev:hover h2{text-decoration:underline;text-decoration-thickness:1px;text-underline-offset:3px}
.thumb{width:112px;height:112px;border-radius:10px;overflow:hidden;background:var(--seg)}
.thumb img{width:100%%;height:100%%;object-fit:cover;display:block}
.src{margin:0;font-size:12px;color:var(--mute);display:flex;align-items:center;gap:6px}
.dot{width:7px;height:7px;border-radius:50%%;display:inline-block}
.dot.eventbrite{background:var(--eb)}.dot.luma{background:var(--lu)}.dot.partiful{background:var(--pf)}
h2{font-size:16px;font-weight:600;line-height:1.3;margin:3px 0 2px}
.meta{margin:0;font-size:13px;color:var(--mute)}
.desc{margin:6px 0 0;font-size:13.5px;line-height:1.45;opacity:.85;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.empty,.err{color:var(--mute);font-size:13px;margin-top:20px}
@media (max-width:520px){.ev{grid-template-columns:76px minmax(0,1fr);gap:12px}.thumb{width:76px;height:76px}}
</style></head><body><main>
<h1>Picks through %s</h1>
<div class="bar">
  <div class="seg" role="tablist">
    <button role="tab" aria-selected="true" data-tab="top">Top picks<span class="n" id="n-top">%d</span></button><button role="tab" aria-selected="false" data-tab="more">More<span class="n" id="n-more">%d</span></button>
  </div>
  <div class="sites" role="group" aria-label="Filter by site">
    <button aria-pressed="true" data-f="all">All</button><button aria-pressed="false" data-f="Eventbrite">Eventbrite</button><button aria-pressed="false" data-f="Luma">Luma</button><button aria-pressed="false" data-f="Partiful">Partiful</button>
  </div>
</div>
<section class="panel" id="top" data-target="%d">%s</section>
<section class="panel" id="more" hidden>%s</section>
%s
</main><script>
// The page is written once a week, so drop events that have already started
// and top up the main picks from the runners-up, best first.
function prune(){
  var now=Date.now();
  document.querySelectorAll('.ev').forEach(function(e){if(Date.parse(e.dataset.start)<now)e.remove()});
  var top=document.getElementById('top'),more=document.getElementById('more');
  var want=+top.dataset.target-top.querySelectorAll('.ev').length;
  more.querySelectorAll('.ev').forEach(function(e){
    if(want>0){var t=Date.parse(e.dataset.start),placed=false;
      top.querySelectorAll('.ev').forEach(function(x){if(!placed&&Date.parse(x.dataset.start)>t){top.insertBefore(e,x);placed=true}});
      if(!placed)top.appendChild(e);want--}
  });
  [['top','n-top','No upcoming picks left. Refresh to find more.'],['more','n-more','Nothing else coming up.']].forEach(function(a){
    var p=document.getElementById(a[0]),n=p.querySelectorAll('.ev').length;
    document.getElementById(a[1]).textContent=n;
    var em=p.querySelector('.empty');
    if(!n&&!em){em=document.createElement('p');em.className='empty';em.textContent=a[2];p.appendChild(em)}
    if(n&&em)em.remove();
  });
}
prune();
setInterval(prune,60000);
document.querySelectorAll('.seg button').forEach(function(b){b.onclick=function(){
  document.querySelectorAll('.seg button').forEach(function(x){x.setAttribute('aria-selected',x===b)});
  document.querySelectorAll('.panel').forEach(function(p){p.hidden=p.id!==b.dataset.tab});
  window.scrollTo(0,0);
}});
document.querySelectorAll('.sites button').forEach(function(b){b.onclick=function(){
  document.querySelectorAll('.sites button').forEach(function(x){x.setAttribute('aria-pressed',x===b)});
  document.querySelectorAll('.ev').forEach(function(c){c.hidden=b.dataset.f!=='all'&&c.dataset.src!==b.dataset.f});
}});
</script></body></html>""" % (meta["end_label"], len(picks), len(runners), len(picks), top, more, errors)


# -------------------------------------------------------------------- main

def default_browser_id():
    """Bundle id of the app that opens https links (the macOS default browser)."""
    import plistlib
    plist = Path.home() / "Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist"
    try:
        for h in plistlib.loads(plist.read_bytes()).get("LSHandlers", []):
            if h.get("LSHandlerURLScheme") == "https":
                return h.get("LSHandlerRoleAll")
    except Exception:
        pass
    return None


def main():
    args = set(sys.argv[1:])
    DATA.mkdir(parents=True, exist_ok=True)
    if not (DATA / "profile.json").exists():
        shutil.copy(HERE / "profile.json", DATA / "profile.json")
    profile = json.loads((DATA / "profile.json").read_text())
    tz = ZoneInfo(profile["timezone"])
    now = datetime.now(tz)
    last_day = calendar.monthrange(now.year, now.month)[1]
    end = now.replace(day=last_day, hour=23, minute=59, second=59)
    # late in the month, look at least a week ahead so there's still something to pick
    end = max(end, (now + timedelta(days=profile["min_days_ahead"])).replace(hour=23, minute=59))
    start = now

    rx = {}
    for key, lane in profile["lanes"].items():
        rx["lane:" + key] = kw_regex(lane["keywords"])
    for p in profile["penalties"]:
        rx["pen:" + p["label"]] = kw_regex(p["keywords"])

    all_events, errors, counts = [], [], {}
    for name, fn in (("Eventbrite", fetch_eventbrite), ("Luma", fetch_luma), ("Partiful", fetch_partiful)):
        print("Fetching %s…" % name)
        try:
            evs = fn(profile, start, end, tz)
        except Exception as e:
            errors.append("%s: %s" % (name, e))
            evs = []
        evs = [e for e in evs if start <= e["start"] <= end]
        counts[name] = len(evs)
        all_events += evs

    skip = set(profile.get("skip_urls", []))
    skip_kw = [k.lower() for k in profile.get("skip_title_keywords", [])]
    busy = [(datetime.fromisoformat(b["start"]).replace(tzinfo=tz), datetime.fromisoformat(b["end"]).replace(tzinfo=tz))
            for b in profile.get("busy", [])]
    home = profile.get("home")
    filters = profile.get("filters", {})
    max_km = filters.get("max_km") or (home or {}).get("max_km")
    max_price = filters.get("max_price")
    tr = profile["time_rules"]
    uniq = {}
    for ev in all_events:
        if ev["url"] in skip or any(k in ev["title"].lower() for k in skip_kw):
            continue
        if home and max_km and ev.get("coord") and km(home["lat"], home["lng"], *ev["coord"]) > max_km:
            continue
        if max_price is not None and ev["price"] is not None and ev["price"] > max_price:
            continue
        hour = ev["start"].astimezone(tz).hour
        if filters.get("avoid_late") and (hour >= tr["late_start_hour"] or hour < 4):
            continue
        if filters.get("avoid_early") and 4 <= hour < tr["early_start_hour"]:
            continue
        if any(b0 <= ev["start"] < b1 for b0, b1 in busy):
            continue
        uniq[ev["url"]] = ev
    scored = [score_event(ev, profile, rx) for ev in uniq.values()]
    picks, runners = choose(scored, profile)

    if "--debug" in args:
        for ev in sorted(scored, key=lambda e: -e["score"])[:60]:
            print("%6.1f  %-10s %s  %s  | %s" % (ev["score"], ev["source"], fmt_when(ev, tz), ev["title"][:70], "; ".join(ev["reasons"])))

    OUT.mkdir(exist_ok=True)
    meta = {
        "end_label": end.strftime("%b %-d"),
        "run_label": now.strftime("%a %b %-d, %-I:%M %p"),
        "total": len(scored), "counts": counts, "errors": "; ".join(errors),
    }
    page = render(picks, runners, meta, profile)
    (OUT / "picks.html").write_text(page, encoding="utf-8")
    (OUT / ("picks-%s.html" % now.date().isoformat())).write_text(page, encoding="utf-8")
    (OUT / "picks.json").write_text(json.dumps([
        {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in e.items() if k != "text"}
        for e in picks], indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n%d picks (from %d events: %s)%s" % (len(picks), len(scored), counts,
                                                ("; errors: " + "; ".join(errors)) if errors else ""))
    for ev in picks:
        print("  %5.1f  %-10s %-22s %s" % (ev["score"], ev["source"], fmt_when(ev, tz), ev["title"]))
    print("\nWrote %s" % (OUT / "picks.html"))

    if sys.platform == "darwin" and "--quiet" not in args:
        subprocess.run(["osascript", "-e", 'display notification "%d picks through %s" with title "Weekly event picks"'
                        % (len(picks), meta["end_label"])], check=False)
        if profile.get("open_after_run") and "--no-open" not in args:
            app = Path.home() / "Applications" / "Event Picks.app"
            if app.exists():
                subprocess.run(["open", str(app)], check=False)
            else:
                # .html files may be tied to Safari even when another browser is the
                # default; open with whatever handles https links instead
                browser = default_browser_id()
                subprocess.run(["open"] + (["-b", browser] if browser else []) + [str(OUT / "picks.html")], check=False)


if __name__ == "__main__":
    main()
