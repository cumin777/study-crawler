"""论坛/网页引擎：CSS 选择器驱动，抓附件和直链文件。

适合电子书站、资源论坛这类"列表页 -> 帖子页 -> 附件"的结构。
选择器写进 config.toml，不用改代码。

流程：
  1. 抓列表页，按 selectors.item/link 解析出 (标题, 链接)
  2. 链接直接指向文件 -> 下载到 分类目录/
  3. 否则若 follow_detail -> 进详情页，抓所有文件直链，
     下载到 分类目录/帖子标题/ 子文件夹
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..storage import category_dir, safe_filename
from .base import RunSummary

log = logging.getLogger("crawler")

# 认作"可直接下载的文件"的后缀
FILE_EXTS = {
    ".pdf", ".epub", ".mobi", ".azw3", ".djvu", ".txt",
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2",
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv",
    ".mp3", ".flac", ".wav", ".ape",
    ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",
    ".jpg", ".png", ".jpeg", ".webp",
    ".abr", ".psd", ".svg",  # 笔刷/素材包
}


def parse_list(html: str, base_url: str, selectors: dict) -> list[tuple[str, str]]:
    """按选择器解析列表页，返回 (标题, 绝对链接) 列表。"""
    item_sel = selectors.get("item", "")
    link_sel = selectors.get("link", "")
    title_sel = selectors.get("title", "")
    if not (item_sel and link_sel):
        raise ValueError(
            "forum 来源需要配置 selectors.item 和 selectors.link（CSS 选择器）"
        )
    soup = BeautifulSoup(html, "html.parser")
    out: list[tuple[str, str]] = []
    for node in soup.select(item_sel):
        link_node = node.select_one(link_sel)
        if link_node is None or not link_node.get("href"):
            continue
        title_node = node.select_one(title_sel) if title_sel else link_node
        title = (title_node or link_node).get_text(" ", strip=True)
        out.append((title or "untitled", urljoin(base_url, link_node["href"])))
    return out


def file_links_in_page(html: str, base_url: str) -> list[str]:
    """从详情页 HTML 里挑出指向文件后缀的直链，去重保序。"""
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        url = urljoin(base_url, a["href"])
        if Path(urlparse(url).path).suffix.lower() in FILE_EXTS and url not in seen:
            seen.add(url)
            out.append(url)
    return out


class ForumEngine:
    def __init__(self, app):
        self.app = app  # AppContext: cfg / db / fetcher

    def run(self, source) -> RunSummary:
        summary = RunSummary(source.name)
        html = self.app.fetcher.get_text(source.url)
        items = parse_list(html, source.url, source.selectors)
        if not items:
            summary.errors.append("列表页解析出 0 个条目，检查选择器是否失效")
            return summary

        cat_dir = category_dir(self.app.cfg.settings, source.category)
        handled = 0
        for title, url in items:
            if handled >= source.max_items:
                break
            if self.app.db.seen(url, source.name):
                summary.skipped += 1
                continue
            handled += 1
            try:
                ext = Path(urlparse(url).path).suffix.lower()
                if ext in FILE_EXTS:
                    self.app.fetcher.download(url, cat_dir, fallback_stem=title)
                elif source.follow_detail:
                    n = self._scrape_detail(url, title, cat_dir)
                    if n == 0:
                        log.info("[%s] 详情页无附件: %s", source.name, title)
                else:
                    summary.skipped += 1
                self.app.db.mark(url, source.name, title=title, status="done")
                summary.new += 1
            except Exception as exc:
                log.warning("[%s] 抓取失败 %s: %r", source.name, title, exc)
                self.app.db.mark(url, source.name, title=title, status="error")
                summary.errors.append(f"{title}: {exc!r}")
        return summary

    def _scrape_detail(self, url: str, title: str, cat_dir: Path) -> int:
        """进帖子详情页抓附件，放进以帖子标题命名的子文件夹。"""
        html = self.app.fetcher.get_text(url)
        links = file_links_in_page(html, url)
        if not links:
            return 0
        sub = cat_dir / safe_filename(title)
        sub.mkdir(parents=True, exist_ok=True)
        for link in links:
            self.app.fetcher.download(link, sub, fallback_stem=title)
        return len(links)
