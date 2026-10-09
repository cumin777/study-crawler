"""关键词找站（hunt）：搜索 -> 探测打分 -> 顺手收割 -> 出报告。

用法:
    python -m studycrawler hunt "绘画 AI提效 claude"
    python -m studycrawler hunt "xxx" --pages 1 --sites 6
    python -m studycrawler hunt "xxx" --adopt   # 最优站自动转正为长期 source

流程:
  1. 搜: bing / baidu / duckduckgo + config 里的自定义搜索源
  2. 探: 逐个访问候选站，按 关键词命中*2 + 文件直链*3 + 网盘链接*2 打分
  3. 收: 高分站的文件直链限量下载，网盘链接进索引
  4. 报: _hunt/ 下出 Markdown 报告，含排序与建议的 source 配置
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from .engines.forum import file_links_in_page
from .engines.linkhub import SHARE_PATTERNS, extract_links
from .engines.video import ffmpeg_args
from .storage import category_dir, count_files, safe_filename

log = logging.getLogger("crawler")

# 收割门槛：关键词至少命中这么多、总分至少这么多才动手下载
HARVEST_MIN_KW = 1
HARVEST_MIN_SCORE = 8

# 深挖层数/每站页数上限/最多深挖几个候选
DEEP_DEPTH = 2
DEEP_MAX_PAGES = 8
DEEP_SITES = 3

# 引擎收割限额（hunt 是探路不是搬家，下到样本就停）
HUNT_VIDEO_LIMIT = 1      # 最多下几个视频
HUNT_VIDEO_HEIGHT = 480   # 视频分辨率上限
HUNT_GALLERY_LIMIT = 10   # 图站最多下几张

# 引导深爬的"资源味"链接特征
DEEP_HINT = re.compile(
    r"download|resource|free|教程|资源|下载|打包|合集|网盘|素材|提取|盘",
    re.I,
)

# 预览图类后缀：收割时排后面，防止拿示例图凑数
WEAK_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}


def _strong_first(urls: list[str]) -> list[str]:
    """压缩包/笔刷/文档等强类型排前面，预览图排后面。"""
    strong = [
        u for u in urls
        if Path(urlparse(u).path).suffix.lower() not in WEAK_EXTS
    ]
    weak = [u for u in urls if u not in strong]
    return strong + weak

# 已知站点的收录建议
SUGGEST_TYPES = [
    (re.compile(r"bilibili\.com|youtube\.com", re.I), "video"),
    (re.compile(r"pixiv\.net|twitter\.com|x\.com|danbooru|safebooru|yande\.re", re.I), "gallery"),
    (re.compile(r"pan\.baidu\.com|quark|aliyundrive|alipan|123pan|lanzou", re.I), "pan"),
]


def suggest_type(url: str) -> str:
    """按域名猜测这个站该收录成哪种 source 类型。"""
    for pat, kind in SUGGEST_TYPES:
        if pat.search(url):
            return kind
    return "forum"


# ---------- 关键词队列（watch 模式用） ----------

KEYWORD_SOURCE = "__keywords__"


def read_keywords(path) -> list[str]:
    """关键词文件：一行一个关键词（可含空格短语），# 开头是注释。"""
    path = Path(path)
    if not path.exists():
        return []
    return [
        ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def query_done(app, query: str) -> bool:
    """这个关键词是否已经被 hunt 处理过（搜索成功即算）。"""
    return app.db.seen(f"query:{query}", KEYWORD_SOURCE)


def mark_query_done(app, query: str) -> None:
    app.db.mark(f"query:{query}", KEYWORD_SOURCE, title=query, status="done")


# ---------- 搜索源 ----------

def _hits_from_selectors(html, base_url, item_sel, link_sel, title_sel=""):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for node in soup.select(item_sel):
        a = node.select_one(link_sel) if link_sel else None
        if a is None or not a.get("href"):
            continue
        t = node.select_one(title_sel) if title_sel else a
        out.append(
            {"title": (t or a).get_text(" ", strip=True), "url": urljoin(base_url, a["href"])}
        )
    return out


def _ddg_real_url(href: str) -> str:
    """ddg 的结果链接是 /l/?uddg=<编码后真实地址> 跳转。"""
    if href.startswith("//") or href.startswith("/l/"):
        href = urljoin("https://duckduckgo.com", href)
    q = parse_qs(urlparse(href).query).get("uddg")
    return unquote(q[0]) if q else href


def search_bing(query, pages, fetcher):
    hits = []
    for p in range(pages):
        url = f"https://cn.bing.com/search?q={quote(query)}&first={p * 10 + 1}"
        try:
            html = fetcher.get_text(url)
        except Exception as exc:
            log.warning("bing 第 %d 页失败: %r", p + 1, exc)
            break
        hits += _hits_from_selectors(html, url, "li.b_algo", "h2 a", "h2 a")
    return hits


def search_baidu(query, pages, fetcher):
    hits = []
    for p in range(pages):
        url = f"https://www.baidu.com/s?wd={quote(query)}&pn={p * 10}"
        try:
            html = fetcher.get_text(url)
        except Exception as exc:
            log.warning("baidu 第 %d 页失败: %r", p + 1, exc)
            break
        if "安全验证" in html:
            log.warning("baidu 要求安全验证，本台机器/IP 暂时搜不了")
            break
        # 结果链接是 baidu 跳转链接，探测访问时会自动落到真实地址
        hits += _hits_from_selectors(html, url, "div.result, div.c-container", "h3 a", "h3 a")
    return hits


def search_ddg(query, pages, fetcher):
    hits = []
    for p in range(pages):
        url = f"https://html.duckduckgo.com/html/?q={quote(query)}&s={p * 30}"
        try:
            html = fetcher.get_text(url)
        except Exception as exc:
            log.warning("ddg 第 %d 页失败: %r", p + 1, exc)
            break
        for h in _hits_from_selectors(html, url, "div.result", "a.result__a"):
            h["url"] = _ddg_real_url(h["url"])
            hits.append(h)
    return hits


def search_custom(eng_cfg, query, pages, fetcher):
    """config [[hunt.engine]] 里配的任意搜索源。

    url 模板里 {query} 会被替换成编码后的关键词，{page} 是页码（从 1 起）。
    """
    hits = []
    for p in range(pages):
        url = (
            eng_cfg["url"]
            .replace("{query}", quote(query))
            .replace("{page}", str(p + 1))
        )
        try:
            html = fetcher.get_text(url)
        except Exception as exc:
            log.warning("[%s] 第 %d 页失败: %r", eng_cfg.get("name", "?"), p + 1, exc)
            break
        hits += _hits_from_selectors(
            html, url,
            eng_cfg.get("item", "div.result"),
            eng_cfg.get("link", "a"),
            eng_cfg.get("title", ""),
        )
    return hits


SEARCHERS = {"bing": search_bing, "baidu": search_baidu, "ddg": search_ddg}

# 通用引擎（会自动追加"关键词+网盘"变体）；网盘搜索引擎不需要
GENERAL_ENGINES = {"bing", "baidu", "ddg"}


def _is_pan_link(url: str) -> bool:
    return any(p.search(url) for p in SHARE_PATTERNS)


# ---------- 网盘搜索引擎（返回的命中本身就是分享链接，成品直达） ----------

def _parse_pansearch_item(content: str, fallback_title: str) -> dict | None:
    """pansearch 条目的 content 是 HTML 片段：名称 + <a href> + pwd 参数。"""
    m_href = re.search(r'href="([^"]+)"', content)
    if not m_href:
        return None
    url = m_href.group(1)
    m_pwd = re.search(r'[?&]pwd=([^"&#]+)', content)
    m_title = re.search(r"名称：([^<\n]+)", content)
    title = m_title.group(1).strip() if m_title else fallback_title
    return {"title": title, "url": url, "code": m_pwd.group(1) if m_pwd else ""}


def search_pansearch(query, pages, fetcher):
    """盘搜 pansearch.me：Next.js 数据接口，先取 buildId 再查询。"""
    try:
        html = fetcher.get_text("https://www.pansearch.me/search")
    except Exception as exc:
        log.warning("[pansearch] 取 buildId 失败: %r", exc)
        return []
    m = re.search(r'"buildId":"([^"]+)"', html)
    if not m:
        log.warning("[pansearch] 页面没找到 buildId（站点可能改版）")
        return []
    build_id = m.group(1)
    out = []
    for p in range(max(1, pages)):
        url = (
            f"https://www.pansearch.me/_next/data/{build_id}/search.json"
            f"?keyword={quote(query)}&offset={p * 10}"
        )
        try:
            data = fetcher.get_json(url)
        except Exception as exc:
            log.warning("[pansearch] 第 %d 页失败: %r", p + 1, exc)
            break
        items = data.get("pageProps", {}).get("data", {}).get("data") or []
        for it in items:
            hit = _parse_pansearch_item(it.get("content", ""), query)
            if hit:
                out.append(hit)
    return out


def search_qupansou(query, pages, fetcher):
    """去盘搜 funletu：一个 POST JSON 接口直接出结果。"""
    body = {
        "style": "get",
        "datasrc": "search",
        "query": {
            "id": "", "datetime": "", "courseid": 1, "categoryid": "",
            "filetypeid": "", "filetype": "", "reportid": "", "validid": "",
            "searchtext": query,
        },
        "page": {"pageSize": 100, "pageIndex": 1},
        "order": {"prop": "sort", "order": "desc"},
        "message": "请求资源列表数据",
    }
    try:
        resp = fetcher.post_json(
            "https://v.funletu.com/search", body,
            headers={"referer": "https://pan.funletu.com/"},
        )
    except Exception as exc:
        log.warning("[qupansou] 请求失败: %r", exc)
        return []
    if resp.get("status") != 200:
        log.warning("[qupansou] 返回异常 status=%s", resp.get("status"))
        return []
    out = []
    for it in resp.get("data") or []:
        url = it.get("url") or ""
        if not url:
            continue
        title = re.sub(r"<[^>]+>", "", it.get("title") or "").strip()
        m_pwd = re.search(r"[?&]pwd=([^&#]+)", url)
        out.append({"title": title, "url": url, "code": m_pwd.group(1) if m_pwd else ""})
    return out


SEARCHERS.update({"pansearch": search_pansearch, "qupansou": search_qupansou})


# ---------- 探测与收割 ----------

class Hunt:
    def __init__(self, app):
        self.app = app
        self.h = app.cfg.hunt

    def _blocked(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return any(b in host for b in self.h.blocklist)

    def run(
        self,
        query: str,
        pages: int = 2,
        top: int = 0,
        files_cap: int = 0,
        out: str | None = None,
    ) -> tuple[Path, list[dict]]:
        """files_cap: 本次最多下载的文件数；out: 指定输出目录（默认 sync_dir/_hunt）。"""
        top = top or self.h.max_sites
        terms = [t.lower() for t in query.split() if t.strip()]
        log.info("hunt 启动: 关键词=%r, 引擎=%s", query, self.h.engines)

        # 1) 搜索。通用引擎自动补一个"网盘"意图变体；网盘搜索引擎不需要
        pan_direct: dict[str, dict] = {}  # url -> hit（成品分享链接，直接进索引）
        site_hits: dict[str, dict] = {}   # domain -> hit（普通网页，走探测打分）
        for name in self.h.engines:
            fn = SEARCHERS.get(name)
            if fn is None:
                log.warning("未知搜索引擎: %s (可选: %s)", name, sorted(SEARCHERS))
                continue
            if name in GENERAL_ENGINES and not re.search(r"网盘|下载|磁力", query):
                qs = [query, f"{query} 网盘"]
            else:
                qs = [query]
            for q in qs:
                try:
                    got = fn(q, pages, self.app.fetcher)
                except Exception as exc:
                    log.warning("[%s] 搜索异常: %r", name, exc)
                    continue
                log.info("[%s] %r 拿到 %d 条结果", name, q, len(got))
                for h in got:
                    if _is_pan_link(h["url"]):
                        pan_direct.setdefault(h["url"], h)
                    else:
                        domain = urlparse(h["url"]).netloc
                        if domain:
                            site_hits.setdefault(domain, h)
        for eng_cfg in self.h.custom_engines:
            for h in search_custom(eng_cfg, query, pages, self.app.fetcher):
                if _is_pan_link(h["url"]):
                    pan_direct.setdefault(h["url"], h)
                else:
                    domain = urlparse(h["url"]).netloc
                    if domain:
                        site_hits.setdefault(domain, h)

        candidates = [h for h in site_hits.values() if not self._blocked(h["url"])][:top]
        log.info("站点候选 %d 个，直连分享链接 %d 条", len(candidates), len(pan_direct))

        # 2) 探测打分
        results = []
        for h in candidates:
            r = self._probe(h, terms)
            if r:
                results.append(r)
        results.sort(key=lambda r: r["score"], reverse=True)

        # 2.5) 深挖：对高分候选做有界聚焦爬，把散在内页的文件/网盘链接挖出来。
        # video/gallery 站不深爬——那是 gallery-dl/yt-dlp 引擎的主场。
        for r in results[:DEEP_SITES]:
            if r["suggest"] in ("video", "gallery", "pan"):
                continue
            try:
                pages_seen, files, pans = self._deep_crawl(r["url"], terms)
            except Exception as exc:
                log.warning("深挖失败 %s: %r", r["url"], exc)
                continue
            have_f = set(r["files"])
            r["files"] += [f for f in files if f not in have_f]
            have_p = {p["link"] for p in r["pans"]}
            r["pans"] += [p for p in pans if p["link"] not in have_p]
            log.info("深挖 %s (%d 页): +文件 %d, +网盘 %d",
                     urlparse(r["url"]).netloc, len(pages_seen), len(files), len(pans))

        # 关键词本轮已处理（搜索成功即算，即使没收到东西）；搜索全挂则下轮重试
        if candidates or pan_direct:
            mark_query_done(self.app, query)

        # 3) 收割 + 4) 报告。out 指定时整套结果落在 out 下，否则在 sync_dir/_hunt
        base = Path(out).expanduser().resolve() if out else None
        qdir = safe_filename(query)
        if base:
            cat = base / qdir
            cat.mkdir(parents=True, exist_ok=True)
        else:
            cat = category_dir(self.app.cfg.settings, f"_hunt/{qdir}")

        # 3.1) 网盘搜索引擎返回的成品分享链接：不探测，直接进索引
        n_pans = self._collect_direct(query, pan_direct, cat)

        n_files, n_pans_site, pan_index = self._harvest(query, results, cat, files_cap)
        n_pans += n_pans_site

        # 3.5) video/gallery 候选交给现成引擎收割（限量）
        n_files += self._engine_harvest(query, results, cat, files_cap)

        stamp = datetime.now().strftime("%Y-%m-%d")
        if base:
            report = base / f"{qdir}-{stamp}.md"
        else:
            report = category_dir(self.app.cfg.settings, "_hunt") / f"{qdir}-{stamp}.md"
        self._report(report, query, results, cat, n_files, n_pans)
        log.info("报告已写入 %s", report)
        return report, results

    def _probe(self, hit: dict, terms: list[str]) -> dict | None:
        url = hit["url"]
        try:
            resp = self.app.fetcher.get(url)
        except Exception as exc:
            log.info("探测失败 %s: %r", url, exc)
            return None
        final_url = str(resp.url)  # 解析搜索引擎的跳转链接
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.get_text(strip=True) if soup.title else hit["title"]
        text = soup.get_text(" ", strip=True).lower()
        kw = sum(1 for t in terms if t in text)
        files = file_links_in_page(html, final_url)
        pans = extract_links(html)
        score = kw * 2 + len(files) * 3 + len(pans) * 2
        log.info("探测 %s -> 分数 %d (关键词%d 文件%d 网盘%d)",
                 urlparse(final_url).netloc, score, kw, len(files), len(pans))
        return {
            "url": final_url, "title": title, "kw": kw,
            "files": files, "pans": pans, "score": score,
            "suggest": suggest_type(final_url),
        }

    def _deep_crawl(self, start_url: str, terms: list[str]):
        """有界聚焦爬：顺着"资源味"或含关键词的站内链接往深处挖，汇总文件直链和网盘链接。

        层数和总页数都有上限，不会失控。
        """
        visited: set[str] = set()
        files: list[str] = []
        files_seen: set[str] = set()
        pans: list[dict] = []
        pans_seen: set[str] = set()
        frontier = [(start_url, 0)]
        host = urlparse(start_url).netloc

        while frontier and len(visited) < DEEP_MAX_PAGES:
            url, depth = frontier.pop(0)
            if url in visited:
                continue
            visited.add(url)
            try:
                resp = self.app.fetcher.get(url)
            except Exception:
                continue
            html = resp.text
            base = str(resp.url)
            for f in file_links_in_page(html, base):
                if f not in files_seen:
                    files_seen.add(f)
                    files.append(f)
            for p in extract_links(html):
                if p["link"] not in pans_seen:
                    pans_seen.add(p["link"])
                    pans.append(p)
            if depth >= DEEP_DEPTH:
                continue
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                href = urljoin(base, a["href"])
                p = urlparse(href)
                if p.netloc != host:
                    continue
                if p.path.lower().endswith(
                    (".css", ".js", ".png", ".jpg", ".jpeg", ".svg", ".ico", ".webp", ".gif")
                ):
                    continue
                text = a.get_text(" ", strip=True)
                path_l = unquote(p.path).lower()
                promising = (
                    DEEP_HINT.search(p.path)
                    or DEEP_HINT.search(text)
                    or any(t in text.lower() for t in terms)
                    or any(t in path_l for t in terms)
                )
                if promising and href not in visited:
                    frontier.append((href, depth + 1))
        return visited, files, pans

    def _collect_direct(self, query: str, pan_direct: dict, cat: Path) -> int:
        """网盘搜索引擎直接返回的分享链接：不探测不下载，进索引即交付。"""
        src_name = f"hunt:{query}"
        fresh = [
            h for u, h in pan_direct.items()
            if not self.app.db.seen(u, src_name)
        ]
        if not fresh:
            return 0
        index = cat / "分享链接.md"
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        is_new = not index.exists()
        with open(index, "a", encoding="utf-8") as f:
            if is_new:
                f.write(f"# hunt 收集的分享链接: {query}\n\n")
            for h in fresh:
                code = f"  提取码 {h['code']}" if h.get("code") else ""
                title = (h.get("title") or "").strip() or "(无标题)"
                f.write(f"- {now} | {title[:80]} | {h['url']}{code}\n")
        for h in fresh:
            self.app.db.mark(
                h["url"], src_name, title=h.get("title", ""), status="done",
                path=str(index),
            )
        log.info("[%s] 直连分享链接 %d 条 -> %s", query, len(fresh), index)
        return len(fresh)

    def _engine_harvest(self, query, results, cat: Path, files_cap: int) -> int:
        """video/gallery 候选直接调 yt-dlp / gallery-dl 收割，严格限量。

        每种类型只取排名最高的一站—— hunt 要的是样本和入口，不是搬家。
        """
        n = 0
        for r in results:
            if files_cap and n >= files_cap:
                break
            if r["suggest"] == "video":
                exe = shutil.which("yt-dlp")
                if not exe:
                    log.warning("未装 yt-dlp，跳过视频收割: pip install yt-dlp")
                    break
                log.info("[引擎收割] yt-dlp <- %s", r["url"])
                proc = subprocess.run(
                    [
                        exe, "-P", str(cat), "-o", "%(title)s [%(id)s].%(ext)s",
                        "-f", f"bv*[height<={HUNT_VIDEO_HEIGHT}]+ba/b[height<={HUNT_VIDEO_HEIGHT}]",
                        "--playlist-items", f"1:{HUNT_VIDEO_LIMIT}",
                        "--retries", "5", *ffmpeg_args(), r["url"],
                    ],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                )
                if proc.returncode == 0:
                    n += HUNT_VIDEO_LIMIT
                else:
                    tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
                    log.warning("yt-dlp 失败 %s: %s", r["url"], " | ".join(tail))
                break
            if r["suggest"] == "gallery":
                exe = shutil.which("gallery-dl")
                if not exe:
                    log.warning("未装 gallery-dl，跳过图站收割: pip install gallery-dl")
                    break
                log.info("[引擎收割] gallery-dl <- %s", r["url"])
                before = count_files(cat)
                proc = subprocess.run(
                    [exe, "--destination", str(cat),
                     "--range", f"1-{HUNT_GALLERY_LIMIT}", r["url"]],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                )
                got = max(0, count_files(cat) - before)
                n += got
                if proc.returncode != 0:
                    tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
                    log.warning("gallery-dl 失败 %s: %s", r["url"], " | ".join(tail))
                break
        return n

    def _harvest(self, query, results, cat: Path, files_cap: int = 0):
        """限量收割：files_cap 是本次总下载上限，per-site 上限取配置。"""
        pan_index = cat / "分享链接.md"
        src_name = f"hunt:{query}"
        n_files = n_pans = 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        for r in results:
            if files_cap and n_files >= files_cap:
                break
            if r["kw"] < HARVEST_MIN_KW or r["score"] < HARVEST_MIN_SCORE:
                continue
            for url in _strong_first(r["files"]):
                if files_cap and n_files >= files_cap:
                    break
                if self.app.db.seen(url, src_name):
                    continue
                try:
                    self.app.fetcher.download(url, cat, fallback_stem=Path(urlparse(url).path).stem)
                    self.app.db.mark(url, src_name, title=r["title"], status="done")
                    n_files += 1
                except Exception as exc:
                    log.warning("下载失败 %s: %r", url, exc)
            if r["pans"]:
                is_new = not pan_index.exists()
                with open(pan_index, "a", encoding="utf-8") as f:
                    if is_new:
                        f.write(f"# hunt 收集的分享链接: {query}\n\n")
                    for p in r["pans"]:
                        if self.app.db.seen(p["link"], src_name):
                            continue
                        code = f"  提取码 {p['code']}" if p["code"] else ""
                        f.write(f"- {now} | {r['title']} | {p['link']}{code}\n")
                        self.app.db.mark(p["link"], src_name, title=r["title"], status="done")
                        n_pans += 1
        hit_cap = files_cap and n_files >= files_cap
        log.info("[%s] 收割文件 %d 个%s", query, n_files,
                 f"（已达上限 {files_cap}）" if hit_cap else "")
        return n_files, n_pans, pan_index

    def _report(self, report: Path, query, results, harvest_dir, n_files, n_pans) -> Path:
        stamp = datetime.now().strftime("%Y-%m-%d")
        lines = [
            f"# hunt 报告: {query}",
            f"日期: {stamp}  引擎: {', '.join(self.h.engines)}  候选: {len(results)} 站",
            "",
            "| # | 分数 | 关键词 | 文件 | 网盘 | 类型建议 | 标题 | URL |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for i, r in enumerate(results, 1):
            lines.append(
                f"| {i} | {r['score']} | {r['kw']} | {len(r['files'])} | "
                f"{len(r['pans'])} | {r['suggest']} | {r['title'][:40]} | {r['url']} |"
            )
        lines += [
            "",
            f"## 已自动收集（落在 {harvest_dir}）",
            f"- 文件: {n_files} 个",
            f"- 网盘链接: {n_pans} 条 -> 分享链接.md",
            "",
            "## 建议收录为长期来源",
            "把下面的块复制进 config.toml（forum 类型还要 F12 补选择器）：",
            "",
        ]
        for r in results[:2]:
            if r["suggest"] in ("video", "gallery", "pan"):
                lines.append(f"### {r['title'][:50]}")
                lines.append(_source_block(r["suggest"], r["url"]))
                lines.append("")
        report.write_text("\n".join(lines), encoding="utf-8")
        return report

    def adopt(self, results: list[dict], cfg_path: Path) -> str | None:
        """把最优的 video/gallery/pan 候选追加进 config.toml，返回其 URL。

        forum 类型不自动转正——选择器需要人肉对着页面调。
        """
        best = next(
            (r for r in results if r["suggest"] in ("video", "gallery", "pan")),
            None,
        )
        if best is None:
            log.info("没有可自动转正的候选（forum 需要人肉配选择器）")
            return None
        text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
        if f'url = "{best["url"]}"' in text:
            log.info("%s 已在 config.toml 里，跳过", best["url"])
            return best["url"]
        cfg_path.write_text(
            text.rstrip("\n") + "\n\n" + _source_block(best["suggest"], best["url"]) + "\n",
            encoding="utf-8",
        )
        kind = "linkhub" if best["suggest"] == "pan" else best["suggest"]
        log.info("已转正 %s (%s) -> config.toml", best["url"], kind)
        return best["url"]


def _source_block(kind: str, url: str) -> str:
    if kind == "video":
        return f'''[[source]]
name = "hunt-{urlparse(url).netloc}"
type = "video"
url = "{url}"
category = "课程"
enabled = true'''
    if kind == "gallery":
        return f'''[[source]]
name = "hunt-{urlparse(url).netloc}"
type = "gallery"
url = "{url}"
category = "图包"
enabled = true'''
    # pan 候选收录成 linkhub，监控它的列表页收集新分享链接
    return f'''[[source]]
name = "hunt-{urlparse(url).netloc}"
type = "linkhub"
url = "{url}"
category = "_links"
enabled = true'''
