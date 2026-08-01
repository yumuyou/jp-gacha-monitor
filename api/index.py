# -*- coding: utf-8 -*-
"""
Vercel serverless 版 API — 与本地 server.py 同一套接口。
差异:
  - 无常驻进程: buzz 不做后台全量构建, 改为 /api/buzz?game=<cn> 按游戏同步返回, 前端逐个请求
  - 无持久磁盘: roadmap 的版本积累以仓库里提交的 data/versions.json 为底, 运行时合并当前版本(不落盘)
  - 部署在海外节点: YouTube/nitter/twimg 直连, 不走 proxy_for_youtube
"""
import json, os, re, glob, math, time
import urllib.request, urllib.parse
from http.server import BaseHTTPRequestHandler

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
CFG = json.load(open(os.path.join(BASE, "config.json")))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
NITTER = "https://nitter.net"

_cache = {}  # 模块级缓存, 函数实例保温期间有效


def cached(key, ttl, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    if val or not _cache.get(key):
        _cache[key] = (now, val)
    else:
        val = _cache[key][1]
    return val


def fetch(url, timeout=20, headers=None):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")


def get_charts():
    def _do():
        out = {}
        for kind, key in [("topgrossingapplications", "grossing"), ("topfreeapplications", "free")]:
            try:
                raw = fetch(f"https://itunes.apple.com/jp/rss/{kind}/limit=200/genre=6014/json")
                entries = json.loads(raw)["feed"]["entry"]
                out[key] = {e["id"]["attributes"]["im:id"]: i for i, e in enumerate(entries, 1)}
            except Exception:
                out[key] = {}
        return out
    return cached("charts", 600, _do)


def get_detail(app_id):
    def _do():
        d = {"meta": None, "reviews": []}
        try:
            raw = fetch(f"https://itunes.apple.com/lookup?id={app_id}&country=jp&lang=ja_jp")
            rs = json.loads(raw)["results"]
            if rs:
                r = rs[0]
                d["meta"] = {
                    "name": r.get("trackName"), "version": r.get("version"),
                    "versionDate": (r.get("currentVersionReleaseDate") or "")[:10],
                    "releaseNotes": (r.get("releaseNotes") or "")[:600],
                    "rating": r.get("averageUserRating"), "ratingCount": r.get("userRatingCount"),
                    "icon": r.get("artworkUrl100"),
                }
        except Exception:
            pass
        try:
            url = ("https://itunes.apple.com/WebObjects/MZStore.woa/wa/userReviewsRow"
                   f"?cc=jp&id={app_id}&displayable-kind=11&startIndex=0&endIndex=30&sort=0&appVersion=all")
            raw = fetch(url, headers={"X-Apple-Store-Front": "143462-9,29"})
            for r in json.loads(raw).get("userReviewList", []):
                d["reviews"].append({
                    "rating": r.get("rating"), "title": (r.get("title") or "")[:80],
                    "body": (r.get("body") or "")[:280], "date": (r.get("date") or "")[:10],
                    "votes": r.get("voteCount"),
                })
        except Exception:
            pass
        return d
    return cached(f"detail:{app_id}", 600, _do)


def get_youtube(channel_id):
    def _do():
        try:
            raw = fetch(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
            from xml.etree import ElementTree as ET
            root = ET.fromstring(raw)
            ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
                  "media": "http://search.yahoo.com/mrss/"}
            out = []
            for e in root.findall("a:entry", ns)[:12]:
                stats = e.find("media:group/media:community/media:statistics", ns)
                thumb = e.find("media:group/media:thumbnail", ns)
                out.append({
                    "videoId": e.find("yt:videoId", ns).text,
                    "title": e.find("a:title", ns).text[:110],
                    "published": e.find("a:published", ns).text[:10],
                    "views": int(stats.get("views")) if stats is not None else None,
                    "thumb": thumb.get("url") if thumb is not None else None,
                })
            return out
        except Exception:
            return []
    return cached(f"yt:{channel_id}", 900, _do)


def get_x(handle):
    def _do():
        try:
            raw = fetch(f"{NITTER}/{urllib.parse.quote(handle)}/rss")
            from xml.etree import ElementTree as ET
            root = ET.fromstring(raw)
            out = []
            for it in root.findall(".//item")[:15]:
                title = (it.findtext("title") or "").strip()
                link = (it.findtext("link") or "").replace(NITTER, "https://x.com")
                pub = (it.findtext("pubDate") or "")[:22]
                m = re.search(r"/status/(\d+)", link)
                out.append({"id": m.group(1) if m else None, "text": title[:200],
                            "url": link, "date": pub, "reply": title.startswith("R to ")})
            return out
        except Exception:
            return []
    return cached(f"x:{handle}", 900, _do)


def _tweet_token(tid):
    n = (int(tid) / 1e15) * math.pi
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    i, frac = int(n), n - int(n)
    s = "" if i else "0"
    while i:
        s = digits[i % 36] + s
        i //= 36
    for _ in range(12):
        frac *= 36
        d = int(frac)
        s += digits[d]
        frac -= d
    return s.replace("0", "").replace(".", "")


def get_tweet_stats(tid):
    def _do():
        try:
            url = f"https://cdn.syndication.twimg.com/tweet-result?id={tid}&token={_tweet_token(tid)}&lang=ja"
            d = json.loads(fetch(url, timeout=12))
            media = d.get("mediaDetails") or []
            return {"likes": d.get("favorite_count"), "replies": d.get("conversation_count"),
                    "hasMedia": bool(media), "isVideo": any(m.get("type") == "video" for m in media)}
        except Exception:
            return None
    return cached(f"tw:{tid}", 3600, _do)


MKT_CATS = [
    ("kuji",      "转抽/抽奖",      ["フォロー", "リポスト", "RTキャンペ", "抽選", "プレゼントキャンペーン",
                                     "アマギフ", "Amazonギフト", "ギフト券", "が当たる", "山分け"]),
    ("collab",    "联动/合作",      ["コラボ", "タイアップ", "連動企画"]),
    ("offline",   "线下活动/硬广",  ["秋葉原", "渋谷", "新宿", "池袋", "広告", "ポップアップ", "POP UP", "POPUP",
                                     "カフェ", "会場", "TGS", "東京ゲームショウ", "ビジョン", "サイネージ",
                                     "リアルイベント", "献血", "ローソン", "ファミマ", "セブン"]),
    ("live",      "直播/前瞻番组",  ["生放送", "生配信", "特別番組", "配信決定", "ニコ生", "プレミア公開",
                                     "放送", "予告番組", "新情報発表"]),
    ("kol",       "声优/KOL/艺人",  ["声優", "VTuber", "ホロライブ", "にじさんじ", "実況", "ご出演",
                                     "インフルエンサー", "アンバサダー"]),
    ("milestone", "里程碑/周年",    ["周年", "アニバーサリー", "突破", "ハーフアニバ", "記念して"]),
    ("ugc",       "二创/贺图/征集", ["ファンアート", "イラストコンテスト", "二次創作", "描いて",
                                     "コンテスト", "募集", "投稿キャンペーン", "生誕祭", "誕生日",
                                     "記念イラスト", "お祝いイラスト"]),
    ("music",     "音乐/主题曲",    ["主題歌", "テーマソング", "MV", "サウンドトラック", "OST", "楽曲", "歌唱"]),
    ("preregist", "事前登録/预约",  ["事前登録", "事前予約", "予約受付"]),
    ("update",    "版本/游戏情报",  ["アップデート", "バージョン", "Ver.", "メンテナンス", "新キャラクター",
                                     "新イベント", "ガチャ", "実装", "配信スタート", "リリース"]),
]


def classify_mkt(text):
    t = text or ""
    for key, label, pats in MKT_CATS:
        if any(p in t for p in pats):
            return key
    return "other"


MKT_LABELS = {k: v for k, v, _ in MKT_CATS}
MKT_LABELS["other"] = "其他/官方公告"


def get_buzz_game(cn):
    """单款游戏的营销动作 (serverless: 每次请求处理一款, 前端逐个调用)"""
    g = next((x for x in CFG["games"] if x["cn"] == cn), None)
    if not g:
        return {"error": "unknown game"}
    def _do():
        tweets, videos = [], []
        if g.get("x"):
            per_game = 0
            for t in get_x(g["x"]):
                if t.get("reply") or not t.get("id") or per_game >= 6:
                    continue
                st = get_tweet_stats(t["id"])
                per_game += 1
                tweets.append({"game": g["cn"], "text": t["text"], "url": t["url"], "date": t["date"],
                               "cat": classify_mkt(t["text"]),
                               "likes": (st or {}).get("likes"), "replies": (st or {}).get("replies"),
                               "video": (st or {}).get("isVideo", False)})
        if g.get("yt_channel"):
            for v in get_youtube(g["yt_channel"]):
                videos.append({"game": g["cn"], "title": v["title"], "videoId": v["videoId"],
                               "cat": classify_mkt(v["title"]),
                               "published": v["published"], "views": v.get("views")})
        return {"tweets": tweets, "videos": videos}
    return cached(f"buzz:{cn}", 1800, _do)


def get_history(game_cn):
    out = []
    for p in sorted(glob.glob(os.path.join(BASE, CFG["data_dir"], "*.json"))):
        try:
            s = json.load(open(p))
            g = s["games"].get(game_cn) or {}
            out.append({"date": s["date"], "grossing": g.get("grossingRank"), "free": g.get("freeRank")})
        except Exception:
            pass
    return out[-30:]


def _infer_cadence(entries):
    dates = sorted({e["date"] for e in entries})
    if len(dates) < 3:
        return None
    from datetime import date as _d
    ds = [_d.fromisoformat(x) for x in dates]
    gaps = sorted((b - a).days for a, b in zip(ds, ds[1:]) if (b - a).days >= 10)
    return gaps[len(gaps) // 2] if gaps else None


def get_roadmap():
    def _do():
        try:
            hist = json.load(open(os.path.join(BASE, "data", "versions.json")))
        except Exception:
            hist = {}
        ids = [str(g["appId"]) for g in CFG["games"] if g.get("appId")]
        try:
            raw = fetch("https://itunes.apple.com/lookup?id=" + ",".join(ids) + "&country=jp&lang=ja_jp")
            for r in json.loads(raw).get("results", []):
                lst = hist.setdefault(str(r["trackId"]), [])
                v, vd = r.get("version"), (r.get("currentVersionReleaseDate") or "")[:10]
                if v and vd and not any(e["version"] == v for e in lst):
                    lst.append({"version": v, "date": vd, "notes": (r.get("releaseNotes") or "")[:400]})
                    lst.sort(key=lambda e: e["date"])
        except Exception:
            pass
        from datetime import date as _d, timedelta
        out = []
        for g in CFG["games"]:
            app_id = g.get("appId")
            entries = hist.get(str(app_id), []) if app_id else []
            cad = g.get("cadence_days") or _infer_cadence(entries)
            nxt = None
            if entries and cad:
                nd = _d.fromisoformat(entries[-1]["date"]) + timedelta(days=cad)
                today = _d.today()
                while nd < today:
                    nd += timedelta(days=cad)
                nxt = nd.isoformat()
            out.append({
                "cn": g["cn"], "jp": g["jp"], "appId": app_id,
                "origin": g.get("origin"), "note": g.get("note"),
                "history": entries[-12:],
                "cadence": cad,
                "cadenceSource": "config" if g.get("cadence_days") else ("inferred" if cad else None),
                "nextEstimate": nxt,
                "manual": g.get("roadmap", []),
            })
        return out
    return cached("roadmap", 3600, _do)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        path = u.path
        try:
            if path == "/api/games":
                body = CFG["games"]
            elif path == "/api/charts":
                body = get_charts()
            elif path == "/api/detail":
                body = get_detail(int(q["app"]))
            elif path == "/api/youtube":
                body = get_youtube(q["ch"])
            elif path == "/api/x":
                body = get_x(q["user"])
            elif path == "/api/buzz":
                if q.get("game"):
                    body = get_buzz_game(q["game"])
                else:
                    # 无game参数 → 告知前端走逐游戏模式
                    body = {"perGame": True, "catLabels": MKT_LABELS,
                            "games": [g["cn"] for g in CFG["games"] if g.get("x") or g.get("yt_channel")]}
            elif path == "/api/history":
                body = get_history(q["game"])
            elif path == "/api/roadmap":
                body = get_roadmap()
            else:
                self._send({"error": "not found"}, 404)
                return
            self._send(body)
        except Exception as ex:
            self._send({"error": str(ex)}, 500)

    def _send(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "s-maxage=300, stale-while-revalidate=600")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass
