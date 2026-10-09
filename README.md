# study-crawler 学习资料收集爬虫

自动收集课程、电子书、图包、网盘分享链接，落到网盘客户端的同步目录里，剩下的上传交给网盘客户端。

## 架构

    config.toml（你的来源列表，不进 git）
           │
           ▼
        ┌─ App ──────────────────────┐
        │  forum    网页/论坛附件     │  CSS 选择器驱动，进帖子抓附件
        │  linkhub  网盘链接聚合站    │  只收集分享链接+提取码，写索引
        │  gallery  图站/图包         │  包装 gallery-dl
        │  video    课程/视频         │  包装 yt-dlp
        └────────────┬───────────────┘
                     ▼
         SQLite 去重（增量抓取的基础）
                     ▼
          sync_dir/（网盘客户端同步目录）
            ├── 课程/
            ├── 电子书/
            ├── 图包/
            └── _links/xxx.md   分享链接索引（手动转存用）

核心思路：**不重复造轮子**。图站和视频站交给 gallery-dl / yt-dlp 这两个现成的下载器（它们支持几千个站、自带断点和去重），自写代码只负责它们覆盖不了的：论坛附件抓取、网盘链接收集、去重调度、文件归档。

## 安装

    cd study-crawler
    pip install -r requirements.txt

图包 / 视频来源需要再装（可选）：

    pip install gallery-dl yt-dlp

## 快速开始

    python -m studycrawler init      # 生成 config.toml
    # 编辑 config.toml：改 sync_dir、填来源 URL、enabled = true
    python -m studycrawler list      # 看来源列表
    python -m studycrawler crawl     # 抓一轮
    python -m studycrawler watch     # 常驻定时增量抓取

## 配置说明

复制 `config.example.toml` 为 `config.toml` 后编辑。关键项：

- `settings.sync_dir`：网盘客户端的同步目录。爬虫只负责把文件放进来，上传由客户端自动完成。任何网盘（百度/阿里/夸克/OneDrive）都这样接。
- `[[source]]`：一个来源一条。`type` 四选一：

| type | 适用 | 说明 |
|---|---|---|
| `forum` | 电子书站、资源论坛 | 需要配 CSS 选择器（F12 看页面结构） |
| `linkhub` | 网盘链接聚合站 | 收集分享链接+提取码到 `_links/` 索引，不下载文件 |
| `gallery` | 图站、图包 | 走 gallery-dl |
| `video` | 课程、视频 | 走 yt-dlp |

forum 的选择器示例：

    [[source]]
    name = "某论坛"
    type = "forum"
    url = "https://xxx.com/list.html"
    category = "电子书"
    [source.selectors]
    item = "div.post"     # 列表页每个条目的容器
    title = "h3 a"        # 标题（相对条目）
    link  = "h3 a"        # 链接（相对条目，必填）

选择器写不对就跑一次 `crawl`，看日志报"解析出 0 个条目"再调。

## 关键词找站（hunt 模式）

给一个关键词，爬虫自己去搜、去探、顺手收割：

    python -m studycrawler hunt "AI绘画教程 网盘" --pages 1 --sites 6
    python -m studycrawler hunt "claude 教程" --adopt    # 最优站自动转正为长期来源

流程：bing/baidu/ddg 搜索 -> 候选站逐个探测打分（关键词命中×2 + 文件直链×3 + 网盘链接×2）-> 高分站限量收割（文件直链直接下、网盘链接进索引、video/gallery 候选调引擎）-> `_hunt/` 下出 Markdown 报告，附建议的 source 配置。

> **重要结论**（2026-10-08 实测定档，详见 docs/网盘搜索站决策.md）：通用搜索引擎直接爬关键词**实测效果不行**（返回工具站/官网/登录墙落地页）；主力是**网盘搜索引擎**——`pansearch` 已内置为默认引擎（返回的分享链接+提取码直接进 `_hunt/<关键词>/分享链接.md` 索引，2026-10-09 实测板绘 10 条/笔刷 15 条），bing 兜底，B 站等视频路径由 video 引擎收割不受影响。

实跑经验：

