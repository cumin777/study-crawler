"""配置加载：读取项目根目录的 config.toml。

config.toml 不进 git（含个人路径和来源），仓库里只放 config.example.toml 模板。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import tomllib  # Python 3.11+

PROJECT_ROOT = Path(__file__).resolve().parents[1]

VALID_TYPES = {"forum", "linkhub", "gallery", "video"}

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class Source:
    """一个抓取来源，对应 config.toml 里的一个 [[source]]。"""

    name: str
    type: str  # forum / linkhub / gallery / video
    url: str
    category: str = "未分类"      # 同步目录下的子文件夹名
    enabled: bool = True
    follow_detail: bool = True    # forum: 是否进入帖子详情页找附件
    max_items: int = 30           # forum: 每轮最多处理的新条目数
    selectors: dict = field(default_factory=dict)  # forum: CSS 选择器


@dataclass
class Settings:
    sync_dir: Path
    db_path: Path
    request_delay: float = 2.0    # 对同一站点的请求间隔（秒）
    watch_interval_min: int = 30  # watch 模式轮询间隔（分钟）
    timeout: int = 20
    user_agent: str = DEFAULT_UA


@dataclass
class HuntSettings:
    """hunt 关键词找站模式的配置。"""

    engines: list[str] = field(default_factory=lambda: ["bing", "baidu"])
    custom_engines: list[dict] = field(default_factory=list)  # 任意搜索源
    max_sites: int = 10           # 每次最多探测的站点数
    max_files_per_site: int = 5   # 每站最多直接下载的文件数
    blocklist: list[str] = field(default_factory=lambda: [
        "zhihu.com", "weibo.com", "tieba.baidu.com",
        "douyin.com", "baijiahao.baidu.com",
    ])


@dataclass
class AppConfig:
    settings: Settings
    sources: list[Source]
    hunt: HuntSettings = field(default_factory=HuntSettings)


def _resolve(p: str | Path) -> Path:
    """相对路径一律相对项目根目录解析，绝对路径原样保留。"""
    p = Path(p)
    return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()


def load_config(path: Path | None = None) -> AppConfig:
    path = Path(path) if path else PROJECT_ROOT / "config.toml"
    if not path.exists():
        raise FileNotFoundError(
            f"找不到配置文件 {path}\n先运行: python -m studycrawler init 生成后编辑"
        )
    raw = tomllib.loads(path.read_text(encoding="utf-8"))

    s = raw.get("settings", {})
    settings = Settings(
        sync_dir=_resolve(s.get("sync_dir", "./data/sync")),
        db_path=_resolve(s.get("db_path", "./data/state.db")),
        request_delay=float(s.get("request_delay", 2.0)),
        watch_interval_min=int(s.get("watch_interval_min", 30)),
        timeout=int(s.get("timeout", 20)),
        user_agent=str(s.get("user_agent", DEFAULT_UA)),
    )

    sources: list[Source] = []
    for item in raw.get("source", []):
        src = Source(
            name=str(item["name"]),
            type=str(item["type"]),
            url=str(item["url"]),
            category=str(item.get("category", "未分类")),
            enabled=bool(item.get("enabled", True)),
            follow_detail=bool(item.get("follow_detail", True)),
            max_items=int(item.get("max_items", 30)),
            selectors=dict(item.get("selectors", {})),
        )
        if src.type not in VALID_TYPES:
            raise ValueError(
                f"来源 [{src.name}] 类型 {src.type} 无效，可选: {sorted(VALID_TYPES)}"
            )
        sources.append(src)

    h = raw.get("hunt", {})
    hunt = HuntSettings(
        engines=list(h.get("engines", ["bing", "baidu"])),
        custom_engines=[dict(e) for e in h.get("engine", [])],
        max_sites=int(h.get("max_sites", 10)),
        max_files_per_site=int(h.get("max_files_per_site", 5)),
        blocklist=list(h.get("blocklist", HuntSettings().blocklist)),
    )

    return AppConfig(settings=settings, sources=sources, hunt=hunt)


DEFAULT_CONFIG_TOML = """\
# 学习资料爬虫配置
# 改名/复制为 config.toml 后生效；config.toml 已被 git 忽略，不会提交

[settings]
# 网盘客户端的同步目录。爬虫下载到这里，客户端负责自动上传。
# 个人电脑上改成真实同步目录，例如 "D:/BaiduSync/学习资料"
sync_dir = "./data/sync"
# 去重状态库位置
db_path = "./data/state.db"
# 对同一站点的请求间隔秒数（别调太小，对站点礼貌一点）
request_delay = 2.0
# watch 模式轮询间隔（分钟）
watch_interval_min = 30
# HTTP 超时（秒）
timeout = 20

# ---------- hunt 关键词找站 ----------
[hunt]
# 用哪些搜索引擎（bing / baidu / ddg）
engines = ["bing", "baidu"]
# 每次最多探测的站点数
max_sites = 10
# 每站最多直接下载的文件数（防失控）
max_files_per_site = 5
# 探测时跳过这些域名（登录墙/无文件可抓的站）
blocklist = [
    "zhihu.com", "weibo.com", "tieba.baidu.com",
    "douyin.com", "baijiahao.baidu.com",
]

# 自定义搜索源（可选）：url 里 {query} 换成关键词、{page} 换成页码
# [[hunt.engine]]
# name = "我的搜索站"
# url = "https://example.com/search?q={query}&p={page}"
# item = "div.result"
# link = "a"
# title = "a"

# ---------- 来源 ----------
# type 四选一：
#   forum   = 网页/论坛：CSS 选择器驱动，抓附件和直链文件（电子书站、资源论坛）
#   linkhub = 网盘链接聚合站：只收集分享链接+提取码，写进 _links/ 的 Markdown 索引
#   gallery = 图站/图包：调用 gallery-dl（需 pip install gallery-dl）
#   video   = 课程/视频：调用 yt-dlp（需 pip install yt-dlp）

[[source]]
name = "示例-论坛"
type = "forum"
url = "https://example.com/list"
category = "电子书"        # 同步目录下的子文件夹名
enabled = false            # 改成 true 并换成真实 URL 后启用
follow_detail = true       # 进入帖子详情页找附件
max_items = 30
[source.selectors]         # 打开列表页 F12 看结构后填写
item = "div.post"          # 每个条目的容器
title = "h3 a"             # 标题（相对条目，可省略）
link = "h3 a"              # 链接（相对条目，必填）

[[source]]
name = "示例-链接站"
type = "linkhub"
url = "https://example.com/pan"
category = "_links"
enabled = false

[[source]]
name = "示例-图包"
type = "gallery"
url = "https://example.com/album/123"
category = "图包"
enabled = false

[[source]]
name = "示例-课程"
type = "video"
url = "https://example.com/course/1"
category = "课程"
enabled = false
"""
