#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日本二游竞品监测 - 周报生成
聚合最近7天的每日快照, 生成 Markdown 周报:
  ①畅销榜排名周趋势 ②本周版本更新 ③评论舆情(评分分布+高赞评论) ④官方YouTube本周动态
用法: python3 report.py            # 以今天为截止日, 回看7天
      python3 report.py 2026-07-18 # 指定截止日
"""
import json, os, sys, glob
from datetime import date, datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(BASE, "config.json")))


def load_snapshots(end_date, days=7):
    snaps = {}
    for i in range(days):
        d = (end_date - timedelta(days=i)).isoformat()
        p = os.path.join(BASE, CFG["data_dir"], f"{d}.json")
        if os.path.exists(p):
            snaps[d] = json.load(open(p))
    return dict(sorted(snaps.items()))


def prev_week_snapshots(end_date, days=7):
    return load_snapshots(end_date - timedelta(days=days), days)


def rank_series(snaps, game, key="grossingRank"):
    out = []
    for d, s in snaps.items():
        g = s["games"].get(game, {})
        out.append((d, g.get(key)))
    return out


def best(vals):
    vs = [v for v in vals if v]
    return min(vs) if vs else None


def avg(vals):
    vs = [v for v in vals if v]
    return round(sum(vs) / len(vs), 1) if vs else None


def fmt_rank(r):
    return str(r) if r else "圏外"


def arrow(delta):
    if delta is None:
        return ""
    if delta > 0:
        return f"↑{delta}"
    if delta < 0:
        return f"↓{-delta}"
    return "→"


def main():
    end = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    snaps = load_snapshots(end)
    if not snaps:
        sys.exit(f"最近7天没有快照数据, 请先运行 snapshot.py (数据目录: {CFG['data_dir']})")
    prev = prev_week_snapshots(end)
    days = list(snaps.keys())
    latest = snaps[days[-1]]

    L = []
    L.append(f"# 日本二游竞品周报  {days[0]} ~ {days[-1]}")
    L.append("")
    L.append(f"> 数据来源: 日区App Store畅销榜(Top100)/iTunes元数据/日区用户评论/官方YouTube · 覆盖{len(snaps)}天快照 · 生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    L.append("")

    # ---------- ① 畅销榜排名 ----------
    L.append("## ① 畅销榜排名周趋势 (日区·游戏类)")
    L.append("")
    L.append("| 游戏 | 本周最高 | 本周均值 | 上周均值 | 周环比 | 每日走势 |")
    L.append("|---|---|---|---|---|---|")
    rows = []
    for g in CFG["games"]:
        name = g["cn"]
        cur = [v for _, v in rank_series(snaps, name)]
        pre = [v for _, v in rank_series(prev, name)] if prev else []
        b, a = best(cur), avg(cur)
        pa = avg(pre)
        delta = round(pa - a) if (a and pa) else None  # 排名数值降低=上升
        trend = "/".join(fmt_rank(v) for _, v in rank_series(snaps, name))
        rows.append((a if a else 999, name, b, a, pa, delta, trend))
    rows.sort()
    for _, name, b, a, pa, delta, trend in rows:
        star = " ⭐" if name == CFG.get("my_game") else ""
        L.append(f"| {name}{star} | {fmt_rank(b)} | {fmt_rank(a)} | {fmt_rank(pa)} | {arrow(delta)} | {trend} |")
    L.append("")
    L.append("*排名为日区游戏类畅销榜名次, 「圏外」=当日不在Top100。周环比↑=排名上升。*")
    L.append("")

    # ---------- ② 版本更新 ----------
    L.append("## ② 本周版本更新")
    L.append("")
    any_update = False
    for g in CFG["games"]:
        name = g["cn"]
        seen = {}
        for d, s in snaps.items():
            m = (s["games"].get(name) or {}).get("meta")
            if m and m.get("version"):
                seen.setdefault(m["version"], (d, m))
        if len(seen) == 0:
            continue
        versions = list(seen.items())
        # 更新日在本周内的才算"本周更新"
        latest_meta = versions[-1][1][1]
        vdate = latest_meta.get("versionDate", "")
        if vdate and days[0] <= vdate <= days[-1]:
            any_update = True
            notes = (latest_meta.get("releaseNotes") or "").strip().replace("\n", " / ")[:200]
            L.append(f"### {name} → v{latest_meta['version']} ({vdate})")
            L.append(f"- 更新说明: {notes if notes else '(未提供)'}")
            L.append("")
    if not any_update:
        L.append("本周监测范围内无App版本更新。")
        L.append("")

    # ---------- ③ 评论舆情 ----------
    L.append("## ③ 用户评论舆情 (本周新增)")
    L.append("")
    L.append("| 游戏 | 本周新评论 | 均分 | 1-2星占比 | 商店总评分 |")
    L.append("|---|---|---|---|---|")
    review_pool = {}
    for g in CFG["games"]:
        name = g["cn"]
        seen = {}
        for d, s in snaps.items():
            for r in (s["games"].get(name) or {}).get("reviews", []):
                if r.get("id") and r.get("date") and days[0] <= r["date"] <= days[-1]:
                    seen[r["id"]] = r
        rs = list(seen.values())
        review_pool[name] = rs
        if not rs:
            continue
        ratings = [r["rating"] for r in rs if r.get("rating")]
        neg = sum(1 for x in ratings if x <= 2)
        meta = (latest["games"].get(name) or {}).get("meta") or {}
        store = f"{meta.get('rating', '-')} ({meta.get('ratingCount', '-')}件)" if meta else "-"
        L.append(f"| {name} | {len(rs)} | {round(sum(ratings)/len(ratings),2) if ratings else '-'} | {round(neg/len(ratings)*100) if ratings else 0}% | {store} |")
    L.append("")

    # 高赞/代表性评论: 每款取票数最高的差评和好评各1条
    L.append("### 代表性评论摘录")
    L.append("")
    for name, rs in review_pool.items():
        if not rs:
            continue
        rs_sorted = sorted(rs, key=lambda r: -(r.get("votes") or 0))
        pos = next((r for r in rs_sorted if (r.get("rating") or 0) >= 4), None)
        negr = next((r for r in rs_sorted if (r.get("rating") or 0) <= 2), None)
        if not pos and not negr:
            continue
        L.append(f"**{name}**")
        for tag, r in [("👍", pos), ("👎", negr)]:
            if r:
                body = r["body"].replace("\n", " ")[:120]
                L.append(f"- {tag} {'★'*r['rating']} 「{r['title']}」{body} ({r['date']})")
        L.append("")

    # ---------- ④ YouTube动态 ----------
    L.append("## ④ 官方YouTube本周动态")
    L.append("")
    yt_rows = []
    for g in CFG["games"]:
        name = g["cn"]
        seen = {}
        for d, s in snaps.items():
            for v in (s["games"].get(name) or {}).get("youtube", []):
                if v.get("published") and days[0] <= v["published"] <= days[-1]:
                    # 保留最后一次快照的观看数(最新)
                    seen[v["videoId"]] = v
        for v in seen.values():
            yt_rows.append((name, v))
    if yt_rows:
        yt_rows.sort(key=lambda x: -(x[1].get("views") or 0))
        L.append("| 游戏 | 视频标题 | 发布日 | 观看数 |")
        L.append("|---|---|---|---|")
        for name, v in yt_rows:
            views = f"{v['views']:,}" if v.get("views") else "-"
            L.append(f"| {name} | [{v['title'][:60]}](https://youtu.be/{v['videoId']}) | {v['published']} | {views} |")
    else:
        L.append("本周监测范围内官方频道无新视频。")
    L.append("")

    # ---------- 汇总提示 ----------
    L.append("---")
    L.append("### 本周值得注意")
    notes = []
    for _, name, b, a, pa, delta, _t in rows:
        if delta and delta >= 15:
            notes.append(f"- **{name}** 畅销榜均值周环比上升{delta}位 (可能有版本/活动节点, 建议查看其官推)")
        if delta and delta <= -15:
            notes.append(f"- **{name}** 畅销榜均值周环比下滑{-delta}位")
    for name, rs in review_pool.items():
        ratings = [r["rating"] for r in rs if r.get("rating")]
        if len(ratings) >= 10 and sum(1 for x in ratings if x <= 2) / len(ratings) >= 0.5:
            notes.append(f"- **{name}** 本周新评论中差评(≤2星)占比超50%, 可能有舆情事件")
    L.extend(notes if notes else ["- 无异常信号。"])
    L.append("")

    out = os.path.join(BASE, CFG["report_dir"], f"weekly_{days[-1]}.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w").write("\n".join(L))
    print(f"周报已生成 -> {out}")


if __name__ == "__main__":
    main()
