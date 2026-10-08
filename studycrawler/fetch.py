"""HTTP 抓取层：重试、限速、统一 UA。全项目唯一的网络出口。"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .storage import filename_from_response, unique_path

log = logging.getLogger("crawler")


class Fetcher:
    def __init__(self, settings):
        self.delay = float(settings.request_delay)
        self.timeout = int(settings.timeout)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = settings.user_agent
        retry = Retry(
            total=3,
            backoff_factor=1.5,  # 1.5s / 3s / 4.5s 递增退避
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD"}),
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self._last_hit: dict[str, float] = {}

    def _polite_wait(self, url: str) -> None:
        """同一站点的两次请求之间至少间隔 self.delay 秒。"""
        try:
            host = url.split("://", 1)[1].split("/", 1)[0]
        except IndexError:
            host = url
        last = self._last_hit.get(host)
        if last is not None:
            wait = self.delay - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def get_text(self, url: str) -> str:
        self._polite_wait(url)
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding  # 中文站点常见
        return resp.text

    def download(self, url: str, dest_dir: Path, fallback_stem: str = "") -> Path:
        """流式下载到 dest_dir；同名且同大小则跳过（视为成功）。

        先写 .part 临时文件，完成后改名，中断不会留半个文件。
        """
        self._polite_wait(url)
        with self.session.get(url, stream=True, timeout=self.timeout) as resp:
            resp.raise_for_status()
            name = filename_from_response(resp, fallback_stem or "file")
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = unique_path(dest_dir / name)
            expected = int(resp.headers.get("Content-Length") or 0)
            if dest.exists() and expected and dest.stat().st_size == expected:
                log.info("已存在，跳过: %s", dest.name)
                return dest
            tmp = dest.with_name(dest.name + ".part")
            try:
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1 << 16):
                        if chunk:
                            f.write(chunk)
                tmp.replace(dest)
            finally:
                if tmp.exists():
                    tmp.unlink()
        log.info("下载完成: %s", dest)
        return dest
