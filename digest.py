#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日本二游周报 - 群发精华版 (飞书)
在完整版周报(report.py)之外, 生成可直接发群的纯文本:
  reports/digest_YYYY-MM-DD.txt   纯文本版 (emoji分节, 每条附原始链接, 复制即发)
数据: 最近7天快照 + 实时抓官推互动量(X声量Top) + claude CLI中文提炼
用法: python3 digest.py                 # 以今天为截止日
      python3 digest.py 2026-08-01     # 指定截止日
      选项: (默认: 有X缓存读缓存, 没有则自动抓取)
            --refresh-x 忽略缓存强制重抓官推
            --no-x      完全跳过X板块  --no-ai 跳过AI提炼
"""
import json, os, re, sys, math, time, glob, subprocess, tempfile
import urllib.request, urllib.parse
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

BASE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(BASE, "config.json")))
# 本机私密配置 (API key等, 不进git): config.local.json 覆盖合并
_local = os.path.join(BASE, "config.local.json")
if os.path.exists(_local):
    CFG.update(json.load(open(_local)))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
PROXY = CFG.get("proxy_for_youtube")
NITTER = "https://nitter.net"
MY = CFG.get("my_game", "")
APP_IDS = {g["cn"]: g.get("appId") for g in CFG["games"]}


def store_url(name):
    aid = APP_IDS.get(name)
    return f"https://apps.apple.com/jp/app/id{aid}" if aid else None

FLAGS = {a for a in sys.argv[1:] if a.startswith("--")}
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]


# ================= 数据加载 (同 report.py) =================
def load_snapshots(end_date, days=7):
    snaps = {}
    for i in range(days):
        d = (end_date - timedelta(days=i)).isoformat()
        p = os.path.join(BASE, CFG["data_dir"], f"{d}.json")
        if os.path.exists(p):
            snaps[d] = json.load(open(p))
    return dict(sorted(snaps.items()))


def rank_vals(snaps, game, key="grossingRank"):
    return [(s["games"].get(game) or {}).get(key) for s in snaps.values()]


def avg(vals):
    vs = [v for v in vals if v]
    return round(sum(vs) / len(vs), 1) if vs else None


def best(vals):
    vs = [v for v in vals if v]
    return min(vs) if vs else None


def wan(n):
    """123456 -> 12.3万"""
    if n is None:
        return "-"
    if n >= 10000:
        v = n / 10000
        return f"{v:.1f}万".replace(".0万", "万")
    return f"{n:,}"


# ================= X 声量抓取 (monid CLI -> tikhub, $0.0015/账号/次) =================
def _parse_tw_date(s):
    """'Fri Jul 31 11:00:03 +0000 2026' -> '2026-07-31'"""
    try:
        return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y").date().isoformat()
    except Exception:
        return ""


def _fetch_one_game(g, day0, day1):
    """抓取单个游戏的官推, 返回(推文列表, cost). 失败抛异常"""
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    try:
        subprocess.run(
            ["monid", "run", "-p", "tikhub", "-e", "/api/v1/twitter/web/fetch_user_post_tweet",
             "--query", json.dumps({"screen_name": g["x"]}), "-w", "90", "-o", tmp.name],
            capture_output=True, text=True, timeout=150, env={**os.environ, "NO_COLOR": "1"})
        d = json.load(open(tmp.name))
        tweets = []
        for t in d.get("timeline", []):
            pub = _parse_tw_date(t.get("created_at", ""))
            text = (t.get("text") or "").strip()
            if not (day0 <= pub <= day1) or text.startswith("RT @"):
                continue
            media = t.get("media") or {}
            tweets.append({
                "game": g["cn"], "text": text[:180], "date": pub,
                "url": f"https://x.com/{g['x']}/status/{t.get('tweet_id')}",
                "likes": t.get("favorites"), "rts": t.get("retweets"),
                "views": int(t["views"]) if str(t.get("views") or "").isdigit() else None,
                "video": bool(media.get("video"))})
        return tweets, 0.0015
    finally:
        os.unlink(tmp.name)


def fetch_x_buzz(day0, day1):
    """全竞品官推最近20条(一次调用/账号), 过滤到本周窗口, 按点赞排序.
    单个游戏失败自动重试一次, 降低偶发网络抖动丢数据."""
    tweets = []
    games = [g for g in CFG["games"] if g.get("x")]
    cost = 0.0
    for i, g in enumerate(games):
        label = f"  X {i+1}/{len(games)} {g['cn']} (@{g['x']})"
        ok = False
        for attempt in range(2):
            tag = "" if attempt == 0 else " (重试)"
            print(f"{label}{tag} ...", flush=True)
            try:
                ts, c = _fetch_one_game(g, day0, day1)
                tweets.extend(ts)
                cost += c
                ok = True
                break
            except Exception as ex:
                if attempt == 0:
                    time.sleep(2)
                else:
                    print(f"    跳过 ({ex})")
        # 重试也失败才打印跳过
    print(f"  X抓取完成: {len(tweets)}条本周推文, 成本约${cost:.3f}")
    tweets.sort(key=lambda t: -(t.get("likes") or 0))
    return tweets


# 线下/联动活动关键词 (日文)
EVENT_KW = ["コラボ", "タイアップ", "カフェ", "ポップアップ", "POP UP", "POPUP",
            "リアルイベント", "会場", "来場", "展示", "原画展", "フェス", "物販",
            "グッズ", "出展", "ブース", "サイン会", "トークショー", "ライブ",
            "オーケストラ", "コンサート", "聖地", "ローソン", "ファミリーマート",
            "セブン-イレブン", "ミュージアム", "展覧会", "限定ショップ", "献血"]


def filter_events(tweets):
    """从全量周推文中筛出疑似线下/联动活动的候选, 按点赞排序"""
    cands = [t for t in tweets if any(k.lower() in t["text"].lower() for k in EVENT_KW)]
    cands.sort(key=lambda t: -(t.get("likes") or 0))
    return cands[:15]


# ================= 数据聚合 =================
def build_data(end):
    snaps = load_snapshots(end)
    if not snaps:
        sys.exit("最近7天没有快照数据, 请先运行 snapshot.py")
    prev = load_snapshots(end - timedelta(days=7))
    days = list(snaps.keys())
    latest = snaps[days[-1]]
    D = {"day0": days[0], "day1": days[-1], "days": days}

    # ① 排名异动: 环比变化>=10 或 本周新进榜/掉榜
    movers, my_row, all_rows = [], None, []
    for g in CFG["games"]:
        name = g["cn"]
        cur, pre = rank_vals(snaps, name), rank_vals(prev, name) if prev else []
        a, pa, b = avg(cur), avg(pre), best(cur)
        delta = round(pa - a) if (a and pa) else None
        today_rank = cur[-1]
        row = {"name": name, "best": b, "avg": a, "prevAvg": pa, "delta": delta,
               "today": today_rank, "trend": [v for v in cur]}
        all_rows.append(row)
        if name == MY:
            my_row = row
        if delta is not None and abs(delta) >= 10:
            row["kind"] = "up" if delta > 0 else "down"
            movers.append(row)
        elif a and not pa:  # 新进榜
            row["kind"] = "enter"
            movers.append(row)
        elif pa and not a:  # 掉出榜
            row["kind"] = "exit"
            movers.append(row)
    movers.sort(key=lambda r: -(abs(r["delta"]) if r["delta"] else (100 - (r["avg"] or 100))))
    top_now = [r for r in all_rows if r["best"]]
    top_now.sort(key=lambda r: r["best"])
    D["movers"], D["my"], D["top_now"] = movers[:12], my_row, top_now[:10]

    # ② 本周版本更新
    updates = []
    for g in CFG["games"]:
        name = g["cn"]
        metas = {}
        for d, s in snaps.items():
            m = (s["games"].get(name) or {}).get("meta")
            if m and m.get("version"):
                metas.setdefault(m["version"], m)
        for m in list(metas.values())[-1:]:
            vd = m.get("versionDate", "")
            if vd and days[0] <= vd <= days[-1]:
                updates.append({"name": name, "version": m["version"], "date": vd,
                                "notes": (m.get("releaseNotes") or "").strip()[:400]})
    updates.sort(key=lambda u: u["date"])
    D["updates"] = updates

    # ③ 舆情: 本周新评论聚合, 报警条件 评论>=10 且 (差评>=40% 或 均分<=2.5)
    alerts, senti = [], []
    for g in CFG["games"]:
        name = g["cn"]
        seen = {}
        for d, s in snaps.items():
            for r in (s["games"].get(name) or {}).get("reviews", []):
                if r.get("id") and r.get("date") and days[0] <= r["date"] <= days[-1]:
                    seen[r["id"]] = r
                elif r.get("date") and days[0] <= r["date"] <= days[-1] and not r.get("id"):
                    seen[r.get("title", "") + r["date"]] = r
        rs = list(seen.values())
        ratings = [r["rating"] for r in rs if r.get("rating")]
        if not ratings:
            continue
        neg = sum(1 for x in ratings if x <= 2)
        rec = {"name": name, "n": len(ratings), "avg": round(sum(ratings) / len(ratings), 2),
               "negPct": round(neg / len(ratings) * 100)}
        senti.append(rec)
        if len(ratings) >= 10 and (rec["negPct"] >= 40 or rec["avg"] <= 2.5):
            top_neg = sorted([r for r in rs if (r.get("rating") or 5) <= 2],
                             key=lambda r: -(r.get("votes") or 0))[:3]
            rec2 = dict(rec)
            rec2["samples"] = [f"「{r.get('title','')}」{(r.get('body') or '')[:80]}" for r in top_neg]
            alerts.append(rec2)
    alerts.sort(key=lambda a: -a["negPct"])
    D["alerts"], D["senti"] = alerts, senti

    # ④ YouTube 本周热门
    yt = {}
    for g in CFG["games"]:
        name = g["cn"]
        for d, s in snaps.items():
            for v in (s["games"].get(name) or {}).get("youtube", []):
                if v.get("published") and days[0] <= v["published"] <= days[-1]:
                    yt[v["videoId"]] = {"game": name, "title": v["title"],
                                        "published": v["published"], "views": v.get("views"),
                                        "url": f"https://youtu.be/{v['videoId']}"}
    D["yt_top"] = sorted(yt.values(), key=lambda v: -(v.get("views") or 0))[:5]

    # ⑤ X 声量 + 线下/联动候选
    # 策略: 有有效缓存优先读; 否则默认自动抓取; --no-x 显式跳过; --refresh-x 强制重抓
    tweets = []
    cache = os.path.join(BASE, "data", "x_cache", f"{days[-1]}.json")
    cache_valid = False
    if os.path.exists(cache):
        try:
            cached = json.load(open(cache))
            cache_valid = isinstance(cached, list) and len(cached) > 0
        except Exception:
            cache_valid = False

    if "--no-x" in FLAGS:
        tweets = []
    elif cache_valid and "--refresh-x" not in FLAGS:
        tweets = json.load(open(cache))
        print(f"使用X缓存 ({len(tweets)}条): {cache}")
    else:
        if "--refresh-x" in FLAGS:
            print("强制重抓官推声量 (约2-4分钟)...")
        else:
            print("抓取官推声量 (约2-4分钟)...")
        try:
            tweets = fetch_x_buzz(days[0], days[-1])
            if tweets:
                os.makedirs(os.path.dirname(cache), exist_ok=True)
                json.dump(tweets, open(cache, "w"), ensure_ascii=False)
                print(f"  X缓存已保存 ({len(tweets)}条): {cache}")
            else:
                # 抓了但没数据: 不写缓存, 下次还能重试
                print("  X抓取返回0条推文, 未写缓存 (下次运行可重试)")
                if os.path.exists(cache):
                    os.remove(cache)
        except Exception as ex:
            print("X抓取失败:", ex)
            tweets = []
    D["x_top"] = tweets[:5]
    D["events_src"] = filter_events(tweets)
    D["x_missing"] = not tweets
    return D


# ================= AI 中文提炼 (claude CLI -> DeepSeek API 自动降级) =================
def _call_claude(prompt):
    r = subprocess.run(["claude", "-p", "--output-format", "text"],
                       input=prompt, capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"claude CLI 失败: {(r.stderr or '')[:120]}")
    return r.stdout.strip()


def _call_deepseek(prompt):
    key = os.environ.get("DEEPSEEK_API_KEY") or CFG.get("deepseek_api_key")
    if not key:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY")
    body = json.dumps({
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 1.0,
    }).encode()
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        d = json.load(resp)
    return d["choices"][0]["message"]["content"].strip()


def ai_enrich(D):
    """返回 {headline, update_cn:{name:一句话}, alert_cn:{name:原因}, tweet_cn:[...], video_cn:[...], observation:[...]}"""
    if "--no-ai" in FLAGS:
        return {}
    ctx = {
        "周期": f"{D['day0']} ~ {D['day1']}",
        "排名异动": [{"游戏": m["name"], "类型": m["kind"], "环比": m["delta"],
                     "本周最高": m["best"], "今日": m["today"]} for m in D["movers"]],
        "版本更新": [{"游戏": u["name"], "版本": u["version"], "日期": u["date"],
                     "更新说明(日文)": u["notes"][:300]} for u in D["updates"]],
        "舆情警报": [{"游戏": a["name"], "新评论": a["n"], "均分": a["avg"], "差评占比%": a["negPct"],
                     "差评样本(日文)": a["samples"]} for a in D["alerts"]],
        "X热门推文(日文)": [{"i": i, "游戏": t["game"], "内容": t["text"], "点赞": t["likes"]}
                          for i, t in enumerate(D["x_top"])],
        "线下联动候选推文(日文)": [{"i": i, "游戏": t["game"], "日期": t["date"], "内容": t["text"]}
                                for i, t in enumerate(D["events_src"])],
        "YouTube热门(日文)": [{"i": i, "游戏": v["game"], "标题": v["title"], "观看": v["views"]}
                             for i, v in enumerate(D["yt_top"])],
        "我方游戏": MY, "我方本周": D["my"],
    }
    prompt = (
        "你是日本二次元手游市场分析师。以下是本周日区竞品监测数据(JSON)。请输出严格的JSON(不要markdown代码块), 字段:\n"
        '{"headline": "本周一句话焦点摘要(40字内, 点出最重要的1-2个市场事件)",\n'
        ' "update_cn": {"游戏名": "该版本更新内容的中文一句话概括(30字内, 提取新角色/新活动名)"},\n'
        ' "alert_cn": {"游戏名": "差评爆发原因的中文一句话推断(基于差评样本, 25字内)"},\n'
        ' "tweet_cn": ["按i顺序, 每条推文的中文一句话概括(25字内)"],\n'
        ' "events": [{"i": 候选推文编号, "type": "线下活动或联动", "desc": "中文一句话(30字内, 点出合作对象/地点/档期)"}]'
        ' — 从"线下联动候选推文"中甄别真实的线下活动(咖啡店/快闪店/展会/演唱会/周年祭等)与联动合作(品牌/IP/便利店/异业等),'
        ' 排除纯游戏内活动; 同一活动多条推文只保留编号最小的一条; 没有则给空数组,\n'
        ' "video_cn": ["按i顺序, 每条视频标题的中文一句话概括(20字内)"],\n'
        ' "observation": ["对我方游戏的观察/可参考点, 2-3条, 每条30字内, 基于竞品本周动作"]}\n\n'
        "数据:\n" + json.dumps(ctx, ensure_ascii=False)
    )
    out = ""
    for label, fn in [("claude", _call_claude), ("DeepSeek", _call_deepseek)]:
        try:
            print(f"调用 {label} 提炼中文摘要...")
            out = fn(prompt)
            break
        except Exception as ex:
            print(f"  {label} 不可用: {ex}")
    if not out:
        print("AI提炼全部失败, 使用原始数据")
        return {}
    try:
        out = re.sub(r"^```(json)?|```$", "", out, flags=re.M).strip()
        m = re.search(r"\{.*\}", out, re.S)
        return json.loads(m.group(0)) if m else {}
    except Exception as ex:
        print("AI输出解析失败, 使用原始数据:", ex)
        return {}


# ================= 输出: 飞书纯文本版 =================
def fmt_rank(r):
    return str(r) if r else "圏外"


def arrow_txt(m):
    if m["kind"] == "up":
        return f"🔺 上升{m['delta']}位"
    if m["kind"] == "down":
        return f"🔻 下滑{-m['delta']}位"
    if m["kind"] == "enter":
        return "🆕 新进Top100"
    return "⬇️ 掉出Top100"


def render_text(D, AI):
    mmdd = lambda s: s[5:].replace("-", ".")
    rng = f"{mmdd(D['day0'])}~{mmdd(D['day1'])}"
    L = [f"📊 日本二游周报 ({D['day0'].replace('-', '.')} ~ {D['day1'].replace('-', '.')})"]
    L.append("━━━━━━━━━━━━━━")
    if AI.get("headline"):
        L.append(f"📌 {AI['headline']}")
        L.append("")

    if D.get("x_missing"):
        L.append("⚠️ 本周X声量/线下活动数据缺失 (未抓取官推)")
        L.append("")

    if D["movers"]:
        L.append(f"📈 畅销榜异动 ({rng} 日区Top100)")
        for m in D["movers"]:
            pos = f"本周最高{m['best']}位" if m["best"] else ""
            L.append(f"· {m['name']} {arrow_txt(m)} {pos}")
        L.append("")

    if D["top_now"]:
        L.append(f"🏆 本周畅销榜Top10 (按本周最高)")
        for i, r in enumerate(D["top_now"]):
            delta = r["delta"]
            chg = ""
            if delta is not None and abs(delta) >= 10:
                chg = " 🔺" if delta > 0 else " 🔻"
            L.append(f"{i+1}. {r['name']} — 最高第{r['best']}位{chg}")
        L.append("")

    if D["updates"]:
        L.append(f"🆕 本周版本更新 ({rng})")
        for u in D["updates"]:
            cn = AI.get("update_cn", {}).get(u["name"])
            desc = cn if cn else u["notes"][:50].replace("\n", " ")
            L.append(f"· {u['name']} v{u['version']} ({mmdd(u['date'])}) — {desc}")
        L.append("")

    if D["alerts"]:
        L.append(f"⚠️ 舆情警报 ({rng} 新增评论)")
        for a in D["alerts"]:
            cause = AI.get("alert_cn", {}).get(a["name"], "")
            L.append(f"· {a['name']}: 新评{a['n']}条 均分{a['avg']} 差评{a['negPct']}%"
                     + (f" — {cause}" if cause else ""))
        L.append("")

    ev, src = AI.get("events") or [], D.get("events_src") or []
    if ev or (src and not AI):
        L.append(f"🎪 线下 & 联动活动 ({rng} 官宣)")
        if ev:
            for e in ev:
                i = e.get("i")
                t = src[i] if isinstance(i, int) and 0 <= i < len(src) else None
                game = f"[{t['game']}] " if t else ""
                L.append(f"· {game}{e.get('desc', '')} ({e.get('type', '')})")
                if t:
                    L.append(f"  🔗 {t['url']}")
        else:  # 无AI时: 关键词命中的原文兜底
            for t in src[:5]:
                L.append(f"· [{t['game']}] {t['text'][:40]}")
                L.append(f"  🔗 {t['url']}")
        L.append("")

    if D["x_top"]:
        L.append(f"🔥 X声量Top5 ({rng} 按点赞)")
        for i, t in enumerate(D["x_top"]):
            cn = (AI.get("tweet_cn") or [None] * 9)[i] if i < len(AI.get("tweet_cn", [])) else None
            desc = cn if cn else t["text"][:40]
            tag = "🎬" if t.get("video") else ""
            vv = f" 👁{wan(t['views'])}" if t.get("views") else ""
            L.append(f"{i+1}. [{t['game']}]{tag} {desc} — ❤️{wan(t['likes'])}{vv}")
            if t.get("url"):
                L.append(f"  🔗 {t['url']}")
        L.append("")

    if D["yt_top"]:
        L.append(f"🎬 YouTube声量Top5 ({rng} 按观看)")
        for i, v in enumerate(D["yt_top"]):
            cn = (AI.get("video_cn") or [])[i] if i < len(AI.get("video_cn", [])) else None
            desc = cn if cn else v["title"][:40]
            L.append(f"{i+1}. [{v['game']}] {desc} — ▶️{wan(v['views'])}")
            if v.get("url"):
                L.append(f"  🔗 {v['url']}")
        L.append("")

    my = D.get("my")
    obs = AI.get("observation", [])
    if my and my["avg"]:
        # 进榜了才展示
        L.append(f"⭐ {MY}")
        L.append(f"· 本周畅销榜均值 {my['avg']} (最高{fmt_rank(my['best'])})")
        for ob in obs:
            L.append(f"· {ob}")
        L.append("")
    elif obs:
        # 没进榜但有竞品观察 → 跳过排名, 只输出洞察
        L.append(f"💡 竞品观察")
        for ob in obs:
            L.append(f"· {ob}")
        L.append("")
    L.append(f"—— 数据: 日区App Store/官方X/YouTube · {datetime.now().strftime('%m-%d %H:%M')}生成")
    return "\n".join(L)


# ================= 事件存档 (-> data/events.json, 供dashboard roadmap展示) =================
# kind: mkt=线下/联动  alert=舆情警报  x=X热门推文  yt=YouTube热门视频
def save_events(D, AI):
    src = D.get("events_src") or []
    recs = []
    for e in AI.get("events") or []:
        i = e.get("i")
        t = src[i] if isinstance(i, int) and 0 <= i < len(src) else None
        if t:
            recs.append({"kind": "mkt", "date": t["date"], "game": t["game"],
                         "type": e.get("type", ""), "desc": e.get("desc", ""), "url": t["url"]})
    for a in D.get("alerts") or []:
        cause = AI.get("alert_cn", {}).get(a["name"], "")
        link = store_url(a["name"])
        recs.append({"kind": "alert", "date": D["day1"], "game": a["name"], "type": "舆情警报",
                     "desc": f"新评{a['n']}条 均分{a['avg']} 差评{a['negPct']}%"
                             + (f" — {cause}" if cause else ""),
                     "url": f"{link}?see-all=reviews" if link else None,
                     "key": f"alert:{a['name']}:{D['day1']}"})
    tw_cn = AI.get("tweet_cn") or []
    for i, t in enumerate(D.get("x_top") or []):
        cn = tw_cn[i] if i < len(tw_cn) else t["text"][:40]
        recs.append({"kind": "x", "date": t["date"], "game": t["game"], "type": "X热门推文",
                     "desc": f"{cn} (❤️{wan(t['likes'])})", "url": t["url"]})
    vd_cn = AI.get("video_cn") or []
    for i, v in enumerate(D.get("yt_top") or []):
        cn = vd_cn[i] if i < len(vd_cn) else v["title"][:40]
        recs.append({"kind": "yt", "date": v["published"], "game": v["game"], "type": "YouTube热门",
                     "desc": f"{cn} (▶️{wan(v['views'])})", "url": v["url"]})
    if not recs:
        return
    p = os.path.join(BASE, "data", "events.json")
    arch = json.load(open(p)) if os.path.exists(p) else []
    seen = {e.get("key") or e.get("url") for e in arch}
    added = 0
    for r in recs:
        k = r.get("key") or r.get("url")
        if not k or k in seen:
            continue
        r["added"] = D["day1"]
        arch.append(r)
        seen.add(k)
        added += 1
    if added:
        arch.sort(key=lambda e: e["date"])
        json.dump(arch, open(p, "w"), ensure_ascii=False, indent=1)
        print(f"事件存档 +{added}条 -> {p} (累计{len(arch)}条)")


# ================= main =================
def main():
    end = date.fromisoformat(ARGS[0]) if ARGS else date.today()
    print(f"聚合快照数据 (截止 {end}) ...")
    D = build_data(end)
    AI = ai_enrich(D)

    out_dir = os.path.join(BASE, CFG["report_dir"])
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.join(out_dir, f"digest_{D['day1']}")

    txt = render_text(D, AI)
    open(stem + ".txt", "w").write(txt)
    print(f"\n纯文本版 -> {stem}.txt")
    save_events(D, AI)

    print("\n" + "=" * 40 + "\n" + txt)


if __name__ == "__main__":
    main()
