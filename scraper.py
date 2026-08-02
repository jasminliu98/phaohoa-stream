import requests
import json
import hashlib
import re
import time
import os
from datetime import datetime, timezone, timedelta
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

VN_TZ = timezone(timedelta(hours=7))

BASE_URL = "https://phaohoa1.live"
API_BASE = f"{BASE_URL}/api"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer":   f"{BASE_URL}/",
    "Origin":    BASE_URL,
    "Accept":    "application/json, text/plain, */*",
    "Accept-Language": "vi-VN,vi;q=0.9,fr-FR;q=0.8,fr;q=0.7,en-US;q=0.6,en;q=0.5",
}

THUMBS_DIR    = "thumbs"
REPO_RAW      = os.environ.get("REPO_RAW", "")
THUMB_VERSION = "v2"

CATE_MAP = {
    "football":    "⚽ Bóng Đá",
    "bong-ro":     "🏀 Bóng Rổ",
    "tennis":      "🎾 Tennis",
    "bong-chuyen": "🏐 Bóng Chuyền",
    "esports":     "🎮 Esport",
    "cau-long":    "🏸 Cầu Lông",
    "boxing":      "🥊 Võ Thuật",
    "billiards":   "🎱 Billiards",
    "bong-ban":    "🏓 Bóng Bàn",
}
CATE_ORDER = ["football", "bong-ro", "tennis", "bong-chuyen", "esports",
              "cau-long", "boxing", "billiards", "bong-ban"]

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def now_vn() -> datetime:
    return datetime.now(tz=VN_TZ)

def make_id(text, prefix):
    return f"{prefix}-{hashlib.md5(text.encode()).hexdigest()[:10]}"

def full_url(path):
    if not path: return ""
    if path.startswith("http"): return path
    return f"{BASE_URL}{path}"

def fetch_image(url):
    try:
        res = requests.get(url, headers=HEADERS, timeout=8)
        res.raise_for_status()
        return Image.open(BytesIO(res.content)).convert("RGBA")
    except Exception:
        return None

def parse_start_time(s):
    if not s: return None
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=VN_TZ)
    except Exception:
        return None

def format_time_hhmm(dt):
    return dt.strftime("%H:%M") if dt else ""

def format_date_ddmm(dt):
    return dt.strftime("%d/%m") if dt else ""

def get_stream_type(url):
    if not url: return "hls"
    c = url.lower().split("?")[0]
    if c.endswith(".flv"): return "httpflv"
    if c.endswith(".mpd"): return "dash"
    if c.endswith(".mp4"): return "mp4"
    return "hls"

def is_within_24h(start_time_str, is_live=False):
    if is_live: return True
    dt = parse_start_time(start_time_str)
    if dt is None: return True
    now = now_vn()
    return (now - timedelta(hours=6)) <= dt <= (now + timedelta(hours=24))

def parse_time_sort(start_time_str):
    dt = parse_start_time(start_time_str)
    if dt:
        return dt.month * 10_000_000 + dt.day * 10_000 + dt.hour * 100 + dt.minute
    return 999_999_999

# ─────────────────────────────────────────────────────────────────────────────
# DATA EXTRACTOR
# ─────────────────────────────────────────────────────────────────────────────

def find_matches_in_data(data):
    """Tìm đệ quy mọi danh sách chứa các dictionary có key 'slug' và 'home_team_name'"""
    matches = []
    if isinstance(data, list):
        for item in data:
            matches.extend(find_matches_in_data(item))
    elif isinstance(data, dict):
        if "slug" in data and ("home_team_name" in data or "home_team" in data):
            matches.append(data)
        else:
            for v in data.values():
                matches.extend(find_matches_in_data(v))
    return matches

# ─────────────────────────────────────────────────────────────────────────────
# NUXT DATA PARSER
# ─────────────────────────────────────────────────────────────────────────────

def parse_nuxt_data(html_text):
    m = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', html_text, re.DOTALL)
    if not m: return None
    try:
        raw = json.loads(m.group(1))
    except Exception:
        return None

    REACTIVE = {"ShallowReactive", "Reactive", "ShallowRef", "Ref"}

    def resolve(idx, depth=0):
        if depth > 30: return None
        if not isinstance(idx, int) or idx < 0 or idx >= len(raw): return idx
        item = raw[idx]

        if isinstance(item, (str, bool, int, float)): return item
        if item is None: return None
        if isinstance(item, list):
            if len(item) >= 1 and isinstance(item[0], str) and item[0] in REACTIVE:
                return resolve(item[1], depth + 1) if len(item) >= 2 else None
            if len(item) == 1 and isinstance(item[0], str) and item[0] == "Set":
                return []
            out = []
            for x in item:
                if isinstance(x, bool): out.append(x)
                elif isinstance(x, int): out.append(resolve(x, depth + 1))
                elif isinstance(x, (str, float)) or x is None: out.append(x)
            return out
        if isinstance(item, dict):
            out = {}
            for k, v in item.items():
                if isinstance(v, bool): out[k] = v
                elif isinstance(v, int): out[k] = resolve(v, depth + 1)
                elif isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and v[0] in REACTIVE:
                    out[k] = resolve(v[1], depth + 1)
                else: out[k] = v
            return out
        return item

    try:
        return resolve(0)
    except Exception:
        return None

