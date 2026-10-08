"""课程/视频引擎：包装 yt-dlp。

yt-dlp 支持数千个视频站，自带断点续传、分片重试、格式选择。
课程多半是合集（playlist），重复运行时已下载的分 P 会跳过。

pip install yt-dlp 后本引擎自动可用。
"""

from __future__ import annotations

import logging
import shutil
import subprocess

from ..storage import category_dir, count_files
from .base import RunSummary

log = logging.getLogger("crawler")


class VideoEngine:
    def __init__(self, app):
        self.app = app

    def run(self, source) -> RunSummary:
        summary = RunSummary(source.name)
        exe = shutil.which("yt-dlp")
        if not exe:
            summary.errors.append("未安装 yt-dlp，运行: pip install yt-dlp")
            return summary

        dest = category_dir(self.app.cfg.settings, source.category)
        before = count_files(dest)
        proc = subprocess.run(
            [
                exe,
                "-P", str(dest),
                "-o", "%(title)s [%(id)s].%(ext)s",
                "--retries", "10",
                "--fragment-retries", "10",
                source.url,
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        summary.new = max(0, count_files(dest) - before)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
            summary.errors.append(
                f"yt-dlp 退出码 {proc.returncode}: {' | '.join(tail)}"
            )
        return summary
