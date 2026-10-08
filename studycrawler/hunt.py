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
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from .engines.forum import file_links_in_page
from .engines.linkhub import extract_links
from .storage import category_dir, safe_filename

log = logging.getLogger("crawler")

# 收割门槛：关键词至少命中这么多、总分至少这么多才动手下载
HARVEST_MIN_KW = 1
HARVEST_MIN_SCORE = 8

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

        # 1) 搜索，去重 + 每个域名只留一条
        all_hits: dict[str, dict] = {}
        for name in self.h.engines:
            fn = SEARCHERS.get(name)
            if fn is None:
                log.warning("未知搜索引擎: %s (可选: %s)", name, sorted(SEARCHERS))
                continue
            try:
                got = fn(query, pages, self.app.fetcher)
            except Exception as exc:
                log.warning("[%s] 搜索异常: %r", name, exc)
                continue
            log.info("[%s] 拿到 %d 条结果", name, len(got))
            for h in got:
                domain = urlparse(h["url"]).netloc
                if domain and domain not in all_hits:
                    all_hits[domain] = h
        for eng_cfg in self.h.custom_engines:
            for h in search_custom(eng_cfg, query, pages, self.app.fetcher):
                domain = urlparse(h["url"]).netloc
                if domain and domain not in all_hits:
                    all_hits[domain] = h

        candidates = [h for h in all_hits.values() if not self._blocked(h["url"])][:top]
        log.info("去重+过滤后候选 %d 个站，开始探测", len(candidates))

        # 2) 探测打分
        results = []
        for h in candidates:
            r = self._probe(h, terms)
            if r:
                results.append(r)
        results.sort(key=lambda r: r["score"], reverse=True)

        # 关键词本轮已处理（搜索成功即算，即使没收到东西）；搜索全挂则下轮重试
        if candidates:
            mark_query_done(self.app, query)

        # 3) 收割 + 4) 报告。out 指定时整套结果落在 out 下，否则在 sync_dir/_hunt
        base = Path(out).expanduser().resolve() if out else None
        qdir = safe_filename(query)
        if base:
            cat = base / qdir
            cat.mkdir(parents=True, exist_ok=True)
        else:
            cat = category_dir(self.app.cfg.settings, f"_hunt/{qdir}")
        n_files, n_pans, pan_index = self._harvest(query, results, cat, files_cap)

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
            for url in r["files"]:
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
