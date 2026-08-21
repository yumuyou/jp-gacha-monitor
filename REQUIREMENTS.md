# JP Gacha Monitor — 需求文档

> 日本二游竞品监测器 · Vercel 云端部署版  
> 目标：在任意设备的浏览器中打开网页，即可完成全部监测动作（榜单查看、详情钻取、营销 Buzz、周报生成）

---

## 1. 项目背景

### 1.1 现状

JP Gacha Monitor 是一个以「二重螺旋」为视角的日本二次元手游竞品监测工具，覆盖 **26 款竞品**，监测维度包括：

| 维度 | 数据源 | 频率 |
|------|--------|------|
| 畅销榜/免费榜排名 | 日区 App Store RSS（游戏类 Top100） | 每 10 分钟 |
| 版本更新 | iTunes Lookup API | 每 10 分钟 |
| 用户评论舆情 | iTunes MZStore userReviewsRow 接口 | 每 10 分钟 |
| 官方 YouTube 动态 | YouTube 频道 RSS | 每 15 分钟 |
| 官推 X/Twitter 动态 | nitter.net RSS + syndication.twimg.com | 每 15 分钟 |
| 每日快照 | 以上全部维度 | 每天 1 次 |
| 周报生成 | 7 天快照聚合 + AI 中文提炼 | 每周 1 次 |

### 1.2 当前痛点

- **使用门槛高**：需要本机运行 `python3 server.py`，只能在本地浏览器访问 `http://127.0.0.1:8642`
- **依赖本机环境**：YouTube 需要本机代理（127.0.0.1:9090），Mac 睡眠期间定时任务不会执行
- **无法分享**：仪表盘和周报只能在本机查看，无法发给团队成员
- **monid 与部署耦合**：周报的 X 声量抓取依赖 monid CLI 的 subprocess 调用，无法在 Serverless 环境运行

### 1.3 目标

将 JP Gacha Monitor **完整部署到 Vercel**，实现：

1. **网页端完成全部动作**：榜单监控、竞品详情、Buzz 营销聚合、周报生成与查看，全部在浏览器操作
2. **monid 能力云端化**：X/Twitter 官推数据抓取在 Vercel 上正常运行
3. **零本地依赖**：不需要开代理、不需要启动 server.py
4. **任意设备访问**：手机、平板、其他电脑均可通过 Vercel 域名打开

---

## 2. 现有资产分析

### 2.1 已有 Vercel 适配版

`api/index.py`（333 行）已经完成了 Vercel Serverless 的核心适配，覆盖率约 **80%**：

```
✅ /api/games        — 竞品名单
✅ /api/charts       — 日区榜单 Top100
✅ /api/detail       — App 详情 + 最新评论
✅ /api/youtube      — 官方频道视频（海外直连，无需代理）
✅ /api/x            — 官推动态（nitter RSS）
✅ /api/buzz         — 营销动作聚合（逐游戏模式）
✅ /api/history      — 排名历史（读本地快照文件）
✅ /api/roadmap      — 版本路线图
❌ /api/digest/*     — 周报生成（未实现）
```

### 2.2 与本地版的差异

| 项目 | 本地 server.py | Vercel api/index.py | 需适配 |
|------|:---:|:---:|:---:|
| 运行模式 | 常驻进程 | 按需启动（Serverless） | — |
| 缓存策略 | 内存 dict | 模块级变量（warm 保温） | — |
| YouTube 访问 | 走本地代理 | 海外节点直连 | — |
| Buzz 构建 | 后台线程全量构建 | 前端逐游戏请求聚合 | ⚠️ 前端需适配 |
| 周报 digest | subprocess 调用 monid + claude | 未实现 | ⚠️ 需新增 |
| 文件读写 | 本地磁盘 | 只读 repo 文件 + Vercel Blob | ⚠️ 需适配 |

### 2.3 关键技术发现

**monid 不需要上 Vercel**。monid CLI 在 `digest.py` 中只做一件事：抓取 X/Twitter 官推的**互动量数据**（点赞/转发/浏览）。Vercel 版 `api/index.py` 已通过免费公开源实现了完全等价的功能：

