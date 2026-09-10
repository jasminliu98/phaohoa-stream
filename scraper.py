import requests
import json
import hashlib
import re
import time
import os
import html as htmllib
from collections import Counter
from datetime import datetime, timezone, timedelta
from urllib.parse import unquote
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG  — Phaohoa1.live -> xoiche.tv (Next.js, khong con __NUXT_DATA__)
# ─────────────────────────────────────────────────────────────────────────────

VN_TZ = timezone(timedelta(hours=7))

BASE_URL = "https://xoiche.tv"
API_BASE = f"{BASE_URL}/api"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer":   f"{BASE_URL}/",
    "Origin":    BASE_URL,
    "Accept":    "application/json, text/plain, */*",
    "Accept-Language": "vi-VN,vi;q=0.9,fr-FR;q=0.8,fr;q=0.7,en-US;q=0.6,en;q=0.5",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

THUMBS_DIR    = "thumbs"
REPO_RAW      = os.environ.get("REPO_RAW", "")
THUMB_VERSION = "v3"

PAST_HOURS     = 6     # giu tran da bat dau <= 6h
UPCOMING_HOURS = 36    # giu tran sap dau trong 36h (sua 24 neu muon)

UUID_RE = re.compile(r'[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}')

FINISHED_STATUSES = {"finished", "completed", "complete", "ended", "ft", "full_time",
                     "fulltime", "cancelled", "canceled", "postponed", "abandoned"}
LIVE_STATUSES = {"live", "in_progress", "inprogress", "playing", "half_time",
                 "halftime", "half-time", "ht", "1h", "2h"}

SPORT_ALIASES = {
    "football": "football", "soccer": "football", "bong-da": "football",
    "bong da": "football", "bongda": "football", "bóng đá": "football",
    "volleyball": "volleyball", "bong-chuyen": "volleyball", "bong chuyen": "volleyball",
    "bongchuyen": "volleyball", "bóng chuyền": "volleyball", "voleibol": "volleyball",
    "basketball": "bong-ro", "bong-ro": "bong-ro", "bong ro": "bong-ro",
    "bongro": "bong-ro", "bóng rổ": "bong-ro",
    "tennis": "tennis",
    "esports": "esports", "esport": "esports", "e-sports": "esports",
    "badminton": "cau-long", "cau-long": "cau-long", "cau long": "cau-long",
    "caulong": "cau-long", "cầu lông": "cau-long",
    "boxing": "boxing", "mma": "boxing", "vo-thuat": "boxing",
    "vo thuat": "boxing", "võ thuật": "boxing",
    "billiards": "billiards", "billiard": "billiards", "bi-a": "billiards",
    "bi a": "billiards", "pool": "billiards", "snooker": "billiards",
    "table-tennis": "bong-ban", "bong-ban": "bong-ban", "bong ban": "bong-ban",
    "tabletennis": "bong-ban", "bóng bàn": "bong-ban",
}
CATE_MAP = {
    "football":   "⚽ Bóng Đá",
    "volleyball": "🏐 Bóng Chuyền",
    "bong-ro":    "🏀 Bóng Rổ",
    "tennis":     "🎾 Tennis",
    "esports":    "🎮 Esport",
    "cau-long":   "🏸 Cầu Lông",
    "boxing":     "🥊 Võ Thuật",
    "billiards":  "🎱 Billiards",
    "bong-ban":   "🏓 Bóng Bàn",
}
CATE_ORDER = list(CATE_MAP.keys())

_PAGE_CACHE = {}

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def now_vn():
    return datetime.now(tz=VN_TZ)

def make_id(text, prefix):
    return f"{prefix}-{hashlib.md5(str(text).encode()).hexdigest()[:10]}"

def full_url(path):
    if not path: return ""
    if path.startswith("http"): return path
    if path.startswith("//"): return "https:" + path
    if path.startswith("/"): return f"{BASE_URL}{path}"
    return f"{BASE_URL}/{path}"

def http_get(url, timeout=15, as_json=False):
    try:
        res = SESSION.get(url, timeout=timeout)
        if res.status_code != 200:
            return None
        if as_json:
            try: return res.json()
            except Exception: return None
        return res.text
    except Exception:
        return None

def get_page(path):
    if path not in _PAGE_CACHE:
        url = path if path.startswith("http") else f"{BASE_URL}{path}"
        _PAGE_CACHE[path] = http_get(url, timeout=25)
    return _PAGE_CACHE[path]

def fetch_image(url):
    if not url: return None
    try:
        res = SESSION.get(url, timeout=8)
        res.raise_for_status()
        return Image.open(BytesIO(res.content)).convert("RGBA")
    except Exception:
        return None

def norm_sport(s):
    s = (s or "").strip().lower()
    if not s: return "football"
    if s in SPORT_ALIASES: return SPORT_ALIASES[s]
    for alias, canon in SPORT_ALIASES.items():
        if alias in s: return canon
    return "other"

def parse_start_time(s):
    if not isinstance(s, str) or not s.strip(): return None
    s = s.strip()
    if s.endswith("Z"): s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=VN_TZ)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=VN_TZ)
        except Exception:
            continue
    return None

def to_vn(dt):
    if dt is None: return None
    if dt.tzinfo is None: return dt.replace(tzinfo=VN_TZ)
    return dt.astimezone(VN_TZ)

def format_time_hhmm(dt):
    dt = to_vn(dt)
    return dt.strftime("%H:%M") if dt else ""

def format_date_ddmm(dt):
    dt = to_vn(dt)
    return dt.strftime("%d/%m") if dt else ""

def parse_when(txt):
    """'02:00 - 11/09' (hien thi tren card) -> datetime VN"""
    m = re.search(r'(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2})/(\d{1,2})', txt or "")
    if not m: return None
    hh, mm, dd, mo = (int(g) for g in m.groups())
    now = now_vn()
    for yr in (now.year, now.year + 1):
        try:
            dt = datetime(yr, mo, dd, hh, mm, tzinfo=VN_TZ)
        except ValueError:
            continue
        if dt >= now - timedelta(days=45):
            return dt
    return None

