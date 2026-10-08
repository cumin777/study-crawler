"""网盘链接聚合引擎：只收集分享链接 + 提取码，不下载文件。

很多资源站的"下载"其实是跳网盘分享链接。这类站全部抓文件不现实，
所以把新链接整理进 sync_dir/_links/<来源名>.md 的索引里，
攒着慢慢手动转存（标题、提取码都在，复制即用）。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup

from ..storage import category_dir, safe_filename
from .base import RunSummary

log = logging.getLogger("crawler")

# 各家网盘的分享链接特征
SHARE_PATTERNS = [
    re.compile(r"https?://pan\.baidu\.com/s/[\w\-]+", re.I),
    re.compile(r"https?://(?:www\.)?(?:aliyundrive|alipan)\.com/s/\w+", re.I),
    re.compile(r"https?://pan\.quark\.cn/s/\w+", re.I),
    re.compile(r"https?://(?:[\w\-]+\.)*123pan\.com/s/[\w\-]+", re.I),
    re.compile(r"https?://(?:[\w\-]+\.)*lanzou[a-z]{0,2}\.com/\w+", re.I),
    re.compile(r"magnet:\?xt=urn:btih:[A-Za-z0-9]+", re.I),
]

# "提取码: abcd" / "密码：1234" / "pwd ab12"
CODE_RE = re.compile(
    r"(?:提取码|提取碼|密码|密碼|访问码|pwd|code)\s*[：:=\s]\s*([A-Za-z0-9]{4,8})",
    re.I,
)


def extract_links(html: str) -> list[dict]:
    """提取页面里所有分享链接，返回 [{link, title, code}]，按出现顺序去重。"""
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}

    for a in soup.find_all("a", href=True):
        for pat in SHARE_PATTERNS:
            m = pat.search(a["href"])
            if m is None:
                continue
            link = m.group(0)
            if link in found:
                break
            # 提取码常在链接文字或紧邻的父节点文字里
            context = " ".join(
                t for t in (
                    a.get_text(" ", strip=True),
                    a.parent.get_text(" ", strip=True) if a.parent else "",
                ) if t
            )
            cm = CODE_RE.search(context)
            found[link] = {
                "link": link,
                "title": a.get_text(" ", strip=True),
                "code": cm.group(1) if cm else "",
            }
            break

    # 纯文本里的 magnet 链接（经常不在 <a> 标签上）
    for m in SHARE_PATTERNS[-1].finditer(soup.get_text(" ", strip=True)):
        if m.group(0) not in found:
            found[m.group(0)] = {"link": m.group(0), "title": "", "code": ""}

    return list(found.values())


class LinkHubEngine:
    def __init__(self, app):
        self.app = app

    def run(self, source) -> RunSummary:
        summary = RunSummary(source.name)
        html = self.app.fetcher.get_text(source.url)
        page = BeautifulSoup(html, "html.parser")
        page_title = page.title.get_text(strip=True) if page.title else source.name

        records = [
            r for r in extract_links(html)
            if not self.app.db.seen(r["link"], source.name)
        ]
        if not records:
            summary.skipped = 1
            return summary

        index = (
            category_dir(self.app.cfg.settings, source.category)
            / f"{safe_filename(source.name)}.md"
        )
        is_new = not index.exists()
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(index, "a", encoding="utf-8") as f:
            if is_new:
                f.write(f"# {source.name} 收集的分享链接\n\n来源页: {source.url}\n\n")
            for r in records:
                code = f"  提取码 {r['code']}" if r["code"] else ""
                title = r["title"] or page_title
                f.write(f"- {now} | {title} | {r['link']}{code}\n")

        for r in records:
            self.app.db.mark(
                r["link"], source.name, title=r["title"], status="done", path=str(index)
            )
        summary.new = len(records)
        log.info("[%s] 新增 %d 条分享链接 -> %s", source.name, len(records), index)
        return summary
