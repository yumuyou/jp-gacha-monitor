#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日本二游监测 - 每日快照
抓取: ①日区畅销榜/免费榜排名 ②App版本与评分 ③最新用户评论 ④官方YouTube动态
产出: data/snapshots/YYYY-MM-DD.json  (每天一份, 供周报聚合)
用法: python3 snapshot.py
"""
import json, os, re, sys, time, urllib.request, urllib.parse
from datetime import datetime, date

BASE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(BASE, "config.json")))
# 本机私密配置 (API key等, 不进git): config.local.json 覆盖合并
_local = os.path.join(BASE, "config.local.json")
if os.path.exists(_local):
    CFG.update(json.load(open(_local)))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"


def fetch(url, proxy=None, timeout=25, retries=2):
    for i in range(retries + 1):
        try:
            if proxy:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
            else:
                opener = urllib.request.build_opener()
            opener.addheaders = [("User-Agent", UA)]
            return opener.open(url, timeout=timeout).read().decode("utf-8", "ignore")
        except Exception as e:
            if i == retries:
                print(f"  ! fetch失败 {url[:80]} : {e}", file=sys.stderr)
                return None
            time.sleep(2)


# ---------- ① 日区榜单 (App Store RSS, 游戏类 genre=6014, top200) ----------
def get_charts():
    charts = {}
    for kind, key in [("topgrossingapplications", "grossing"), ("topfreeapplications", "free")]:
        url = f"https://itunes.apple.com/jp/rss/{kind}/limit=200/genre=6014/json"
        raw = fetch(url)
        ranks = {}
        if raw:
            try:
                entries = json.loads(raw)["feed"]["entry"]
                for i, e in enumerate(entries, 1):
                    ranks[int(e["id"]["attributes"]["im:id"])] = i
            except Exception as e:
                print(f"  ! 榜单解析失败 {key}: {e}", file=sys.stderr)
        charts[key] = ranks
        print(f"  榜单[{key}] 取得 {len(ranks)} 条")
    return charts


# ---------- ② App元数据: 版本/更新说明/评分 ----------
def get_app_meta(app_id):
    raw = fetch(f"https://itunes.apple.com/lookup?id={app_id}&country=jp&lang=ja_jp")
    if not raw:
        return None
    try:
        rs = json.loads(raw)["results"]
        if not rs:
            return None
        r = rs[0]
        return {
            "version": r.get("version"),
            "versionDate": (r.get("currentVersionReleaseDate") or "")[:10],
            "releaseNotes": (r.get("releaseNotes") or "")[:500],
            "rating": r.get("averageUserRating"),
            "ratingCount": r.get("userRatingCount"),
        }
    except Exception:
        return None


# ---------- ③ 最新用户评论 (日区, 最新50条) ----------
# 使用 iTunes MZStore userReviewsRow 接口 (RSS版customerreviews已不稳定, 长期返回空feed)
# sort=0 按最新排序; X-Apple-Store-Front 143462 = 日本
def get_reviews(app_id, count=50):
    url = ("https://itunes.apple.com/WebObjects/MZStore.woa/wa/userReviewsRow"
           f"?cc=jp&id={app_id}&displayable-kind=11&startIndex=0&endIndex={count}&sort=0&appVersion=all")
    for i in range(3):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "X-Apple-Store-Front": "143462-9,29"})
            d = json.load(urllib.request.urlopen(req, timeout=25))
            out = []
            for r in d.get("userReviewList", []):
                out.append({
                    "id": str(r.get("userReviewId")),
                    "rating": r.get("rating"),
                    "title": (r.get("title") or "")[:80],
                    "body": (r.get("body") or "")[:300],
                    "date": (r.get("date") or "")[:10],
                    "votes": r.get("voteCount"),
                })
            return out
        except Exception as ex:
            if i == 2:
                print(f"  ! 评论抓取失败 {app_id}: {ex}", file=sys.stderr)
            time.sleep(3)
    return []


# ---------- ④ 官方YouTube最新视频 (Data API优先, RSS兜底) ----------
def get_youtube_api(channel_id):
    """用 YouTube Data API v3 抓频道最新视频. 需要 config.local.json 里的 youtube_api_key.
    返回 None 表示无 key/调用失败(让调用方兜底RSS), 返回 [] 表示频道确实没视频."""
    key = CFG.get("youtube_api_key") or os.environ.get("YOUTUBE_API_KEY")
    if not key:
        return None
    proxy = CFG.get("proxy_for_youtube")
    # ① search.list: 频道最新15个视频 (order=date)
    url = (f"https://www.googleapis.com/youtube/v3/search"
           f"?part=snippet&channelId={channel_id}&order=date&maxResults=15&type=video&key={key}")
    raw = fetch(url, proxy=proxy)
    if not raw:
        return None
    try:
        items = json.loads(raw).get("items", [])
    except Exception:
        return None
    if not items:
        return []
    ids = [it["id"]["videoId"] for it in items if it.get("id", {}).get("videoId")]
    # ② videos.list: 批量拿观看数 (statistics)
    stats = {}
    if ids:
        url2 = (f"https://www.googleapis.com/youtube/v3/videos"
                f"?part=statistics&id={','.join(ids)}&key={key}")
        raw2 = fetch(url2, proxy=proxy)
        if raw2:
            try:
                stats = {v["id"]: v.get("statistics", {}).get("viewCount")
                         for v in json.loads(raw2).get("items", [])}
            except Exception:
                pass
    out = []
    for it in items:
        vid = it.get("id", {}).get("videoId")
        if not vid:
            continue
        sn = it.get("snippet", {})
        vc = stats.get(vid)
        out.append({
            "videoId": vid,
            "title": (sn.get("title") or "")[:100],
            "published": (sn.get("publishedAt") or "")[:10],
            "views": int(vc) if vc and str(vc).isdigit() else None,
        })
    return out


def get_youtube(channel_id):
    # 优先 Data API v3 (有 key 时), 失败再兜底 RSS
    if CFG.get("youtube_api_key"):
        try:
            out = get_youtube_api(channel_id)
            if out:
                return out
        except Exception as ex:
            print(f"  ! YT API失败 {channel_id}: {ex}", file=sys.stderr)
    # 兜底: RSS (YouTube 2026年起对RSS收紧, 常返回404)
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    raw = fetch(url, proxy=CFG.get("proxy_for_youtube"), retries=3)
    if not raw:
        return []
    try:
        from xml.etree import ElementTree as ET
        root = ET.fromstring(raw)
        ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
              "media": "http://search.yahoo.com/mrss/"}
        out = []
        for e in root.findall("a:entry", ns)[:15]:
            stats = e.find("media:group/media:community/media:statistics", ns)
            out.append({
                "videoId": e.find("yt:videoId", ns).text,
                "title": e.find("a:title", ns).text[:100],
                "published": e.find("a:published", ns).text[:10],
                "views": int(stats.get("views")) if stats is not None else None,
            })
        return out
    except Exception as ex:
        print(f"  ! YT解析失败 {channel_id}: {ex}", file=sys.stderr)
        return []


def main():
    today = date.today().isoformat()
    print(f"=== 快照 {today} ===")
    print("[1/2] 抓取日区游戏榜单…")
    charts = get_charts()

    snap = {"date": today, "fetchedAt": datetime.now().isoformat(timespec="seconds"), "games": {}}
    games = CFG["games"]
    print(f"[2/2] 逐个抓取 {len(games)} 款竞品…")
    for g in games:
        name = g["cn"]
        rec = {"jp": g["jp"], "tags": g["tags"], "origin": g.get("origin")}
        app_id = g.get("appId")
        if app_id:
            rec["grossingRank"] = charts["grossing"].get(app_id)
            rec["freeRank"] = charts["free"].get(app_id)
            rec["meta"] = get_app_meta(app_id)
            rec["reviews"] = get_reviews(app_id)
            time.sleep(1.0)
        else:
            rec["note"] = g.get("note", "无日区App")
        if g.get("yt_channel"):
            rec["youtube"] = get_youtube(g["yt_channel"])
            time.sleep(2.0)
        gr = rec.get("grossingRank")
        nrev = len(rec.get("reviews", []))
        nyt = len(rec.get("youtube", []))
        print(f"  {name:10s} 畅销:{gr if gr else '-':>4} 评论:{nrev:>2} YT:{nyt:>2}")
        snap["games"][name] = rec

    out_dir = os.path.join(BASE, CFG["data_dir"])
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{today}.json")
    json.dump(snap, open(path, "w"), ensure_ascii=False, indent=1)
    print(f"已保存 -> {path}")


if __name__ == "__main__":
    main()
