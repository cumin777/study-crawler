"""分享链接死活校验。

各网盘的判活方式（2026-10 实测校准）：
  夸克  POST sharepage/token 接口，code==0 为活，41012 等为死（还带原因）
  百度  GET 分享页，死链命中特定文案（"分享的文件已经被取消"等），活链含"提取码"
  蓝奏  GET 页面，死链文案类同
  阿里  无公开校验接口（API 改版，网页是 SPA 壳）→ 未验证
  磁力/ed2k  无服务端可查 → 未验证
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

log = logging.getLogger("crawler")

BAIDU_DEAD_MARKERS = (
    "分享的文件已经被取消",
    "分享的文件已经被删除",
    "链接不存在",
    "分享已过期",
    "你访问的页面不存在",
    "此链接分享内容因为涉及侵权",
    "链接已失效",
)

# ---------- 校验入口 ----------

# 可校验的网盘（有可靠的死活判断）。阿里的公开接口已没了，暂不可校验。
CHECKABLE_HOSTS = ("pan.baidu.com", "quark.cn", "lanzou")


def is_checkable(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(h in host for h in CHECKABLE_HOSTS)


def check(url: str, fetcher) -> tuple[str, str]:
    """返回 (状态, 说明)。状态: ok / dead / unknown"""
    if url.startswith(("magnet:", "ed2k:")):
        return "unknown", "P2P 链接无服务端可查"
    host = urlparse(url).netloc.lower()
    if "pan.baidu.com" in host:
        return _check_baidu(url, fetcher)
    if "quark.cn" in host:
        return _check_quark(url, fetcher)
    if "lanzou" in host:
        return _check_lanzou(url, fetcher)
    if "alipan.com" in host or "aliyundrive.com" in host:
        return "unknown", "阿里盘无公开校验接口"
    return "unknown", "该网盘暂不支持校验"


def _check_baidu(url: str, fetcher) -> tuple[str, str]:
    try:
        text = fetcher.get_text(url)
    except Exception as exc:
        return "unknown", f"请求失败 {type(exc).__name__}"
    for m in BAIDU_DEAD_MARKERS:
        if m in text:
            return "dead", m
    if "提取码" in text or "share" in text.lower():
        return "ok", ""
    return "unknown", "页面结构不认识"


def _check_quark(url: str, fetcher) -> tuple[str, str]:
    pwd_id = url.rsplit("/", 1)[-1].split("?")[0]
    try:
        data = fetcher.post_json(
            "https://drive-h.quark.cn/1/clouddrive/share/sharepage/token"
            "?pr=ucpro&fr=pc",
            {"pwd_id": pwd_id, "passcode": ""},
            accept=(200, 400, 403, 404),  # 死链走 404+JSON body
        )
    except Exception as exc:
        return "unknown", f"请求失败 {type(exc).__name__}"
    if data.get("code") == 0:
        return "ok", ""
    msg = str(data.get("message") or data.get("code"))
    if "提取码" in msg:
        return "ok", "需提取码"  # 要提取码说明分享还在
    return "dead", msg


def _check_lanzou(url: str, fetcher) -> tuple[str, str]:
    try:
        text = fetcher.get_text(url)
    except Exception as exc:
        return "unknown", f"请求失败 {type(exc).__name__}"
    for m in ("文件取消分享了", "文件不存在", "链接错误", "已经被取消"):
        if m in text:
            return "dead", m
    return "ok", ""


# ---------- 索引行解析/重写 ----------

_LINE_RE = re.compile(r"^- ([\d\- :]+?) \| (.*) \| (.+)$")
_STATUS_RE = re.compile(r"\s*\[(有效[^\]]*|失效[^\]]*|未验证[^\]]*)\]\s*$")
_CODE_RE = re.compile(r"提取码\s+(\S+)")


def parse_line(line: str) -> dict | None:
    """'- 时间 | 标题 | url 提取码 xx [状态]' -> 结构化记录。非索引行返回 None。"""
    m = _LINE_RE.match(line)
    if not m:
        return None
    date, title, rest = m.groups()
    status = None
    m2 = _STATUS_RE.search(rest)
    if m2:
        status = m2.group(1)
        rest = rest[: m2.start()]
    bits = rest.split()
    if not bits:
        return None
    mc = _CODE_RE.search(rest)
    return {
        "date": date.strip(),
        "title": title.strip(),
        "url": bits[0],
        "code": mc.group(1) if mc else "",
        "status": status,
    }


def render_line(rec: dict) -> str:
    code = f"  提取码 {rec['code']}" if rec["code"] else ""
    st = f" [{rec['status']}]" if rec["status"] else ""
    return f"- {rec['date']} | {rec['title']} | {rec['url']}{code}{st}"


def status_label(status: str, note: str) -> str:
    if status == "ok":
        return f"有效: {note}" if note else "有效"
    if status == "dead":
        return f"失效: {note}" if note else "失效"
    return f"未验证: {note}" if note else "未验证"
