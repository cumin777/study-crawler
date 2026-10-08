"""图包引擎：包装 gallery-dl。

gallery-dl 支持上千家图站（pixiv、twitter、各种画廊程序…），
自带分页、去重、断点，比手写爬虫可靠得多。
重复运行是安全的：已下载的会自动跳过。

pip install gallery-dl 后本引擎自动可用。
"""

from __future__ import annotations

import logging
import shutil
import subprocess

from ..storage import category_dir, count_files
from .base import RunSummary

log = logging.getLogger("crawler")


class GalleryEngine:
    def __init__(self, app):
        self.app = app

    def run(self, source) -> RunSummary:
        summary = RunSummary(source.name)
        exe = shutil.which("gallery-dl")
        if not exe:
            summary.errors.append("未安装 gallery-dl，运行: pip install gallery-dl")
            return summary

        dest = category_dir(self.app.cfg.settings, source.category)
        before = count_files(dest)
        proc = subprocess.run(
            [exe, "--destination", str(dest), source.url],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        summary.new = max(0, count_files(dest) - before)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
            summary.errors.append(
                f"gallery-dl 退出码 {proc.returncode}: {' | '.join(tail)}"
            )
        return summary