- **关键词要带资源意图**。"绘画 AI提效 claude" 搜出来的是工具站；"AI绘画教程 网盘" 才是找资料的问法（hunt 也会自动补一个"关键词+网盘"变体）。
- **深挖两层**：对高分候选站做有界聚焦爬（顺着"下载/资源/网盘/合集"味的内页链接），把散在详情页的文件直链和网盘链接挖出来。
- **引擎收割**：video/gallery 候选自动调 yt-dlp / gallery-dl 限量下载样本（视频 480p 一集、图站 10 张）。yt-dlp 合流需要 ffmpeg：装系统版，或 `pip install imageio-ffmpeg` 免管理员解决。
- **通用引擎找网盘资源很弱**。真正的威力在 `[[hunt.engine]]` 自定义搜索源——把你信任的网盘搜索站配置进去，hunt 就会拿它当主力引擎。
- 百度对数据中心/公司 IP 常弹安全验证，属正常降级，bing/ddg 会兜底。
- 收割有双重门槛（关键词至少命中 1 个 + 总分≥8），探测到关键词不相关但挂满 PDF 的站不会误抓。
- `--adopt` 只自动转正 video/gallery/pan 类候选；forum 站要人肉配选择器，报告里给了配置块模板。

## 两种运行方式

- 手动：`python -m studycrawler crawl` 或 `python -m studycrawler crawl 来源名`
- 定时增量：`python -m studycrawler watch`（间隔在 `watch_interval_min` 配）。挂在后台即可；Windows 开机自启可用任务计划程序，操作为 `python -m studycrawler watch`，起始于本项目目录。

## 关键词输入模型（日常主力）

三要素：**关键词 / 存储路径 / 文件数**。

一次性：

    python -m studycrawler hunt "板绘 笔刷" --files 20 --out "D:/收集"

常驻（最常用）：

    python -m studycrawler watch --files 20 --out "D:/收集"

watch 每轮做两件事：跑 config 里的 sources，然后读**关键词文件**（默认项目根目录 `keywords.txt`，可用 `--keywords` 换路径）：

```
# 一行一个关键词，可含空格短语，# 注释
板绘
笔刷
```

新关键词自动 hunt 一次（`--files` 个文件、落到 `--out`），SQLite 记录已处理的词不会重复；往文件里加一行就是排一个新任务——外部脚本/hook 往里写关键词即可触发采集。想让全部关键词重抓，删 `data/state.db`。

`watch --once` 只跑一轮就退出（适合配合外部定时器）。

## 双击脚本（Windows）

项目根目录三个 bat，双击即用：

- `找资料.bat` —— 问你三件事（关键词/存储路径/文件数），立即 hunt
- `抓一轮.bat` —— 跑一次 crawl（全部启用的源）
- `常驻监控.bat` —— 启动 watch（含关键词文件监控）

bat 内容是纯 ASCII（避免 cmd 编码坑），交互提示为英文，参数含义同上表。

去重靠 SQLite（`data/state.db`）：同一 URL 成功处理过就跳过，失败的下轮自动重试。删掉 state.db 就是全量重抓。

## 两台电脑之间迁移（公司试跑 -> 个人机部署）

代码在 git 里，数据和配置都不在：

    # 个人电脑上
    git clone <你的私有仓库地址> study-crawler
    cd study-crawler
    pip install -r requirements.txt
    pip install gallery-dl yt-dlp        # 可选
    python -m studycrawler init           # 生成新的 config.toml
    # 编辑 config.toml：sync_dir 改成网盘同步目录的真实路径

注意 `config.toml`、`data/` 都被 .gitignore 排除了——个人来源列表和下载记录不会推到仓库，两台机器各有一份配置互不干扰。

## 注意事项

- 只抓公开可访问、允许下载的内容，尊重站点条款，别碰付费盗版资源。
- `request_delay` 保持默认 2 秒以上，别对站点造成压力。
- 公司电脑上试跑时建议 `sync_dir` 用默认的 `./data/sync`，别把个人资料下载到公司机器。
- 遇到反爬（403/验证码）的站，优先看 gallery-dl / yt-dlp 是否已支持（它们处理了大部分站点的认证与限流）。
