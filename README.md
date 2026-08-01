# 日本二游竞品监测器 (jp-gacha-monitor)

监测名单来自《竞品情况以及亮点拆解.xlsx》的 26 款竞品（二重螺旋视角，config.json 中 `my_game` 标 ⭐）。

## 快速开始（他人试用本 Demo）
```bash
git clone <本仓库>
cd jp-gacha-monitor
python3 snapshot.py        # 先抓一天快照 (纯免费公开接口)
python3 server.py          # 打开 http://127.0.0.1:8642
```
可选能力需要**你自己的**账号/密钥（不含在仓库里，也不会用到作者的额度）：
- **X/推特声量抓取**：安装并登录你自己的 [monid CLI](https://monid.ai)（`npm i -g @monid-ai/cli`），周报勾选"实时抓官推"时按你的余额计费（约 $0.04/次）
- **AI 中文提炼**：本机装有 claude CLI 则自动使用；否则在项目根目录建 `config.local.json`：
  ```json
  { "deepseek_api_key": "sk-你自己的DeepSeek密钥" }
  ```
  两者都没有时周报自动降级为日文原文，其余功能不受影响。


## 监测内容
| 维度 | 数据源 | 说明 |
|---|---|---|
| ① 畅销榜/免费榜排名 | 日区 App Store RSS（游戏类 Top100） | 每日快照，周报中展示周趋势和环比 |
| ② 版本更新 | iTunes Lookup API | 版本号、更新日期、更新说明（可推断活动排期） |
| ③ 用户评论舆情 | iTunes MZStore 评论接口（日区最新50条/天） | 周报聚合去重，输出均分、差评占比、高赞评论 |
| ④ 官方YouTube | 频道 RSS（走本机代理 127.0.0.1:9090） | 本周新视频+观看数，按热度排序 |

X/Twitter 官推暂无免费公开接口，未纳入自动抓取；周报"值得注意"栏会在排名异动时提示手动查看官推。

## 使用
```bash
python3 snapshot.py            # 抓当日快照 -> data/snapshots/YYYY-MM-DD.json
python3 report.py              # 聚合最近7天 -> reports/weekly_YYYY-MM-DD.md
python3 report.py 2026-07-18   # 指定截止日补生成
python3 server.py              # 实时仪表盘 -> http://127.0.0.1:8642
```

## 实时仪表盘 (server.py + dashboard.html)
- 26款竞品卡片按畅销榜排名排序，含畅销/免费双榜实时名次（10分钟自动刷新）
- 点击卡片查看单款详情：排名历史走势图（读快照数据）、当前版本+更新说明、
  官推最新推文（nitter RSS）、官方YouTube最新视频+观看数、日区最新评论
- 所有外部抓取由本地服务代理并缓存（榜单/评论10分钟、YT/X 15分钟），
  浏览器只访问本地接口，无CORS问题
- X抓取走 nitter.net 的RSS（免费公开渠道，偶尔限流429，缓存会保留旧数据）

## 高曝光营销动作 (/api/buzz)
- 仪表盘中部"🔥 高曝光营销动作"板块，点击"加载"按钮触发（首次约1-2分钟，之后30分钟缓存）
- 左栏: 全竞品官推近期推文按**点赞数**排序 Top20（互动量来自 cdn.syndication.twimg.com 公开接口，无需登录；🎬=视频推文）
- 右栏: 全竞品官方YouTube近期视频按**观看数**排序 Top20
- 用途: 一眼看出本周日本市场哪些营销动作真正打出了声量（如原神400万粉纪念、赛马娘CM系列、无限大预热PV）

## 自动运行（已安装 launchd）
- `com.jpgacha.snapshot`：每天 12:10 抓快照（选午间是因为 Mac 夜间可能睡眠；launchd 错过会在下次唤醒后补跑）
- `com.jpgacha.weekly`：每周五 18:00 生成周报
- 日志：`data/snapshot.log`、`data/weekly.log`
- 卸载：`launchctl unload ~/Library/LaunchAgents/com.jpgacha.{snapshot,weekly}.plist`

## 维护
- **增删竞品**：编辑 `config.json` 的 `games`。新游戏需要 `appId`（用 `resolve_ids.py` 里的方式搜）和 `yt_channel`（可留 null，仅榜单+评论）
- **未上线游戏**（无限大、洛克王国世界）：`appId: null`，只监测 YouTube；上线后填入 appId 即可
- **已知坑**：
  - Apple 官方 customerreviews RSS 已不稳定（长期空 feed），本工具用的是 MZStore userReviewsRow 接口 + `X-Apple-Store-Front: 143462`（=日本）
  - YouTube 直连不通，依赖本机 9090 端口代理；代理没开时 YT 数据为空但不影响其他维度
  - 榜单 RSS 实际只返回 Top100（请求200也只给100）
