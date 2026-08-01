#!/usr/bin/env python3
"""一次性脚本：通过 iTunes Search API 解析日区 App Store ID"""
import json, time, urllib.request, urllib.parse

GAMES = [
    ("二重螺旋", "デュエットナイトアビス"),
    ("原神", "原神"),
    ("崩坏星穹铁道", "崩壊：スターレイル"),
    ("鸣潮", "鳴潮"),
    ("异环", "NTE"),
    ("明日方舟终末地", "アークナイツ：エンドフィールド"),
    ("绝区零", "ゼンレスゾーンゼロ"),
    ("NIKKE", "勝利の女神：NIKKE"),
    ("蔚蓝档案", "ブルーアーカイブ"),
    ("战双帕尼什", "パニシング：グレイレイヴン"),
    ("碧蓝航线", "アズールレーン"),
    ("重返未来1999", "リバース：1999"),
    ("FGO", "Fate/Grand Order"),
    ("燕云十六声", "Where Winds Meet"),
    ("学园偶像大师", "学園アイドルマスター"),
    ("剑与远征：启程", "AFK Journey"),
    ("无限大", "ANANTA"),
    ("魔御STAR DIVE", "モンギル：STAR DIVE"),
    ("赛马娘", "ウマ娘 プリティーダービー"),
    ("七大罪Origin", "七つの大罪 Origin"),
    ("明日方舟", "アークナイツ"),
    ("尘白禁区", "スノウブレイク"),
    ("交错战线", "ダイブロス・コア"),
    ("少女前线2", "ドールズフロントライン2"),
    ("炽焰天穹", "ヘブンバーンズレッド"),
    ("洛克王国世界", "ロックキングダム"),
]

out = []
for cn, jp in GAMES:
    q = urllib.parse.urlencode({"term": jp, "country": "jp", "entity": "software", "limit": 3})
    try:
        with urllib.request.urlopen(f"https://itunes.apple.com/search?{q}", timeout=20) as r:
            res = json.load(r).get("results", [])
    except Exception as e:
        res = []
        print(f"ERR {cn}: {e}")
    if res:
        top = res[0]
        out.append({"cn": cn, "jp": jp, "appId": top["trackId"], "storeName": top["trackName"], "seller": top.get("sellerName", "")})
        print(f"OK  {cn:12s} -> {top['trackId']}  {top['trackName'][:40]}  [{top.get('sellerName','')[:30]}]")
    else:
        out.append({"cn": cn, "jp": jp, "appId": None, "storeName": None, "seller": None})
        print(f"MISS {cn} ({jp})")
    time.sleep(1.2)

json.dump(out, open("resolved.json", "w"), ensure_ascii=False, indent=2)