# ─────────────────────────────────────────────────────────────────────────────
# FETCH SOURCES
# ─────────────────────────────────────────────────────────────────────────────

def fetch_matches_from_html(page_url):
    try:
        res = requests.get(page_url, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            print(f"  ! HTML {page_url} ma loi {res.status_code}")
            return []
        if "id=\"__NUXT_DATA__\"" not in res.text:
            print(f"  ! HTML {page_url} khong co __NUXT_DATA__")
            return []
        nuxt = parse_nuxt_data(res.text)
        matches = find_matches_in_data(nuxt) if nuxt else []
        print(f"  + HTML {page_url} : {len(matches)} tran")
        return matches
    except Exception as e:
        print(f"  ! Loi HTML {page_url}: {e}")
        return []

def fetch_matches_from_api():
    """Lấy danh sách trận qua API backend theo ngày"""
    today = now_vn().strftime("%Y-%m-%d")
    tomorrow = (now_vn() + timedelta(days=1)).strftime("%Y-%m-%d")
    urls = [
        f"{API_BASE}/matches/?date={today}",
        f"{API_BASE}/matches/?date={tomorrow}",
        f"{API_BASE}/matches/",
    ]
    all_m = []
    for url in urls:
        try:
            res = requests.get(url, headers=HEADERS, timeout=15)
            if res.status_code == 200:
                data = res.json()
                m = find_matches_in_data(data)
                if m:
                    all_m.extend(m)
        except Exception:
            pass
    if all_m:
        print(f"  + API backend: {len(all_m)} tran")
    return all_m

def fetch_match_detail(slug):
    try:
        res = requests.get(f"{API_BASE}/matches/{slug}/", headers=HEADERS, timeout=10)
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass
    return None

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
    ACCENT = (220, 30, 40)
    draw.rectangle([(0, HEADER_H), (W, HEADER_H + 5)], fill=ACCENT)
    draw.rectangle([(0, H - FOOTER_H - 5), (W, H - FOOTER_H)], fill=ACCENT)

    FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    try:
        font_vs = ImageFont.truetype(FONT_BOLD, 160)
        font_time = ImageFont.truetype(FONT_BOLD, 100)
        font_team = ImageFont.truetype(FONT_BOLD, 58)
    except Exception:
        font_vs = font_time = font_team = ImageFont.load_default()

    content_top = HEADER_H + 5
    content_bot = H - FOOTER_H - 5
    content_h = content_bot - content_top

    logo_size, name_h, time_h = 360, 120, 110
    gap_ln, gap_nt = 40, 60
    total_h = logo_size + gap_ln + name_h + gap_nt + time_h
    block_top = content_top + (content_h - total_h) // 2

    logo_y = block_top
    name_block_y = logo_y + logo_size + gap_ln
    name_center = name_block_y + name_h // 2
    time_y = name_block_y + name_h + gap_nt + time_h // 2

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
                    x = cx - logo_size // 2
                    bg.paste(r, (x, logo_y), r)
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

    if match.get("league"):
        lt = match["league"].upper()
        fs = 62
        f = None
        while fs >= 28:
            try: f = ImageFont.truetype(FONT_BOLD, fs)
            except Exception: f = ImageFont.load_default()
            if draw.textbbox((0, 0), lt, font=f)[2] <= W - 60: break
            fs -= 3
        draw.text((W // 2, HEADER_H // 2), lt, fill=(255, 255, 255), font=f, anchor="mm")

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
# GROUP MATCHES
# ─────────────────────────────────────────────────────────────────────────────

def get_grouped_matches():
    all_matches = []
    
    # 1. Lấy từ HTML các trang chính
    html_pages = [
        f"{BASE_URL}/",
        f"{BASE_URL}/lich-truc-tiep",
        f"{BASE_URL}/ty-so"
    ]
    for url in html_pages:
        m = fetch_matches_from_html(url)
        if m: all_matches.extend(m)
            
    # 2. Nếu HTML không có (do bị chặn), gọi API backend
    if not all_matches:
        print("  -> HTML khong co tran, chuyen sang goi API backend...")
        api_m = fetch_matches_from_api()
        if api_m: all_matches.extend(api_m)

    print(f"  Tong tran goc: {len(all_matches)}")
    if not all_matches: return {}

    seen, unique = set(), []
    for m in all_matches:
        mid = m.get("id")
        if mid and mid not in seen:
            seen.add(mid)
            unique.append(m)

    grouped = {}
    for item in unique:
        match_id = str(item.get("id", ""))
        slug = item.get("slug", "")
        status = item.get("status", "")

        if not match_id or status == "finished": continue

        is_live = status in ("live", "half_time")
        sport_slug = item.get("sport_slug", "football")
        if sport_slug not in CATE_MAP: sport_slug = "football"

        team_a = (item.get("home_team_name") or "").strip()
        team_b = (item.get("away_team_name") or "").strip()
        if not team_a or not team_b: continue

        start_str = item.get("start_time", "")
        start_dt = parse_start_time(start_str)

        if not is_live and not is_within_24h(start_str): continue
        if item.get("requires_token", False): continue

        commentators = item.get("commentators") or []
        if not commentators and slug:
            detail = fetch_match_detail(slug)
            if detail: commentators = detail.get("commentators") or []

        blvs_dict = {}
        for c in commentators:
            if not isinstance(c, dict): continue
            cname = c.get("name") or "BLV"
            urls = []
            for k in ("stream_url", "backup_stream_url", "flv_stream_url"):
                v = c.get(k, "")
                if v and isinstance(v, str) and v.startswith("http"):
                    if v not in urls: urls.append(v)
            if urls:
                blvs_dict.setdefault(cname, [])
                for u in urls:
                    if u not in blvs_dict[cname]: blvs_dict[cname].append(u)

        primary = item.get("primary_stream_url", "")
        if primary and isinstance(primary, str) and primary.startswith("http"):
            blvs_dict.setdefault("Server", [])
            if primary not in blvs_dict["Server"]: blvs_dict["Server"].append(primary)

        if not blvs_dict: continue

        grouped[match_id] = {
            "match_id": match_id,
            "cate_type": sport_slug,
            "name": f"{team_a} vs {team_b}",
            "time": format_time_hhmm(start_dt),
            "date": format_date_ddmm(start_dt),
            "time_sort": parse_time_sort(start_str),
            "team_a": team_a,
            "team_b": team_b,
            "logo_a": full_url(item.get("home_team_logo", "")),
            "logo_b": full_url(item.get("away_team_logo", "")),
            "league": item.get("tournament_name", ""),
            "is_live": is_live,
            "blvs_dict": blvs_dict,
            "status": status,
            "home_score": item.get("home_score", 0),
            "away_score": item.get("away_score", 0),
        }

    return grouped

# ─────────────────────────────────────────────────────────────────────────────
# BUILD CHANNEL JSON
# ─────────────────────────────────────────────────────────────────────────────

def build_channel(match, match_id_safe, thumb_url=""):
    uid = make_id(match_id_safe, "ph")
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
            "name": "PhaohoaTV",
            "contents": [{
                "id": ct_id,
                "name": match["name"],
                "streams": [{"id": st_id, "name": "PH", "stream_links": stream_links}],
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
    print("Lay tran dau tu PhaohoaTV...")

    grouped = get_grouped_matches()
    matches = list(grouped.values())
    matches.sort(key=lambda m: (0 if m["is_live"] else 1, m["time_sort"]))

    live_cnt = sum(1 for m in matches if m["is_live"])
    print(f"Tong: {len(matches)} | LIVE: {live_cnt} | Sap: {len(matches) - live_cnt}\n")

    cate_channels = {c: [] for c in CATE_ORDER}

    for i, m in enumerate(matches):
        safe_id = m["match_id"].replace(":", "-").replace("/", "-")
        tag = "LIVE" if m["is_live"] else "SAP"
        blv = ", ".join(m["blvs_dict"].keys()) if m["blvs_dict"] else "Khong co link"
        print(f"[{tag} {i+1}/{len(matches)}] {m['name']} ({m['time']} {m['date']}) | BLV: {blv}")

        thumb_path = make_thumbnail(m, safe_id)
        ck = m.get("logo_a", "") + m.get("logo_b", "") + THUMB_VERSION
        lh = hashlib.md5(ck.encode()).hexdigest()[:8]
        thumb_url = f"{REPO_RAW}/{thumb_path}?v={lh}" if REPO_RAW else ""

        ch = build_channel(m, safe_id, thumb_url)
        ct = m["cate_type"]
        cate_channels.setdefault(ct, []).append(ch)
        time.sleep(0.15)

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
        "id": "phaohoa",
        "url": BASE_URL,
        "name": "PhaohoaTV",
        "color": "#dc1e28",
        "grid_number": 3,
        "image": {"type": "cover", "url": f"{BASE_URL}/images/logo.png"},
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