```
nitter.net RSS           →  推文文本 + 链接 + 日期（免费，无需登录）
cdn.syndication.twimg.com → 点赞数 + 回复数 + 是否含视频（免费公开接口）
```

**Python 零外部依赖**。`api/index.py` 全部使用标准库（`urllib`, `json`, `os`, `re`, `math`, `time`, `glob`, `xml.etree`, `http.server`），Vercel 部署无需 `requirements.txt`。

---

## 3. 目标架构

```
┌─────────────────────────────────────────────────────────┐
│                      Vercel                             │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │  dashboard.html        ← 静态托管（SPA 单页面）    │   │
│  │                                                       │
│  │  Tab 1: 📊 监测面板                                   │
│  │    · 26 款竞品卡片（按畅销榜排名）                     │
│  │    · 点击查看详情（排名走势图 + 版本 + 评论 + X + YT） │
│  │    · Buzz 高曝光营销动作聚合（类别筛选 + Top20）      │
│  │                                                       │
│  │  Tab 2: 🗓 版本 Roadmap                               │
│  │    · 全年时间轴 · 版本节点 · 推估下版本 · 事件存档    │
│  │                                                       │
│  │  Tab 3: 📰 周报                                       │
│  │    · 一键生成 · 实时查看 · 历史存档 · 一键复制         │
│  └─────────────────────────────────────────────────┘   │
│                          │                              │
│                          │ fetch(/api/*)                │
│                          ▼                              │
│  ┌─────────────────────────────────────────────────┐   │
│  │  api/index.py           ← Python Serverless     │   │
│  │                                                       │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │  │ 实时接口  │  │ Buzz接口  │  │ Digest 周报接口   │   │
│  │  │ /games   │  │ /buzz    │  │ /digest/run      │   │
│  │  │ /charts  │  │ (逐游戏)  │  │ /digest/status   │   │
│  │  │ /detail  │  │          │  │ /digest/list     │   │
│  │  │ /youtube │  │          │  │ /digest/get      │   │
│  │  │ /x       │  │          │  │                  │   │
│  │  │ /history │  │          │  │ AI: DeepSeek API │   │
│  │  │ /roadmap │  │          │  │                  │   │
│  │  └──────────┘  └──────────┘  └──────────────────┘   │
│  │                                                       │
│  │  数据源（全免费公开接口）:                              │
│  │  · Apple RSS / iTunes API / MZStore                   │
│  │  · YouTube RSS（Vercel 海外节点直连）                  │
│  │  · nitter.net RSS（X/Twitter 推文）                   │
│  │  · cdn.syndication.twimg.com（推文互动量）             │
│  │  · api.deepseek.com（AI 中文提炼，需 API Key）         │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │  vercel.json            ← 路由 + 定时任务        │   │
│  │                                                       │
│  │  {                                                    │
│  │    "routes": [                                         │
│  │      { "src": "/api/(.*)", "dest": "/api/index.py" }, │
│  │      { "src": "/(.*)",      "dest": "/dashboard.html" }│
│  │    ]                                                   │
│  │  }                                                     │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

---

## 4. 功能需求

### 4.1 监测面板（Tab: monitor）

#### 4.1.1 竞品卡片网格
- **FR-01**: 以卡片网格展示全部 26 款竞品
- **FR-02**: 卡片按**日区畅销榜实时排名**升序排列
- **FR-03**: 每张卡片显示：中文名、日文名、畅销榜排名、免费榜排名、来源地区（CN/KO/JP）
- **FR-04**: 「二重螺旋」卡片始终显示 ⭐ 标记并置顶（通过 `my_game` 配置识别）
- **FR-05**: 排名颜色语义：Top10 金色、Top30 绿色、Top100 白色、圏外灰色
- **FR-06**: 数据每 **10 分钟自动刷新**，页面上方显示上次刷新时间
- **FR-07**: 提供"立即刷新"按钮手动触发全量更新

#### 4.1.2 竞品详情面板
- **FR-08**: 点击卡片展开详情（不跳转，当前页内展示）
- **FR-09**: 详情顶部：游戏名、标签、当前畅销排名、商店评分、X 账号链接
- **FR-10**: **排名历史走势图**（SVG 折线图，最近 30 天畅销榜排名，越高越靠前）
- **FR-11**: **当前版本信息**：版本号、发布日期、更新说明（日文原文）
- **FR-12**: **X 官推动态**（左栏）：最近 8 条推文，点击跳转原链接
- **FR-13**: **YouTube 最新视频**（中栏）：最近 8 个视频，含标题、发布日期、观看数
- **FR-14**: **日区最新评论**（右栏）：最近 8 条评论，含星级、标题、正文摘要、日期、有用数
- **FR-15**: 差评（≤2 星）评论高亮红色标记
- **FR-16**: 响应式布局：宽屏三栏，窄屏（≤1100px）自动切换为单栏

#### 4.1.3 Buzz 高曝光营销动作
- **FR-17**: 页面中部"🔥 高曝光营销动作"板块
- **FR-18**: 左栏：全竞品官推按**点赞数**排序 Top20，显示互动量、所属游戏、推文摘要
- **FR-19**: 右栏：全竞品 YouTube 视频按**观看数**排序 Top20，显示观看数、所属游戏、视频标题
- **FR-20**: **营销类别分类**（基于日文关键词自动识别）：转抽/抽奖、联动/合作、线下活动/硬广、直播/前瞻番组、声优/KOL/艺人、里程碑/周年、二创/贺图/征集、音乐/主题曲、事前登録/预约、版本/游戏情报、其他
- **FR-21**: 类别筛选 Chips：点击切换，显示每个类别下的推文+视频数量
- **FR-22**: 数据缓存 **30 分钟**，过期后需手动点击刷新
- **FR-23**: Vercel 版改**逐游戏渐进加载**：前端收到游戏列表后逐个请求并聚合，先返回的先显示

### 4.2 版本 Roadmap（Tab: roadmap）

- **FR-24**: 年度时间轴视图，显示全部竞品的版本历史 + 预测
- **FR-25**: X 轴 = 时间（1月~12月），Y 轴 = 竞品（按畅销排名排序，自家游戏置顶）
- **FR-26**: 节点类型：
  - 蓝色圆点 = 大版本（x.y.0）
  - 灰色圆点 = 热修/小版本
  - 金色菱形 = 手动登记官宣节点（来自 config.json 的 `roadmap` 字段）
  - 绿色虚线圆 = 推算的下个版本日
  - 紫色实心圆 = 线下活动事件
  - 紫色空心圆 = 联动合作事件
  - 红色圆点 = 舆情警报
  - 青色圆点 = X 热门推文
  - 橙色圆点 = YouTube 热门视频
- **FR-27**: 鼠标悬停节点显示详情气泡（游戏名、版本号、日期、更新说明/事件描述）
- **FR-28**: 支持年份切换（上一年/下一年按钮），默认显示当前年份
- **FR-29**: 时间轴自动滚动到今天位置
- **FR-30**: 月界线和周一细线两层刻度
- **FR-31**: 彩色事件节点（线下/舆情/X/YT）支持点击跳转到原始链接
- **FR-32**: 版本更新周期推算：优先使用 config 手工配置的 `cadence_days`，否则从历史间隔中位数自动推算

### 4.3 周报（Tab: digest）

- **FR-33**: 一键生成按钮，生成最近 7 天的精华版周报
- **FR-34**: **「实时抓官推」复选框**：勾选后在生成周报时实时抓取 X 官推互动量数据（走 nitter + syndication 免费源，约 1~3 分钟）
- **FR-35**: 生成过程显示实时日志流（从 serverless 函数 websocket 轮询或 SSE 推送）
- **FR-36**: 生成完成后自动展示周报全文
- **FR-37**: "复制全文"按钮，一键复制到剪贴板（用于粘贴到飞书群）
- **FR-38**: **历史周报存档**列表：按日期倒序展示，点击查看往期周报全文

#### 4.3.1 周报内容结构
- **FR-39**: **📌 本周焦点摘要**：AI 提炼的一句话，点出最重要的 1~2 个市场事件（≤40 字）
- **FR-40**: **📈 畅销榜异动**：周环比变化 ≥10 位的竞品列表，含升降方向和最高排名
- **FR-41**: **🆕 版本更新**：本周 App Store 版本更新的竞品列表，AI 提炼更新内容中文概括
- **FR-42**: **⚠️ 舆情警报**：当某竞品周新评论 ≥10 条且差评率 ≥40% 或均分 ≤2.5 时触发，AI 推断差评原因
- **FR-43**: **🎪 线下 & 联动活动**：AI 从本周推文中甄别真实的线下活动（咖啡店/快闪店/展会/演唱会）和联动合作（品牌/IP/便利店），含原始推文链接
- **FR-44**: **🔥 X 声量 Top5**：本周全竞品官推按点赞排序前 5，AI 中文概括，标注是否为视频推文
- **FR-45**: **🎬 YouTube 声量 Top5**：本周全竞品新视频按观看数排序前 5，AI 中文概括标题
- **FR-46**: **⭐ 我方游戏专栏**：二重螺旋的本周畅销榜表现 + 竞品本周动作中对我方可参考的 2~3 条观察
- **FR-47**: 周报底部附数据来源说明和生成时间戳

#### 4.3.2 AI 提炼
- **FR-48**: AI 调用优先级：DeepSeek API（配置在 `config.local.json` 中，通过环境变量注入）→ 降级为日文原文
- **FR-49**: AI 输出格式为严格 JSON（含 headline / update_cn / alert_cn / tweet_cn / video_cn / events / observation 字段），解析失败时自动降级
- **FR-50**: 事件存档：每次 AI 甄别出的线下/联动活动、舆情警报、X 热门推文、YouTube 热门视频自动追加到 `data/events.json`，供 Roadmap 时间轴展示

---

## 5. 非功能需求

### 5.1 性能
- **NFR-01**: 榜单/详情接口缓存 10 分钟（模块级，Vercel 函数实例保温期间有效）
- **NFR-02**: YouTube/X 接口缓存 15 分钟
- **NFR-03**: Buzz 聚合数据缓存 30 分钟
- **NFR-04**: Roadmap 接口缓存 1 小时
- **NFR-05**: Vercel 函数冷启动时间 < 3 秒（Python 标准库，无重依赖）
- **NFR-06**: 所有接口添加 `Cache-Control: s-maxage=300, stale-while-revalidate=600` 响应头

### 5.2 兼容性
- **NFR-07**: 支持现代浏览器（Chrome/Firefox/Safari/Edge 最近 2 个大版本）
- **NFR-08**: 响应式设计：桌面端三栏布局，移动端自动切换单栏
- **NFR-09**: 支持暗色主题（当前即为暗色风格，CSS 变量驱动）

### 5.3 可靠性
- **NFR-10**: 接口抓取失败时保留旧缓存数据，不清空（优雅降级）
- **NFR-11**: nitter.net 限流（429）时保留旧 X 数据并在 UI 提示"稍后重试"
- **NFR-12**: YouTube RSS 获取失败时不影响其他维度数据展示
- **NFR-13**: 快照文件缺失某天数据时（如 Mac 休眠），周报自动跳过空窗期

### 5.4 安全性
- **NFR-14**: `config.local.json`（含 DeepSeek API Key）不进入 git 仓库
- **NFR-15**: DeepSeek API Key 通过 Vercel 环境变量注入，不硬编码在代码中
- **NFR-16**: 所有外部请求设置合理的超时时间（15~30 秒）

### 5.5 可维护性
- **NFR-17**: 增删竞品只需编辑 `config.json` 的 `games` 数组
- **NFR-18**: 新增营销分类关键词只需编辑 `MKT_CATS` 列表
- **NFR-19**: 应用商店 / X / YouTube 等数据源 URL 作为常量集中管理

---

## 6. 数据方案

### 6.1 快照存储：Git 托管（方案 A）

```
工作流程:
═══════════════════════════════════════════════════

  本机（每天一次）                   GitHub              Vercel
  ──────────────                    ──────              ──────
  python3 snapshot.py
  → data/snapshots/2026-08-02.json
       │
  git add data/
  git commit -m "snapshot 2026-08-02"
  git push ─────────────────────→  repo 更新 ────→  自动部署
                                                       │
                                                       │
  浏览器访问 vercel.app ──────────────────────────────→ 新快照上线
