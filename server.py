#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日本二游监测 - 本地仪表盘服务
用法: python3 server.py            # 默认 http://127.0.0.1:8642
浏览器抓 YouTube/X 会被CORS拦截, 所以由本服务代抓并缓存。
接口:
  GET /                     仪表盘页面 (dashboard.html)
  GET /api/games            竞品名单 (config.json)
  GET /api/charts           日区畅销/免费榜 Top100 (缓存10分钟)
  GET /api/detail?app=<id>  单款App: 版本元数据+最新评论 (缓存10分钟)
  GET /api/youtube?ch=<id>  官方频道最新视频 (走代理, 缓存15分钟)
  GET /api/x?user=<handle>  官推最新推文 (nitter RSS 走代理, 缓存15分钟)
  GET /api/history?game=<cn> 本地快照里的排名历史 (data/snapshots)
  GET /api/digest/run?x=0|1 后台生成周报digest (x=1时实时抓官推, 调用monid付费)
  GET /api/digest/status    生成进度轮询 {building,log} / {done} / {error}
  GET /api/digest/list      历史周报列表 (reports/digest_*.txt)
  GET /api/digest/get?f=    读取某期周报全文
"""
import json, os, re, sys, time, threading, glob, math, subprocess
import urllib.request, urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(BASE, "config.json")))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8642
PROXY = CFG.get("proxy_for_youtube")
NITTER = "https://nitter.net"

_cache, _lock = {}, threading.Lock()


def cached(key, ttl, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        # 抓取失败时保留旧缓存, 避免用空数据覆盖
        if val or not _cache.get(key):
            _cache[key] = (now, val)
        else:
            val = _cache[key][1]
    return val


def fetch(url, proxy=None, timeout=25, headers=None):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers=h)
    return opener.open(req, timeout=timeout).read().decode("utf-8", "ignore")


# ---------- 数据抓取 ----------
def get_charts():
    def _do():
        out = {}
        for kind, key in [("topgrossingapplications", "grossing"), ("topfreeapplications", "free")]:
            try:
                raw = fetch(f"https://itunes.apple.com/jp/rss/{kind}/limit=200/genre=6014/json")
                entries = json.loads(raw)["feed"]["entry"]
                out[key] = {e["id"]["attributes"]["im:id"]: i for i, e in enumerate(entries, 1)}
            except Exception as ex:
                print("charts err", key, ex)
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
        except Exception as ex:
            print("lookup err", app_id, ex)
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
        except Exception as ex:
            print("reviews err", app_id, ex)
        return d
    return cached(f"detail:{app_id}", 600, _do)


def get_youtube(channel_id):
    def _do():
        try:
            raw = fetch(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}", proxy=PROXY)
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
        except Exception as ex:
            print("yt err", channel_id, ex)
            return []
    return cached(f"yt:{channel_id}", 900, _do)


def get_x(handle):
    def _do():
        try:
            raw = fetch(f"{NITTER}/{urllib.parse.quote(handle)}/rss", proxy=PROXY)
            from xml.etree import ElementTree as ET
            root = ET.fromstring(raw)
            out = []
            for it in root.findall(".//item")[:15]:
                title = (it.findtext("title") or "").strip()
                link = (it.findtext("link") or "").replace(NITTER, "https://x.com")
                pub = (it.findtext("pubDate") or "")[:22]
                m = re.search(r"/status/(\d+)", link)
                is_reply = title.startswith("R to ")
                out.append({"id": m.group(1) if m else None, "text": title[:200],
                            "url": link, "date": pub, "reply": is_reply})
            return out
        except Exception as ex:
            print("x err", handle, ex)
            return []
    return cached(f"x:{handle}", 900, _do)


# ---------- 推文互动量 (cdn.syndication.twimg.com, 无需登录) ----------
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
            d = json.loads(fetch(url, proxy=PROXY, timeout=15))
            media = d.get("mediaDetails") or []
            return {"likes": d.get("favorite_count"), "replies": d.get("conversation_count"),
                    "hasMedia": bool(media), "isVideo": any(m.get("type") == "video" for m in media)}
        except Exception:
            return None
    return cached(f"tw:{tid}", 3600, _do)


# ---------- 高曝光营销动作聚合 (跨全部竞品: 推文按点赞、视频按观看数排序) ----------
# 全量构建需2-5分钟, 若同步返回会挂死浏览器连接(同域最多6个并发),
# 因此改为后台线程构建, /api/buzz 立即返回 {building, progress} 或成品
_buzz = {"state": "idle", "progress": "", "data": None, "ts": 0}
_buzz_lock = threading.Lock()

# 营销动作分类 (对齐【异环】海外宣发追踪.xlsx的"活动类别"体系, 按日文关键词识别, 先命中先归类)
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


def _build_buzz(top=40):
    tweets, videos = [], []
    games = [g for g in CFG["games"] if g.get("x") or g.get("yt_channel")]
    for i, g in enumerate(games):
        with _buzz_lock:
            _buzz["progress"] = f"{i+1}/{len(games)} {g['cn']}"
        if g.get("x"):
            per_game = 0
            for t in get_x(g["x"]):
                if t.get("reply") or not t.get("id") or per_game >= 6:
                    continue
                st = get_tweet_stats(t["id"])
                per_game += 1
                time.sleep(0.2)
                tweets.append({"game": g["cn"], "text": t["text"], "url": t["url"], "date": t["date"],
                               "cat": classify_mkt(t["text"]),
                               "likes": (st or {}).get("likes"), "replies": (st or {}).get("replies"),
                               "video": (st or {}).get("isVideo", False)})
            time.sleep(0.5)
        if g.get("yt_channel"):
            for v in get_youtube(g["yt_channel"]):
                videos.append({"game": g["cn"], "title": v["title"], "videoId": v["videoId"],
                               "cat": classify_mkt(v["title"]),
                               "published": v["published"], "views": v.get("views")})
    tweets.sort(key=lambda r: -(r.get("likes") or 0))
    videos.sort(key=lambda r: -(r.get("views") or 0))
    with _buzz_lock:
        _buzz.update({"state": "done",
                      "data": {"tweets": tweets[:top * 3], "videos": videos[:top * 3], "catLabels": MKT_LABELS},
                      "ts": time.time(), "progress": ""})


def get_buzz():
    with _buzz_lock:
        fresh = _buzz["data"] and time.time() - _buzz["ts"] < 1800
        if _buzz["state"] == "building":
            return {"building": True, "progress": _buzz["progress"], "data": _buzz["data"]}
        if fresh:
            return _buzz["data"]
        _buzz["state"] = "building"
        _buzz["progress"] = "启动中"
    threading.Thread(target=_build_buzz, daemon=True).start()
    return {"building": True, "progress": "启动中", "data": _buzz["data"]}


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


# ---------- 版本Roadmap ----------
# App Store只暴露"当前版本", 历史靠本地滚动积累:
#   data/versions.json  appId -> [{version, date, notes}]
# 来源: ①每次调用时的批量lookup ②每日快照里的meta.version ③config.json里手动登记的官宣节点(roadmap字段)
# 预测: 上次更新日 + cadence_days(config配置, 或从已积累历史的间隔中位数推算)
VERSIONS_PATH = os.path.join(BASE, "data", "versions.json")


def _merge_version(hist, app_id, version, vdate, notes=""):
    if not version or not vdate:
        return False
    lst = hist.setdefault(str(app_id), [])
    if any(e["version"] == version for e in lst):
        return False
    lst.append({"version": version, "date": vdate, "notes": (notes or "")[:400]})
    lst.sort(key=lambda e: e["date"])
    return True


def _infer_cadence(entries):
    """从已积累的更新记录推周期: 取相邻间隔(>=10天, 排除热修)的中位数"""
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
            hist = json.load(open(VERSIONS_PATH))
        except Exception:
            hist = {}
        changed = False

        # ① 批量lookup当前版本 (一次请求全部appId)
        ids = [str(g["appId"]) for g in CFG["games"] if g.get("appId")]
        try:
            raw = fetch("https://itunes.apple.com/lookup?id=" + ",".join(ids) + "&country=jp&lang=ja_jp")
            for r in json.loads(raw).get("results", []):
                changed |= _merge_version(hist, r["trackId"], r.get("version"),
                                          (r.get("currentVersionReleaseDate") or "")[:10],
                                          r.get("releaseNotes"))
        except Exception as ex:
            print("roadmap lookup err", ex)

        # ② 从每日快照回填 (快照按cn名存, 映射回appId)
        cn2id = {g["cn"]: g["appId"] for g in CFG["games"] if g.get("appId")}
        for p in sorted(glob.glob(os.path.join(BASE, CFG["data_dir"], "*.json"))):
            try:
                s = json.load(open(p))
                for cn, rec in s.get("games", {}).items():
                    m = rec.get("meta") or {}
                    if cn in cn2id:
                        changed |= _merge_version(hist, cn2id[cn], m.get("version"),
                                                  m.get("versionDate"), m.get("releaseNotes"))
            except Exception:
                pass

        if changed:
            os.makedirs(os.path.dirname(VERSIONS_PATH), exist_ok=True)
            json.dump(hist, open(VERSIONS_PATH, "w"), ensure_ascii=False, indent=1)

        # ③ 组装输出
        from datetime import date as _d, timedelta
        try:  # 营销事件存档 (digest.py AI甄别的线下/联动活动)
            evs = json.load(open(os.path.join(BASE, "data", "events.json")))
        except Exception:
            evs = []
        ev_by_game = {}
        for e in evs:
            ev_by_game.setdefault(e.get("game"), []).append(e)
        out = []
        for g in CFG["games"]:
            app_id = g.get("appId")
            entries = hist.get(str(app_id), []) if app_id else []
            cad = g.get("cadence_days") or _infer_cadence(entries)
            nxt = None
            if entries and cad:
                last = _d.fromisoformat(entries[-1]["date"])
                nd = last + timedelta(days=cad)
                # 若推算日已过(数据未及时更新), 顺延到今天之后的下一个周期
                today = _d.today()
                while nd < today:
                    nd += timedelta(days=cad)
                nxt = nd.isoformat()
            out.append({
                "cn": g["cn"], "jp": g["jp"], "appId": app_id,
                "origin": g.get("origin"), "note": g.get("note"),
                "history": entries[-12:],
                "cadence": cad, "cadenceSource": "config" if g.get("cadence_days") else ("inferred" if cad else None),
                "nextEstimate": nxt,
                "manual": g.get("roadmap", []),  # 手动登记的官宣节点 [{date,label,est}]
                "events": ev_by_game.get(g["cn"], []),  # 营销事件 [{date,type,desc,url}]
            })
        return out
    # 缓存key带上events.json的mtime, 文件被外部修改(如命令行跑digest)时自动感知
    ev_path = os.path.join(BASE, "data", "events.json")
    ev_ver = str(int(os.path.getmtime(ev_path))) if os.path.exists(ev_path) else "0"
    return cached(f"roadmap:v{ev_ver}", 3600, _do)


# ---------- 周报生成 (digest.py) + 存档 ----------
# 同 buzz 的后台线程模式: /api/digest/run 触发, /api/digest/status 轮询日志
_digest = {"state": "idle", "log": "", "err": None}
_digest_lock = threading.Lock()


def _run_digest(flags):
    try:
        proc = subprocess.Popen([sys.executable, os.path.join(BASE, "digest.py")] + flags,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, cwd=BASE)
        for line in proc.stdout:
            with _digest_lock:
                _digest["log"] = (_digest["log"] + line)[-4000:]
        proc.wait()
        with _digest_lock:
            _digest["state"] = "done" if proc.returncode == 0 else "error"
            if proc.returncode != 0:
                _digest["err"] = f"digest.py 退出码 {proc.returncode}"
        if proc.returncode == 0:
            with _lock:  # 事件可能有新增, 让roadmap缓存失效
                _cache.pop("roadmap", None)
    except Exception as ex:
        with _digest_lock:
            _digest.update({"state": "error", "err": str(ex)})


def run_digest(with_x=False):
    with _digest_lock:
        if _digest["state"] == "building":
            return {"building": True, "log": _digest["log"]}
        _digest.update({"state": "building", "log": "", "err": None})
    threading.Thread(target=_run_digest, args=(["--x"] if with_x else [],), daemon=True).start()
    return {"building": True, "log": "启动中...\n"}


def digest_status():
    with _digest_lock:
        st = dict(_digest)
    if st["state"] == "building":
        return {"building": True, "log": st["log"]}
    if st["state"] == "error":
        return {"error": st["err"] or "未知错误", "log": st["log"]}
    return {"done": st["state"] == "done", "log": st["log"]}


def list_digests():
    out = []
    for p in sorted(glob.glob(os.path.join(BASE, CFG["report_dir"], "digest_*.txt")), reverse=True):
        f = os.path.basename(p)
        out.append({"file": f, "date": f[7:-4]})
    return out


def read_digest(f):
    if not re.fullmatch(r"digest_\d{4}-\d{2}-\d{2}\.txt", f or ""):
        return {"error": "非法文件名"}
    p = os.path.join(BASE, CFG["report_dir"], f)
    if not os.path.exists(p):
        return {"error": "文件不存在"}
    return {"file": f, "text": open(p).read()}


# ---------- HTTP ----------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        try:
            if u.path == "/" or u.path == "/dashboard.html":
                html = open(os.path.join(BASE, "dashboard.html"), "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
            elif u.path == "/api/games":
                self._json(CFG["games"])
            elif u.path == "/api/charts":
                self._json(get_charts())
            elif u.path == "/api/detail":
                self._json(get_detail(int(q["app"])))
            elif u.path == "/api/youtube":
                self._json(get_youtube(q["ch"]))
            elif u.path == "/api/x":
                self._json(get_x(q["user"]))
            elif u.path == "/api/buzz":
                self._json(get_buzz())
            elif u.path == "/api/history":
                self._json(get_history(q["game"]))
            elif u.path == "/api/roadmap":
                self._json(get_roadmap())
            elif u.path == "/api/digest/run":
                self._json(run_digest(q.get("x") == "1"))
            elif u.path == "/api/digest/status":
                self._json(digest_status())
            elif u.path == "/api/digest/list":
                self._json(list_digests())
            elif u.path == "/api/digest/get":
                self._json(read_digest(q.get("f", "")))
            else:
                self._json({"error": "not found"}, 404)
        except BrokenPipeError:
            pass
        except Exception as ex:
            try:
                self._json({"error": str(ex)}, 500)
            except Exception:
                pass


if __name__ == "__main__":
    print(f"日本二游监测面板: http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