def time_sort_val(dt):
    dt = to_vn(dt)
    return dt.timestamp() if dt else float("inf")

def get_stream_type(url):
    if not url: return "hls"
    c = url.lower().split("?")[0]
    if c.endswith(".flv"): return "httpflv"
    if c.endswith(".mpd"): return "dash"
    if c.endswith(".mp4"): return "mp4"
    return "hls"

STREAM_EXTS = (".m3u8", ".flv", ".mpd", ".mp4", ".ts")
STREAM_PATH_HINTS = ("/live/", "/ph/", "/stream", "/hls", "/playback")

def is_stream_url(u):
    if not isinstance(u, str) or len(u) < 8: return False
    if not (u.startswith("http") or u.startswith("/")): return False
    low = u.lower().split("?")[0]
    if any(low.endswith(e) for e in STREAM_EXTS): return True
    if any(h in low for h in STREAM_PATH_HINTS): return True
    if ".m3u8" in u.lower() or ".flv" in u.lower(): return True
    return False

# ─────────────────────────────────────────────────────────────────────────────
# TIM & TRICH XUAT MATCH (phong thu nhieu dinh dang API)
# ─────────────────────────────────────────────────────────────────────────────

def is_match_dict(d):
    if not isinstance(d, dict): return False
    has_id = False
    for k in ("slug", "uuid", "fixture", "fixture_id", "match_id", "matchId", "id"):
        v = d.get(k)
        if isinstance(v, (str, int)) and str(v).strip():
            has_id = True
            break
    if not has_id: return False
    for k in ("home_team_name", "away_team_name", "homeTeam", "awayTeam",
              "home_team", "away_team", "team_a", "team_b", "teams"):
        v = d.get(k)
        if isinstance(v, str) and v.strip(): return True
        if isinstance(v, dict): return True
        if isinstance(v, list) and v: return True
    return False

def find_match_dicts(data, out=None):
    if out is None: out = []
    if isinstance(data, list):
        for x in data: find_match_dicts(x, out)
    elif isinstance(data, dict):
        if is_match_dict(data):
            out.append(data)
        else:
            for v in data.values():
                find_match_dicts(v, out)
    return out

def normalize_match(item):
    if not isinstance(item, dict): return None

    def s(*keys):
        for k in keys:
            v = item.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""

    status = s("status", "state", "phase", "match_status").lower()
    if status in FINISHED_STATUSES: return None

    # ---- id / uuid / slug ----
    match_id = ""
    for k in ("id", "uuid", "match_id", "matchId", "fixture", "fixture_id"):
        v = item.get(k)
        if isinstance(v, str) and v.strip(): match_id = v.strip(); break
        if isinstance(v, int): match_id = str(v); break

    uuid = ""
    for k in ("uuid", "id", "fixture", "fixture_id", "match_uuid"):
        v = item.get(k)
        if isinstance(v, str) and UUID_RE.fullmatch(v.strip()):
            uuid = v.strip(); break

    slug = s("slug", "match_slug")
    if not slug:
        u = s("url", "link", "match_url")
        if "/tran-dau/" in u:
            slug = u.split("/tran-dau/", 1)[1].split("?", 1)[0].strip("/")
    if not slug: slug = match_id
    if not match_id: match_id = slug

    # ---- doi ----
    def team_name(side):
        for k in (f"{side}_team_name", f"{side}Name", f"{side}_name", f"{side}TeamName"):
            v = item.get(k)
            if isinstance(v, str) and v.strip(): return htmllib.unescape(v.strip())
        for k in (f"{side}_team", f"{side}Team"):
            v = item.get(k)
            if isinstance(v, str) and v.strip(): return htmllib.unescape(v.strip())
            if isinstance(v, dict):
                for nk in ("name", "title", "team_name", "full_name"):
                    nv = v.get(nk)
                    if isinstance(nv, str) and nv.strip(): return htmllib.unescape(nv.strip())
        tdict = item.get("teams")
        if isinstance(tdict, dict):
            v = tdict.get(side) or tdict.get(f"{side}_team")
            if isinstance(v, str) and v.strip(): return htmllib.unescape(v.strip())
            if isinstance(v, dict):
                for nk in ("name", "title", "team_name"):
                    nv = v.get(nk)
                    if isinstance(nv, str) and nv.strip(): return htmllib.unescape(nv.strip())
        return ""

    def team_logo(side):
        for k in (f"{side}_team_logo", f"{side}TeamLogo", f"{side}_logo",
                  f"{side}_team_crest", f"{side}TeamCrest", f"{side}_team_image"):
            v = item.get(k)
            if isinstance(v, str) and v.strip(): return v.strip()
        for k in (f"{side}_team", f"{side}Team"):
            v = item.get(k)
            if isinstance(v, dict):
                for lk in ("logo", "logo_url", "crest", "image", "badge"):
                    lv = v.get(lk)
                    if isinstance(lv, str) and lv.strip(): return lv.strip()
        tdict = item.get("teams")
        if isinstance(tdict, dict):
            v = tdict.get(side) or tdict.get(f"{side}_team")
            if isinstance(v, dict):
                for lk in ("logo", "logo_url", "crest", "image"):
                    lv = v.get(lk)
                    if isinstance(lv, str) and lv.strip(): return lv.strip()
        return ""

    home, away = team_name("home"), team_name("away")
    if not (home and away):
        nm = s("name", "match_name", "title")
        if " gặp " in nm:
            a, b = nm.split(" gặp ", 1)
            home = home or htmllib.unescape(a.strip())
            away = away or htmllib.unescape(b.strip())
    if not (home and away): return None
    if not match_id: match_id = make_id(f"{home}-vs-{away}", "m")

    # ---- thoi gian ----
    start_dt = None
    for k in ("start_time", "startTime", "startDate", "start_at", "starts_at", "kickoff", "kick_off"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            start_dt = parse_start_time(v)
            if start_dt: break
    if start_dt is None:
        w = s("when", "card_when")
        if w: start_dt = parse_when(w)
    start_dt = to_vn(start_dt) if start_dt else None

    # ---- live ----
    is_live = status in LIVE_STATUSES
    if "EventInProgress" in s("eventStatus", "event_status"): is_live = True
    if item.get("is_live") is True or item.get("live") is True: is_live = True
    if not status: status = "live" if is_live else "scheduled"

    # ---- mon / giai ----
    sport_raw = s("sport_slug", "sport", "sport_name", "category_slug", "category")
    if not sport_raw and isinstance(item.get("sport"), dict):
        for k in ("slug", "name", "key", "title"):
            v = item["sport"].get(k)
            if isinstance(v, str) and v.strip(): sport_raw = v.strip(); break
    cate = norm_sport(sport_raw)

    league = s("tournament_name", "league_name", "leagueName", "league", "competition")
    for k in ("tournament", "league_obj", "competition_obj"):
        v = item.get(k)
        if isinstance(v, dict):
            ln = v.get("name") or v.get("title")
            if isinstance(ln, str) and ln.strip():
                league = league or ln.strip(); break
    league = htmllib.unescape(league) if league else ""

    # ---- ty so ----
    def num(*keys):
        for k in keys:
            v = item.get(k)
            if isinstance(v, bool): continue
            if isinstance(v, int): return v
            if isinstance(v, str) and v.strip().isdigit(): return int(v.strip())
        return 0
    home_score = num("home_score", "score_home", "homeScore")
    away_score = num("away_score", "score_away", "awayScore")

    # ---- BLV ----
    commentators = []
    cands = item.get("commentators")
    if not cands: cands = item.get("rooms")
    if isinstance(cands, dict): cands = list(cands.values())
    if isinstance(cands, list):
        for c in cands:
            if isinstance(c, dict):
                nm = ""
                for k in ("name", "commentator_name", "blv_name", "display_name", "title"):
                    v = c.get(k)
                    if isinstance(v, dict): v = v.get("name")
                    if isinstance(v, str) and v.strip(): nm = v.strip(); break
                room = ""
                for k in ("room", "room_id", "room_uuid", "uuid", "id"):
                    v = c.get(k)
                    if isinstance(v, str) and v.strip(): room = v.strip(); break
                if nm or room:
                    commentators.append({"id": room or nm, "name": nm or "BLV", "room": room})
            elif isinstance(c, str) and c.strip():
                commentators.append({"id": c.strip(), "name": c.strip(), "room": ""})

    return {
        "match_id": match_id,
        "uuid": uuid,
        "slug": slug,
        "cate_type": cate,
        "name": f"{home} vs {away}",
        "team_a": home, "team_b": away,
        "logo_a": full_url(team_logo("home")),
        "logo_b": full_url(team_logo("away")),
        "league": league,
        "start_dt": start_dt,
        "time": format_time_hhmm(start_dt),
        "date": format_date_ddmm(start_dt),
        "time_sort": time_sort_val(start_dt),
        "is_live": is_live,
        "status": status,
        "home_score": home_score,
        "away_score": away_score,
        "commentators": commentators,
    }

# ─────────────────────────────────────────────────────────────────────────────
# TRICH XUAT LINK STREAM (phong thu nhieu dinh danh)
# ─────────────────────────────────────────────────────────────────────────────

def extract_streams(data, room_names=None):
    """Tim moi URL co dang stream (.m3u8/.flv//live/...) trong JSON bat ky,
    gan nhan BLV neu co. room_names: {room_uuid: ten_blv}"""
    room_names = room_names or {}
    out = {}

    def add(name, url):
        u = full_url(url)
        key = (name or "").strip() or "Server"
        out.setdefault(key, [])
        if u not in out[key]: out[key].append(u)

    def label_of(d):
        if any(k in d for k in ("home_team_name", "away_team_name", "homeTeam",
                                "awayTeam", "home_team", "away_team")):
            return None
        for k in ("name", "commentator", "commentator_name", "blv", "label",
                  "title", "server", "server_name", "quality", "room_name"):
            v = d.get(k)
            if isinstance(v, dict): v = v.get("name") or v.get("title")
            if isinstance(v, str) and v.strip() and not v.strip().startswith(("http", "/")):
                return v.strip()
        for k in ("room", "room_id", "room_uuid", "uuid"):
            v = d.get(k)
            if isinstance(v, str) and v.strip():
                if v.strip() in room_names: return room_names[v.strip()]
                return f"Room {v.strip()[:8]}"
        return None

    def walk(obj, name=None):
        if isinstance(obj, str):
            if is_stream_url(obj): add(name, obj)
        elif isinstance(obj, list):
            for x in obj: walk(x, name)
        elif isinstance(obj, dict):
            cur = label_of(obj) or name
            for k, v in obj.items():
                if isinstance(v, str) and is_stream_url(v):
                    add(cur or room_names.get(k), v)
                elif isinstance(v, (list, dict)):
                    walk(v, cur)
    try:
        walk(data)
    except Exception:
        pass
    return out

def extract_inline_streams(item, room_names=None):
    """Link stream nam ngay trong item cua API list (kieu cu phaohoa)"""
    out = {}
    cands = item.get("commentators")
    if isinstance(cands, list):
        for c in cands:
            if not isinstance(c, dict): continue
            nm = ""
            for k in ("name", "commentator_name", "blv_name"):
                v = c.get(k)
                if isinstance(v, str) and v.strip(): nm = v.strip(); break
            urls = []
            for k in ("stream_url", "backup_stream_url", "flv_stream_url",
                      "hls_url", "url", "src", "file", "playback_url"):
                v = c.get(k)
                if isinstance(v, str) and is_stream_url(v):
                    u = full_url(v)
                    if u not in urls: urls.append(u)
            if nm and urls:
                out.setdefault(nm, [])
                for u in urls:
                    if u not in out[nm]: out[nm].append(u)
    for key in ("sources", "streams"):
        sub = item.get(key)
        if sub:
            for k, v in extract_streams(sub, room_names).items():
                out.setdefault(k, [])
                for u in v:
                    if u not in out[k]: out[k].append(u)
    for k in ("primary_stream_url", "main_stream_url", "stream_url", "hls_url", "flv_url"):
        v = item.get(k)
        if isinstance(v, str) and is_stream_url(v):
            out.setdefault("Server", [])
            u = full_url(v)
            if u not in out["Server"]: out["Server"].append(u)
    return out

def try_sources_endpoint(mid_val, rooms=()):
    """GET /api/matches/{id}/sources — endpoint moi cua xoiche.tv"""
    base = f"{API_BASE}/matches/{mid_val}/sources"
    data = http_get(base, timeout=12, as_json=True)
    if data is None:
        data = http_get(base + "/", timeout=12, as_json=True)
    if data is None:
        return None
    results = extract_streams(data)
    if results:
        return results
    for r in list(rooms)[:6]:        # thu theo tung room BLV neu goi plain rong
        data2 = http_get(f"{base}?room={r}", timeout=12, as_json=True)
        if data2 is None: continue
        for k, v in extract_streams(data2).items():
            results.setdefault(k, [])
            for u in v:
                if u not in results[k]: results[k].append(u)
    return results or None

def try_detail_endpoint(mid_val):
    for path in (f"{API_BASE}/matches/{mid_val}/", f"{API_BASE}/matches/{mid_val}"):
        data = http_get(path, timeout=12, as_json=True)
        if data is None: continue
        s = extract_streams(data)
        if s: return s
    return None

def find_uuid_from_page(slug):
    txt = get_page(f"/tran-dau/{slug}")
    if not txt: return None
    for pat in (r'/api/matches/([0-9a-fA-F-]{36})',
                r'fixture=([0-9a-fA-F-]{36})',
                r'"(?:id|uuid|fixture)"\s*:\s*"([0-9a-fA-F-]{36})"'):
        m = re.search(pat, txt)
        if m: return m.group(1)
    uuids = UUID_RE.findall(txt)
    if uuids:
        return Counter(uuids).most_common(1)[0][0]
    return None

def find_streams_in_match_page(slug):
    txt = get_page(f"/tran-dau/{slug}")
    if not txt: return {}
    flat = txt.replace("\\/", "/").replace("&amp;", "&")
    urls = re.findall(r'(?:https?://[^\s"\'<>\\]+|/[^\s"\'<>\\]+)\.(?:m3u8|flv|mpd)(?:\?[^\s"\'<>\\]*)?', flat)
    out = {}
    for u in urls:
        fu = full_url(u)
        out.setdefault("Server", [])
        if fu not in out["Server"]: out["Server"].append(fu)
    return out

def get_streams_for_match(md):
    streams = {}
    def absorb(more):
        for k, urls in (more or {}).items():
            streams.setdefault(k, [])
            for u in urls:
                if u not in streams[k]: streams[k].append(u)

    absorb(md.get("inline_streams"))
    if streams and any(k != "Server" for k in streams):
        return streams                     # da co du link BLV tu list API

    rooms = [c.get("room") for c in (md.get("commentators") or []) if c.get("room")]

    cands = []
    for x in (md.get("uuid"), md.get("slug")):
        if x and x not in cands: cands.append(x)
    tm = re.search(r'-(\d{4,})$', md.get("slug") or "")
    if tm and tm.group(1) not in cands: cands.append(tm.group(1))

    for x in cands:
        got = try_sources_endpoint(x, rooms)
        if got: absorb(got); break
    if not streams:
        for x in cands:
            got = try_detail_endpoint(x)
            if got: absorb(got); break

    if not streams and md.get("slug"):
        uuid = md.get("uuid") or find_uuid_from_page(md["slug"])
        if uuid and uuid not in cands:
            absorb(try_sources_endpoint(uuid, rooms))
        if not streams:
            absorb(find_streams_in_match_page(md["slug"]))
    return streams

# ─────────────────────────────────────────────────────────────────────────────
# LAY DU LIEU: API BACKEND (uu tien) + HTML (JSON-LD + match card)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_raw_matches_from_api():
    today = now_vn().strftime("%Y-%m-%d")
    tomorrow = (now_vn() + timedelta(days=1)).strftime("%Y-%m-%d")
    urls = [
        f"{API_BASE}/matches",
        f"{API_BASE}/matches/",
        f"{API_BASE}/matches/?limit=200",
        f"{API_BASE}/matches/?status=scheduled,live,half_time&ordering=-start_time",
        f"{API_BASE}/matches/?status=live,scheduled&limit=200",
        f"{API_BASE}/matches/?date={today}&ordering=-start_time",
        f"{API_BASE}/matches/?date={tomorrow}&ordering=-start_time",
        f"{API_BASE}/matches/live",
        f"{API_BASE}/chrome-demand",
    ]
    items = []
    for url in urls:
        data = http_get(url, timeout=15, as_json=True)
        if data is None: continue
        found = find_match_dicts(data)
        hops = 0
        while (isinstance(data, dict) and isinstance(data.get("next"), str)
               and data["next"].startswith("http") and hops < 4):
            nxt = http_get(data["next"], timeout=15, as_json=True)
            if nxt is None: break
            data = nxt
            found.extend(find_match_dicts(data))
            hops += 1
        if found:
            print(f"  + {url} -> {len(found)} muc")
            if not items and os.environ.get("DEBUG"):
                with open("debug_api.json", "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            items.extend(found)
        else:
            preview = json.dumps(data, ensure_ascii=False, default=str)[:160]
            print(f"  - {url} -> 0 muc ({preview})")
    if items:
        print(f"  * API keys mau: {sorted(str(k) for k in items[0].keys())[:18]}")
    return items

def _collect_sport_events(data, out):
    if isinstance(data, list):
        for x in data: _collect_sport_events(x, out)
    elif isinstance(data, dict):
        if data.get("@type") == "SportsEvent":
            out.append(data)
        else:
            for v in data.values(): _collect_sport_events(v, out)

def parse_jsonld_events(html_text):
    """Trang moi (Next.js) khong con NUXT_DATA, nhung co JSON-LD SportsEvent"""
    events = []
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html_text, re.DOTALL):
        try:
            data = json.loads(m.group(1))
        except Exception:
            continue
        _collect_sport_events(data, events)
    out = []
    for ev in events:
        if not isinstance(ev, dict): continue
        url = ev.get("url") or ""
        if "/tran-dau/" not in url: continue
        slug = url.split("/tran-dau/", 1)[1].split("?", 1)[0].strip("/")
        home = away = ""
        ht, at = ev.get("homeTeam"), ev.get("awayTeam")
        if isinstance(ht, dict): home = (ht.get("name") or "").strip()
        if isinstance(at, dict): away = (at.get("name") or "").strip()
        name = ev.get("name") or ""
        if (not home or not away) and " gặp " in name:
            a, b = name.split(" gặp ", 1)
            home = home or a.strip(); away = away or b.strip()
        if not (home and away): continue
        out.append({
            "slug": slug,
            "name": name,
            "home": htmllib.unescape(home),
            "away": htmllib.unescape(away),
            "start": ev.get("startDate") or "",
            "live": "EventInProgress" in (ev.get("eventStatus") or ""),
            "sport": ev.get("sport") or "",
        })
    return out

def decode_next_img(src):
    """Logo qua proxy /_next/image?url=ENCODED -> tra URL goc"""
    if not src: return ""
    if "/_next/image" in src:
        m = re.search(r'[?&]url=([^&]+)', src)
        if m: return unquote(m.group(1))
    if src.startswith("http"): return src
    if src.startswith("/"): return BASE_URL + src
    return src

def parse_match_cards(html_text):
    """Doc the tran matchCard_*: logo, giai, BLV + room, ty so, fixture uuid"""
    cards = {}
    for m in re.finditer(r'<article\b([^>]*)>(.*?)</article>', html_text, re.DOTALL):
        attrs, blk = m.group(1), m.group(2)
        if "matchCard_card" not in attrs: continue
        sm = re.search(r'href="/tran-dau/([^"?/#]+)', blk)
        if not sm: continue
        slug = sm.group(1)
        card = cards.setdefault(slug, {})

        if 'data-phase="live"' in attrs:
            card["live"] = True

        sp = re.search(r'data-sport="([^"]+)"', blk)
        if sp: card["sport"] = sp.group(1)

        for side in ("home", "away"):
            nm = re.search(rf'data-side="{side}"[^>]*>.*?<strong>(.*?)</strong>', blk, re.DOTALL)
            if nm: card[side] = htmllib.unescape(nm.group(1)).strip()
            lg = re.search(rf'data-side="{side}"[^>]*>.*?<img[^>]+src="([^"]+)"', blk, re.DOTALL)
            if lg: card[f"logo_{side}"] = decode_next_img(lg.group(1))

        lm = re.search(r'matchCard_league__\w*"[^>]*>', blk)
        if lm:
            seg = blk[lm.end():]
            cut = seg.find("matchCard_board")
            if cut > 0: seg = seg[:cut]
            lsm = re.search(r'<span>([^<]{2,80})</span>', seg)
            if lsm: card["league"] = htmllib.unescape(lsm.group(1)).strip()

        pd = re.search(r'matchCard_period__\w*"[^>]*>([^<]+)<', blk)
        if pd:
            card["period"] = htmllib.unescape(pd.group(1)).strip()
            if card["period"] in ("Đang diễn ra", "Hiệp 1", "Hiệp 2", "Nghỉ", "Nghỉ giữa hiệp"):
                card["live"] = True

        wh = re.search(r'matchCard_when__\w*"[^>]*>([^<]+)<', blk)
        if wh: card["when"] = wh.group(1).strip()

        sc = re.search(r'matchCard_score__\w*[^>]*>([^<]+)<', blk)
        if sc:
            scm = re.search(r'(\d+)\s*[–\-—]\s*(\d+)', sc.group(1))
            if scm:
                card["home_score"] = int(scm.group(1))
                card["away_score"] = int(scm.group(2))

        fx = re.search(r'fixture=([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})', blk)
        if fx: card["fixture"] = fx.group(1)

        card.setdefault("commentators", [])
        for cm in re.finditer(r'matchCard_chip__\w+"[^>]*href="([^"]*)"[^>]*>.*?<span>([^<]+)</span>', blk, re.DOTALL):
            href, cname = cm.group(1), htmllib.unescape(cm.group(2)).strip()
            if not cname: continue
            rm = re.search(r'room=([0-9a-fA-F-]{36})', href)
            room = rm.group(1) if rm else ""
            if not any(c["name"] == cname for c in card["commentators"]):
                card["commentators"].append({"id": room or cname, "name": cname, "room": room})
    return cards

def merge_event_card(ev, card):
    ev = ev or {}
    card = card or {}
    live = bool(ev.get("live") or card.get("live"))
    start = ev.get("start") or ""
    if not start and card.get("when"):
        dt = parse_when(card["when"])
        if dt: start = dt.isoformat()
    return {
        "slug": ev.get("slug") or card.get("slug") or "",
        "name": ev.get("name") or "",
        "home_team_name": ev.get("home") or card.get("home") or "",
        "away_team_name": ev.get("away") or card.get("away") or "",
        "home_team_logo": card.get("logo_home", ""),
        "away_team_logo": card.get("logo_away", ""),
        "tournament_name": card.get("league", ""),
        "sport": ev.get("sport") or card.get("sport") or "",
        "start_time": start,
        "eventStatus": "https://schema.org/EventInProgress" if live else "",
        "status": "live" if live else "scheduled",
        "fixture": card.get("fixture", ""),
        "commentators": card.get("commentators", []),
        "home_score": card.get("home_score", 0),
        "away_score": card.get("away_score", 0),
        "when": card.get("when", ""),
    }

def fetch_raw_matches_from_html():
    events, cards = {}, {}
    for path in ("/", "/lich-thi-dau"):
        txt = get_page(path)
        if not txt:
            print(f"  - HTML {path}: khong lay duoc")
            continue
        if "matchCard_card" not in txt and "application/ld+json" not in txt:
            print(f"  ! HTML {path} khong co du lieu tran (co the bi Cloudflare chan)")
            continue
        evs = parse_jsonld_events(txt)
        cds = parse_match_cards(txt)
        print(f"  + HTML {path}: {len(evs)} JSON-LD, {len(cds)} the tran")
        for ev in evs:
            if ev["slug"] not in events: events[ev["slug"]] = ev
        for slug, c in cds.items():
            if slug not in cards: cards[slug] = c
    raw = []
    for slug, ev in events.items():
        raw.append(merge_event_card(ev, cards.get(slug)))
    for slug, card in cards.items():
        if slug not in events:
            raw.append(merge_event_card({}, card))
    return raw

# ─────────────────────────────────────────────────────────────────────────────
# GOM TRAN + LAY LINK
# ─────────────────────────────────────────────────────────────────────────────

def merge_match(a, b):
    for k in ("uuid", "slug", "league", "logo_a", "logo_b"):
        if not a.get(k) and b.get(k): a[k] = b[k]
    if not a.get("team_a") and b.get("team_a"): a["team_a"] = b["team_a"]
    if not a.get("team_b") and b.get("team_b"): a["team_b"] = b["team_b"]
    if a.get("team_a") and a.get("team_b"):
        a["name"] = f"{a['team_a']} vs {a['team_b']}"
    if b.get("is_live") and not a.get("is_live"):
        a["is_live"] = True
        a["status"] = b.get("status") or a.get("status")
    if not a.get("start_dt") and b.get("start_dt"):
        a["start_dt"] = b["start_dt"]
        a["time"] = b["time"]; a["date"] = b["date"]; a["time_sort"] = b["time_sort"]
    if not a.get("home_score") and b.get("home_score"): a["home_score"] = b["home_score"]
    if not a.get("away_score") and b.get("away_score"): a["away_score"] = b["away_score"]
    if a.get("cate_type") == "other" and b.get("cate_type") not in ("", "other"):
        a["cate_type"] = b["cate_type"]
    for c in b.get("commentators") or []:
        if not any(x.get("id") == c.get("id") or x.get("name") == c.get("name")
                   for x in a.setdefault("commentators", [])):
            a["commentators"].append(c)
    for k, urls in (b.get("inline_streams") or {}).items():
        a.setdefault("inline_streams", {}).setdefault(k, [])
        for u in urls:
            if u not in a["inline_streams"][k]: a["inline_streams"][k].append(u)

def get_grouped_matches():
    raw_items = []

    print("1) Lay danh sach tran tu API backend...")
    raw_items.extend(fetch_raw_matches_from_api())

    print("2) Lay danh sach tran tu HTML (JSON-LD + the tran)...")
    raw_items.extend(fetch_raw_matches_from_html())

    print(f"   -> Tong muc goc: {len(raw_items)}")
    if not raw_items: return {}

    grouped, by_slug, by_uuid = {}, {}, {}
    for item in raw_items:
        try:
            nm = normalize_match(item)
        except Exception:
            continue
        if not nm: continue

        room_names = {c["room"]: c["name"] for c in nm["commentators"] if c.get("room")}
        inline = extract_inline_streams(item, room_names)
        if inline: nm["inline_streams"] = inline

        entry = None
        if nm["uuid"] and nm["uuid"] in by_uuid:
            entry = by_uuid[nm["uuid"]]
        elif nm["slug"] and nm["slug"] in by_slug:
            entry = by_slug[nm["slug"]]
        elif nm["match_id"] and nm["match_id"] in grouped:
            entry = grouped[nm["match_id"]]
        if entry is None:
            grouped[nm["match_id"]] = nm
            if nm["slug"]: by_slug[nm["slug"]] = nm
            if nm["uuid"]: by_uuid[nm["uuid"]] = nm
        else:
            merge_match(entry, nm)

    print(f"   -> Sau khi gop/dedup: {len(grouped)} tran")

    now = now_vn()
    kept, dropped = {}, 0
    for key, md in grouped.items():
        if md["is_live"] or md["start_dt"] is None:
            kept[key] = md
        elif (now - timedelta(hours=PAST_HOURS)) <= md["start_dt"] <= (now + timedelta(hours=UPCOMING_HOURS)):
            kept[key] = md
        else:
            dropped += 1
    if dropped:
        print(f"   -> Bo {dropped} tran ngoai cua so (-{PAST_HOURS}h / +{UPCOMING_HOURS}h)")
    grouped = kept

    total = len(grouped)
    for i, (key, md) in enumerate(grouped.items(), 1):
        md["blvs_dict"] = get_streams_for_match(md)
        n_link = sum(len(v) for v in md["blvs_dict"].values())
        blvs = ", ".join(list(md["blvs_dict"])[:4]) or "khong co link"
        print(f"   [{i}/{total}] {md['name']}: {n_link} link ({blvs})")
        time.sleep(0.12)

    return {k: v for k, v in grouped.items() if v["blvs_dict"]}

# ─────────────────────────────────────────────────────────────────────────────
# THUMBNAIL
# ─────────────────────────────────────────────────────────────────────────────

def make_thumbnail(match, match_id_safe):
    os.makedirs(THUMBS_DIR, exist_ok=True)
    cache_key = match.get("logo_a", "") + match.get("logo_b", "") + THUMB_VERSION
    logo_hash  = hashlib.md5(cache_key.encode()).hexdigest()[:8]
    date_str   = now_vn().strftime("%Y%m%d")
    out_path   = f"{THUMBS_DIR}/{match_id_safe}_{logo_hash}_{date_str}.png"
    if os.path.exists(out_path): return out_path

    W, H = 1600, 1200
    HEADER_H, FOOTER_H = 180, 160
    bg = Image.new("RGB", (W, H), (245, 245, 248))
    draw = ImageDraw.Draw(bg)

    for y in range(HEADER_H, H - FOOTER_H):
        ratio = (y - HEADER_H) / (H - FOOTER_H - HEADER_H)
        gray = int(248 - ratio * 18)
        draw.line([(0, y), (W, y)], fill=(gray, gray, gray + 4))

    draw.rectangle([(0, 0), (W, HEADER_H)], fill=(13, 20, 40))
    draw.rectangle([(0, H - FOOTER_H), (W, H)], fill=(13, 20, 40))
    ACCENT = (0, 168, 107)   # xanh la — brand XoiChe
    draw.rectangle([(0, HEADER_H), (W, HEADER_H + 5)], fill=ACCENT)
    draw.rectangle([(0, H - FOOTER_H - 5), (W, H - FOOTER_H)], fill=ACCENT)

    FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    try:
        font_vs = ImageFont.truetype(FONT_BOLD, 160)
        font_time = ImageFont.truetype(FONT_BOLD, 100)
        font_team = ImageFont.truetype(FONT_BOLD, 58)
        font_footer = ImageFont.truetype(FONT_BOLD, 50)
    except Exception:
        font_vs = font_time = font_team = font_footer = ImageFont.load_default()

    content_top = HEADER_H + 5
    content_bot = H - FOOTER_H - 5
    content_h = content_bot - content_top
    logo_size, name_h, time_h = 360, 120, 110
    gap_ln, gap_nt = 40, 60
    total_h = logo_size + gap_ln + name_h + gap_nt + time_h
    block_top = content_top + (content_h - total_h) // 2
    logo_y = block_top
    name_center = logo_y + logo_size + gap_ln + name_h // 2
    time_y = logo_y + logo_size + gap_ln + name_h + gap_nt + time_h // 2

    def draw_team_name(text, cx):
        max_w = W // 2 - 60
        fs = 58
        f = font_team
        while fs >= 28:
            try: f = ImageFont.truetype(FONT_BOLD, fs)
            except Exception: f = ImageFont.load_default()
            if draw.textbbox((0, 0), text, font=f)[2] <= max_w: break
            fs -= 3
        draw.text((cx, name_center), text, fill=(20, 20, 20), font=f, anchor="mm")

    for side, logo_key, cx in [("a", "logo_a", W // 4), ("b", "logo_b", W * 3 // 4)]:
        url = match.get(logo_key)
        if url:
            img = fetch_image(url)
            if img:
                try:
                    r = img.resize((logo_size, logo_size), Image.LANCZOS)
                    bg.paste(r, (cx - logo_size // 2, logo_y), r)
                except Exception: pass

    draw.text((W // 2, logo_y + logo_size // 2), "VS", fill=ACCENT, font=font_vs, anchor="mm")
    if match.get("team_a"): draw_team_name(match["team_a"], W // 4)
    if match.get("team_b"): draw_team_name(match["team_b"], W * 3 // 4)

    time_fmt = match.get("time", "")
    date_fmt = match.get("date", "")
    td = f"{time_fmt} {date_fmt}" if time_fmt and date_fmt else (time_fmt or "")
    if td:
        fs = 100
        f_t = font_time
        while fs >= 40:
            try: f_t = ImageFont.truetype(FONT_BOLD, fs)
            except Exception: f_t = ImageFont.load_default()
            if draw.textbbox((0, 0), td, font=f_t)[2] <= W - 100: break
            fs -= 4
        draw.text((W // 2 + 4, time_y + 4), td, fill=ACCENT, font=f_t, anchor="mm")
        draw.text((W // 2, time_y), td, fill=(15, 15, 15), font=f_t, anchor="mm")

    header_txt = (match.get("league") or "XÔI CHÈ TV").upper()
    fs = 62
    f = None
    while fs >= 28:
        try: f = ImageFont.truetype(FONT_BOLD, fs)
        except Exception: f = ImageFont.load_default()
        if draw.textbbox((0, 0), header_txt, font=f)[2] <= W - 60: break
        fs -= 3
    draw.text((W // 2, HEADER_H // 2), header_txt, fill=(255, 255, 255), font=f, anchor="mm")

    draw.text((W // 2, H - FOOTER_H // 2), "xoiche.tv", fill=(255, 255, 255),
              font=font_footer, anchor="mm")

    draw.rectangle([(0, 0), (W - 1, H - 1)], outline=(180, 180, 180), width=3)
    bg.save(out_path, "PNG", optimize=True)
    return out_path

def cleanup_old_thumbs(days=3):
    if not os.path.exists(THUMBS_DIR): return
    cutoff = now_vn() - timedelta(days=days)
    for fname in os.listdir(THUMBS_DIR):
        if not fname.endswith(".png"): continue
        m = re.search(r'_(\d{8})\.png$', fname)
        if m:
            try:
                if datetime.strptime(m.group(1), "%Y%m%d").replace(tzinfo=VN_TZ) < cutoff:
                    os.remove(os.path.join(THUMBS_DIR, fname))
            except Exception: pass

# ─────────────────────────────────────────────────────────────────────────────
# BUILD CHANNEL JSON
# ─────────────────────────────────────────────────────────────────────────────

def build_channel(match, match_id_safe, thumb_url=""):
    uid = make_id(match_id_safe, "xc")
    src_id = make_id(match_id_safe, "src")
    ct_id = make_id(match_id_safe, "ct")
    st_id = make_id(match_id_safe, "st")

    stream_links = []
    for blv_name, urls in match["blvs_dict"].items():
        for idx, s_url in enumerate(urls):
            stream_links.append({
                "id": make_id(s_url + str(idx), "lnk"),
                "name": f"{blv_name} {idx + 1}" if len(urls) > 1 else blv_name,
                "type": get_stream_type(s_url),
                "default": len(stream_links) == 0,
                "url": s_url,
                "request_headers": [
                    {"key": "Referer", "value": f"{BASE_URL}/"},
                    {"key": "User-Agent", "value": HEADERS["User-Agent"]},
                    {"key": "Origin", "value": BASE_URL},
                ],
            })

    label_text = "● LIVE" if match["is_live"] else "🕐 Sắp"
    label_color = "#ff4444" if match["is_live"] else "#aaaaaa"

    t, d = match.get("time", ""), match.get("date", "")
    display = f"{match['name']} | {t} {d}" if t and d else (f"{match['name']} | {t}" if t else match["name"])

    channel = {
        "id": uid,
        "name": display,
        "type": "single",
        "display": "thumbnail-only",
        "enable_detail": False,
        "labels": [{"text": label_text, "position": "top-left", "color": "#00000080", "text_color": label_color}],
        "sources": [{
            "id": src_id,
            "name": "XoiChe TV",
            "contents": [{
                "id": ct_id,
                "name": match["name"],
                "streams": [{"id": st_id, "name": "XoiChe TV", "stream_links": stream_links}],
            }],
        }],
        "org_metadata": {
            "league": match.get("league", ""),
            "team_a": match.get("team_a", ""),
            "team_b": match.get("team_b", ""),
            "logo_a": match.get("logo_a", ""),
            "logo_b": match.get("logo_b", ""),
            "time": match.get("time", ""),
            "date": match.get("date", ""),
            "blv": ", ".join(match["blvs_dict"].keys()),
            "is_live": match["is_live"],
            "cate_type": match.get("cate_type", ""),
            "status": match.get("status", ""),
            "home_score": match.get("home_score", 0),
            "away_score": match.get("away_score", 0),
        },
    }
    if thumb_url:
        channel["image"] = {
            "padding": 1, "background_color": "#ffffff", "display": "contain",
            "url": thumb_url, "width": 1600, "height": 1200,
        }
    return channel

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(THUMBS_DIR, exist_ok=True)
    cleanup_old_thumbs(days=3)
    print(f"Gio VN: {now_vn().strftime('%H:%M %d/%m/%Y')}")
    print("Lay tran dau tu XoiChe TV (xoiche.tv — domain moi cua Phaohoa)...")

    grouped = get_grouped_matches()
    matches = list(grouped.values())
    matches.sort(key=lambda m: (0 if m["is_live"] else 1, m["time_sort"]))

    live_cnt = sum(1 for m in matches if m["is_live"])
    print(f"\nTong: {len(matches)} | LIVE: {live_cnt} | Sap: {len(matches) - live_cnt}\n")

    cate_channels = {c: [] for c in CATE_ORDER}

    for i, m in enumerate(matches):
        safe_id = re.sub(r'[^A-Za-z0-9_-]', '-', str(m["match_id"]))
        tag = "LIVE" if m["is_live"] else "SAP"
        blv = ", ".join(m["blvs_dict"].keys()) if m["blvs_dict"] else "Khong co link"
        print(f"[{tag} {i+1}/{len(matches)}] {m['name']} ({m['time']} {m['date']}) | BLV: {blv}")

        thumb_path = make_thumbnail(m, safe_id)
        ck = m.get("logo_a", "") + m.get("logo_b", "") + THUMB_VERSION
        lh = hashlib.md5(ck.encode()).hexdigest()[:8]
        thumb_url = f"{REPO_RAW}/{thumb_path}?v={lh}" if REPO_RAW else ""

        ch = build_channel(m, safe_id, thumb_url)
        cate_channels.setdefault(m["cate_type"], []).append(ch)
        time.sleep(0.1)

    groups = []
    for ct in CATE_ORDER:
        chs = cate_channels.get(ct, [])
        if not chs: continue
        label = CATE_MAP.get(ct, "🏅 Thể Thao")
        lc = sum(1 for c in chs if c.get("org_metadata", {}).get("is_live", False))
        name = f"{label} ({lc} LIVE)" if lc > 0 else label
        groups.append({
            "id": f"cate_{ct}", "name": name, "display": "vertical",
            "grid_number": 2, "enable_detail": False, "channels": chs,
        })

    for ct, chs in cate_channels.items():
        if ct not in CATE_ORDER and chs:
            lc = sum(1 for c in chs if c.get("org_metadata", {}).get("is_live", False))
            groups.append({
                "id": f"cate_{ct}", "name": f"🏅 Thể Thao ({lc} LIVE)" if lc > 0 else "🏅 Thể Thao",
                "display": "vertical", "grid_number": 2, "enable_detail": False, "channels": chs,
            })

    output = {
        "id": "xoiche",   # doi tu "phaohoa" — doi lai "phaohoa" neu app dang truy van id cu
        "url": BASE_URL,
        "name": "Xôi Chè TV",
        "color": "#00a86b",
        "grid_number": 3,
        "image": {"type": "cover", "url": f"{BASE_URL}/brand/hero.webp"},
        "groups": groups,
    }

    staging = "output_staging.json"
    with open(staging, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    total = sum(len(g["channels"]) for g in groups)

    def norm(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.dumps(json.load(f), sort_keys=True, ensure_ascii=False)
        except Exception: return ""

    if norm("output.json") != norm(staging):
        os.replace(staging, "output.json")
        print(f"\n✅ Xong! {total} kenh, {len(groups)} mon the thao -> output.json (DA CAP NHAT)")
    else:
        os.remove(staging)
        print(f"\n✅ Xong! {total} kenh, {len(groups)} mon the thao -> Khong co thay doi")

if __name__ == "__main__":
    main()