```

**需要调整**：当前 `.gitignore` 排除了 `data/` 目录。改为只排除运行时生成的文件：
```gitignore
# 旧规则
data/

# 新规则
data/snapshot.log
data/weekly.log
data/server.log
data/x_cache/
```

`data/snapshots/*.json`、`data/versions.json`、`data/events.json` 提交到 git。

**每日操作**:
```bash
python3 snapshot.py                    # 1. 抓当日快照
git add data/snapshots/                # 2. 暂存新快照
git commit -m "snapshot $(date +%Y-%m-%d)"  # 3. 提交
git push                               # 4. 推送 → Vercel 自动部署
```

可进一步简化为一行脚本：
```bash
# snapshot_and_push.sh
python3 snapshot.py && git add data/ && git commit -m "snapshot $(date +%Y-%m-%d)" && git push
```

### 6.2 版本历史：data/versions.json

- 来源：① iTunes Lookup API 当前版本 ② 每日快照中记录的 meta.version
- api/index.py 启动时合并历史 + 当前版本（内存中合并，不落盘）
- 新版本检测到后，需要**手动或通过 CI** 回写到 `data/versions.json` 并提交到 git

### 6.3 事件存档：data/events.json

- 来源：每次生成周报时 AI 甄别的线下/联动活动、舆情警报、热门推文/视频
- 本地运行 `digest.py` 时自动更新 `data/events.json`
- 提交到 git 后，Roadmap 时间轴即可展示

### 6.4 周报存档：Git 托管

- `reports/digest_*.txt` 和 `reports/weekly_*.md` 提交到 git
- `.gitignore` 中移除 `reports/` 排除规则
- Vercel 上 `/api/digest/get` 直接读文件返回

---

## 7. API 接口规范

### 7.1 实时监测接口

| 端点 | 参数 | 返回 | 缓存 |
|------|------|------|:---:|
| `GET /api/games` | — | 竞品名单数组 | — |
| `GET /api/charts` | — | `{grossing: {appId: rank}, free: {appId: rank}}` | 10min |
| `GET /api/detail` | `app=<id>` | `{meta: {...}, reviews: [...]}` | 10min |
| `GET /api/youtube` | `ch=<channel_id>` | 视频数组 `[{videoId, title, published, views, thumb}]` | 15min |
| `GET /api/x` | `user=<handle>` | 推文数组 `[{id, text, url, date, reply}]` | 15min |
| `GET /api/history` | `game=<cn>` | 排名数组 `[{date, grossing, free}]` (最近30天) | — |
| `GET /api/roadmap` | — | 版本路线图数组 | 1h |

### 7.2 Buzz 接口（Vercel 版逐游戏模式）

| 端点 | 参数 | 返回 |
|------|------|------|
| `GET /api/buzz` | — | `{perGame: true, games: [...], catLabels: {...}}` |
| `GET /api/buzz` | `game=<cn>` | `{tweets: [...], videos: [...]}` — 单款游戏数据 |

**前端逻辑**：
1. 先调用 `/api/buzz` 获取游戏列表和分类标签
2. 对每个游戏并发调用 `/api/buzz?game=<cn>`（限制并发数 ≤4）
3. 全部完成后客户端侧聚合、排序、渲染

### 7.3 周报接口

| 端点 | 参数 | 返回 |
|------|------|------|
| `GET /api/digest/run` | `?x=0\|1` | `{building: true, log: "..."}` |
| `GET /api/digest/status` | — | `{building, log}` 或 `{done: true, log}` |
| `GET /api/digest/list` | — | 历史周报文件列表 `[{file, date}]` |
| `GET /api/digest/get` | `?f=<filename>` | `{file, text}` — 周报全文 |

**注意**：Vercel Serverless 有执行时间限制（Hobby 计划 10s，Pro 计划 60s）。周报生成（特别是勾选"实时抓官推"时）可能耗时 1~3 分钟，需要：
- 将 `/api/digest/run` 设计为**异步触发 + 轮询**模式（与本地版一致）
- 利用 Vercel 函数的 **streaming response**（SSE）推送实时日志

---

## 8. 前端适配项

### 8.1 API_BASE 检测逻辑

**当前代码**（`dashboard.html` 第 118 行）：
```javascript
const API_BASE = location.protocol === 'file:' ? 'http://127.0.0.1:8642' : '';
```

**问题**：当通过 Vercel 域名访问时，`location.protocol` 是 `https:`，所以 `API_BASE` = `''`，已经是相对路径，**实际上不需要改**。

**验证**：Vercel 部署后 `fetch('/api/games')` 会自动请求 `<vercel-domain>/api/games`，被 `vercel.json` 路由到 `api/index.py`。✅ 无需修改。

### 8.2 Buzz 加载逻辑适配

**当前代码**（`dashboard.html` `loadBuzz()`）：一次性调用 `/api/buzz`，期望返回全量数据或 `{building: true}` 轮询状态。

**需要改为**：
```javascript
async function loadBuzz(btn) {
  // 1. 获取游戏列表
  const meta = await api('/api/buzz');
  if (!meta.perGame) { /* 本地版兼容: 直接渲染 */ return renderBuzz(meta); }
  
  // 2. 逐游戏并发请求（控制并发数）
  const allTweets = [], allVideos = [];
  const queue = meta.games;
  const CONCURRENCY = 4;
  
  async function fetchOne(cn) {
    const d = await api('/api/buzz?game=' + encodeURIComponent(cn));
    allTweets.push(...(d.tweets || []));
    allVideos.push(...(d.videos || []));
  }
  
  // 并发执行
  for (let i = 0; i < queue.length; i += CONCURRENCY) {
    await Promise.all(queue.slice(i, i + CONCURRENCY).map(fetchOne));
  }
  
  // 3. 排序 + 渲染
  allTweets.sort((a, b) => (b.likes || 0) - (a.likes || 0));
  allVideos.sort((a, b) => (b.views || 0) - (a.views || 0));
  renderBuzz({tweets: allTweets, videos: allVideos, catLabels: meta.catLabels});
}
```

### 8.3 周报生成 UI 适配

- 当前代码已实现轮询模式（`pollDigest`），与 Vercel 异步触发模式兼容 ✅
- 需要确保 `/api/digest/run` 在 Vercel 上正确返回 `{building: true}` 并启动后台生成
- Vercel 版使用流式响应（SSE）替代轮询可进一步改善体验

---

## 9. 部署配置

### 9.1 vercel.json

```json
{
  "functions": {
    "api/index.py": {
      "runtime": "python@3.11",
      "maxDuration": 60
    }
  },
  "routes": [
    {
      "src": "/api/(.*)",
      "dest": "/api/index.py"
    },
    {
      "src": "/(.*)",
      "dest": "/dashboard.html"
    }
  ]
}
```

### 9.2 环境变量（Vercel Dashboard 配置）

| 变量名 | 说明 | 是否必需 |
|--------|------|:---:|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥，用于周报 AI 中文提炼 | 推荐（无则降级为日文原文） |

### 9.3 .gitignore 调整

```gitignore
# 旧: data/  reports/  →  移除整行
# 新:
data/snapshot.log
data/weekly.log
data/server.log
data/x_cache/

