"""文件落地：安全文件名、分类目录、去重命名。"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

# Windows 文件名非法字符
_INVALID = {"/", "\\", ":", "*", "?", '"', "<", ">", "|", "\0"}


def safe_filename(name: str, max_len: int = 120) -> str:
    """转成 Windows/网盘都能接受的文件名。"""
    for ch in _INVALID:
        name = name.replace(ch, "_")
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:max_len].rstrip(" .") or "unnamed"


def unique_path(path: Path) -> Path:
    """a.pdf 已存在时返回 a (1).pdf，依次类推。"""
    if not path.exists():
        return path
    for i in range(1, 1000):
        cand = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not cand.exists():
            return cand
    return path


def category_dir(settings, category: str) -> Path:
    """同步目录下的分类子文件夹，如 sync_dir/电子书/。"""
    d = Path(settings.sync_dir) / safe_filename(category)
    d.mkdir(parents=True, exist_ok=True)
    return d


def count_files(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(1 for p in root.rglob("*") if p.is_file())


# Content-Disposition 里的文件名，兼容 filename= 和 filename*=UTF-8''
_CD_NAME = re.compile(
    r"filename\*\s*=\s*UTF-8''([^;]+)|filename\s*=\s*\"?([^\";]+)\"?", re.I
)


def filename_from_response(resp, fallback: str) -> str:
    """从响应头取文件名，取不到就用 URL 末段，再不行用 fallback。"""
    m = _CD_NAME.search(resp.headers.get("Content-Disposition", ""))
    if m:
        raw = (m.group(1) or m.group(2) or "").strip().strip('"')
        if raw:
            return safe_filename(unquote(raw))
    tail = str(resp.url).split("/")[-1].split("?")[0]
    return safe_filename(unquote(tail)) or safe_filename(fallback)