# 以下目录保留在 git 中:
# data/snapshots/  ← 每日快照 JSON
# data/versions.json
# data/events.json
# reports/         ← 周报存档
```

### 9.4 .vercelignore 新增

确保大文件不打包到 Serverless 函数中：
```
# 大文件
*.xlsx

# 本地工具（不需要部署）
snapshot.py
report.py
digest.py
resolve_ids.py
server.py

# 本地配置
config.local.json
.claude/
```

---

## 10. 运维流程

### 10.1 日常工作流

```
每日 12:10（launchd 定时触发或手动执行）
┌─────────────────────────────────────────────────┐
│  $ python3 snapshot.py                          │
│  $ git add data/                                │
│  $ git commit -m "snapshot 2026-08-02"          │
│  $ git push                                     │
│         │                                        │
│         ▼                                        │
│  GitHub → Vercel 自动部署                       │
│         │                                        │
│         ▼                                        │
│  浏览器打开 <project>.vercel.app                │
│  → 最新快照数据已上线，榜单/roadmap 可查看       │
└─────────────────────────────────────────────────┘

每周五 18:00（生成周报）
┌─────────────────────────────────────────────────┐
│  浏览器中操作：                                   │
│  1. 打开 <project>.vercel.app                   │
│  2. 切换到「📰 周报」Tab                         │
│  3. 可选勾选「实时抓官推」                        │
│  4. 点击「生成本周报」                            │
│  5. 等待 1~3 分钟（X 抓取 + AI 提炼）            │
│  6. 周报展示，点击「复制全文」→ 粘贴到飞书群     │
└─────────────────────────────────────────────────┘
```

### 10.2 竞品维护

添加新竞品：
1. 编辑 `config.json` 的 `games` 数组，添加新条目
2. （可选）用 `resolve_ids.py` 查找 App Store 的 `appId`
3. `git add config.json && git commit && git push`

### 10.3 launchd 保留（可选）

本地 Mac 的 launchd 定时任务（`com.jpgacha.snapshot`、`com.jpgacha.weekly`）可以保留，与 Vercel 部署**互不冲突**：
- Mac 唤醒时 launchd 仍然自动跑快照
- 手动 `git push` 后 Vercel 自动同步

---

## 11. 实施步骤

| 步骤 | 内容 | 涉及文件 | 预估改动 |
|:---:|------|------|:---:|
| 1 | 添加 `vercel.json` | 新建 | ~15 行 |
| 2 | 添加 `.vercelignore` | 新建 | ~15 行 |
| 3 | 调整 `.gitignore` | 修改 | ~5 行 |
| 4 | 前端 Buzz 加载改为逐游戏模式 | `dashboard.html` | ~50 行 |
| 5 | 前端 API_BASE 逻辑验证 | `dashboard.html` | 0 行（已验证兼容） |
| 6 | 周报接口在 api/index.py 中实现 | `api/index.py` | ~100 行 |
| 7 | 快照文件首次提交 | `data/snapshots/` | 已有文件 |
| 8 | Vercel Dashboard 配置环境变量 | — | 手动操作 |
| 9 | 连接 GitHub → Vercel 自动部署 | — | 手动操作 |
| 10 | 端到端测试 | — | — |

**总改动量**: ~185 行新代码，0 个新依赖。

---

## 12. 风险与边界

### 已知限制
- **Vercel Hobby 计划限制**：函数执行时间 10 秒上限。周报生成（含 X 实时抓取）可能超时。建议使用 **Vercel Pro（60s）** 或在本地生成周报后 push。
- **nitter.net 稳定性**：nitter 是第三方 X/Twitter 镜像，偶尔限流（429）或不可用。此时 X 数据展示旧缓存，Roadmap 和 Buzz 中 X 部分为空。
- **YouTube RSS 限流**：YouTube 对 RSS 接口有频率限制。逐游戏并发请求 Buzz 时（26 款 × 每款 1 次），若同时有其他用户访问可能触发限制。
- **快照空窗**：Mac 夜间睡眠期间 launchd 无法运行，次日需手动补跑快照。

### 后续优化
- Vercel Cron Jobs 替代 launchd（需 Pro 计划，每天 1 次免费）
- Vercel KV 替代 `data/versions.json` 文件（实时更新版本历史，不依赖 git push）
- 接入 Anthropic API（Claude）作为 AI 提炼的第二选项（当前只有 DeepSeek）
- Webhook 推送：周报生成后自动发送到飞书/钉钉/企业微信

---

> **文档版本**: v1.0 · **生成日期**: 2026-08-02 · **作者**: JP Gacha Monitor Team
